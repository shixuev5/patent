"""Helpers for initializing AI search sessions from AI-reply follow-up search artifacts."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from patent_agents.ai_search.src.search_elements import normalize_search_elements_payload


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(values: Any) -> List[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    outputs: List[str] = []
    for item in values:
        text = _safe_text(item)
        if text and text not in outputs:
            outputs.append(text)
    return outputs


def seed_search_elements_from_reply(reply_payload: Dict[str, Any]) -> Dict[str, Any]:
    section = reply_payload.get("search_followup_section") if isinstance(reply_payload.get("search_followup_section"), dict) else {}
    constraints = section.get("suggested_constraints") if isinstance(section.get("suggested_constraints"), dict) else {}
    normalized = normalize_search_elements_payload(
        {
            "status": section.get("status"),
            "objective": _safe_text(section.get("objective")),
            "applicants": section.get("applicants") or constraints.get("applicants") or [],
            "filing_date": section.get("filing_date") or constraints.get("filing_date"),
            "priority_date": section.get("priority_date") or constraints.get("priority_date"),
            "missing_items": section.get("missing_items") or [],
            "search_elements": section.get("search_elements") or [],
        }
    )
    normalized["trigger_reasons"] = _string_list(section.get("trigger_reasons"))
    normalized["gap_summaries"] = section.get("gap_summaries") if isinstance(section.get("gap_summaries"), list) else []
    normalized["comparison_document_ids"] = _string_list(constraints.get("comparison_document_ids"))
    normalized["constraint_notes"] = _string_list(constraints.get("notes"))
    normalized["source_dispute_ids"] = _string_list(section.get("source_dispute_ids"))
    normalized["source_feature_ids"] = _string_list(section.get("source_feature_ids"))
    return normalized


def build_reply_seed_user_message(
    reply_payload: Dict[str, Any],
    seeded_search_elements: Dict[str, Any],
) -> str:
    section = reply_payload.get("search_followup_section") if isinstance(reply_payload.get("search_followup_section"), dict) else {}
    notice_context = reply_payload.get("notice_context") if isinstance(reply_payload.get("notice_context"), dict) else {}
    title = str(reply_payload.get("title") or "").strip() or "当前答复报告"
    publication_number = str(reply_payload.get("pn") or "").strip()
    element_lines = []
    for item in seeded_search_elements.get("search_elements") or []:
        if not isinstance(item, dict):
            continue
        block_id = str(item.get("block_id") or "").strip().upper()
        label = f"[Block {block_id}] " if block_id else ""
        element_lines.append(f"- {label}{str(item.get('element_name') or '').strip()}")
    gap_lines: List[str] = []
    for item in section.get("gap_summaries") or []:
        if not isinstance(item, dict):
            continue
        claim_ids = "、".join(_string_list(item.get("claim_ids"))) or "-"
        feature_text = _safe_text(item.get("feature_text")) or "-"
        gap_summary = _safe_text(item.get("gap_summary")) or "-"
        gap_lines.append(f"- 权利要求 {claim_ids}：{feature_text}。{gap_summary}")
    return "\n".join(
        [
            "以下是从 AI 答复报告带入的补检上下文，请基于这些信息直接开展自由检索。",
            "",
            "## 来源",
            f"- AI 答复任务：{str(reply_payload.get('task_id') or '').strip() or '-'}",
            f"- 标题：{title}",
            f"- 专利号：{publication_number or '-'}",
            f"- 审查轮次：{int(notice_context.get('current_notice_round') or 0) or '-'}",
            "",
            "## 本轮补检目标",
            f"- {str(seeded_search_elements.get('objective') or '').strip() or '-'}",
            "",
            "## 触发原因",
            *([f"- {item}" for item in _string_list(section.get("trigger_reasons"))] or ["- -"]),
            "",
            "## 缺口摘要",
            *(gap_lines or ["- -"]),
            "",
            "## 可用检索要素",
            *(element_lines or ["- -"]),
            "",
            "## 已知边界",
            f"- 申请人：{'、'.join(seeded_search_elements.get('applicants') or []) or '-'}",
            f"- 申请日：{seeded_search_elements.get('filing_date') or '-'}",
            f"- 优先权日：{seeded_search_elements.get('priority_date') or '-'}",
            f"- 已有对比文件：{'、'.join(seeded_search_elements.get('comparison_document_ids') or []) or '-'}",
            *([f"- {item}" for item in _string_list(seeded_search_elements.get("constraint_notes"))] or []),
            "",
            "请基于以上信息确认检索目标；如果信息足够，请直接开始检索并保存候选结果。",
        ]
    )


def seed_prompt_from_reply(
    reply_payload: Dict[str, Any],
    seeded_search_elements: Dict[str, Any],
) -> str:
    return (
        "请根据以下上下文直接开展 AI 检索。"
        "要求：用自由检索方式优先围绕答复报告中的补检目标与检索要素推进；不再生成待确认计划，信息足够时直接检索并保存候选。"
        "以下 JSON 是唯一的种子上下文，请不要再寻找或复述其他 seed 文本。\n\n"
        f"{json.dumps(seeded_search_elements, ensure_ascii=False, indent=2)}"
    )
