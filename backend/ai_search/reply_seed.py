"""Helpers for initializing AI search sessions from AI-reply follow-up search artifacts."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from agents.ai_search.src.search_elements import normalize_search_elements_payload


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


def build_execution_spec_from_reply(
    reply_payload: Dict[str, Any],
    seeded_search_elements: Dict[str, Any],
) -> Dict[str, Any]:
    notice_context = reply_payload.get("notice_context") if isinstance(reply_payload.get("notice_context"), dict) else {}
    constraints = reply_payload.get("search_followup_section", {}).get("suggested_constraints") if isinstance(reply_payload.get("search_followup_section"), dict) else {}
    return {
        "search_scope": {
            "objective": str(seeded_search_elements.get("objective") or "").strip(),
            "applicants": seeded_search_elements.get("applicants") if isinstance(seeded_search_elements.get("applicants"), list) else [],
            "filing_date": seeded_search_elements.get("filing_date"),
            "priority_date": seeded_search_elements.get("priority_date"),
            "languages": ["zh", "en"],
            "databases": ["zhihuiya"],
            "excluded_items": [],
            "source": {
                "reply_task_id": str(reply_payload.get("task_id") or "").strip(),
                "title": str(reply_payload.get("title") or "").strip(),
                "publication_number": str(reply_payload.get("pn") or "").strip(),
                "notice_round": int(notice_context.get("current_notice_round") or 0),
            },
        },
        "constraints": {
            "comparison_document_ids": _string_list(constraints.get("comparison_document_ids")),
            "notes": _string_list(constraints.get("notes")),
        },
        "execution_policy": {
            "dynamic_replanning": True,
            "planner_visibility": "summary_only",
            "max_rounds": 2,
            "max_no_progress_rounds": 1,
            "max_selected_documents": 5,
            "decision_on_exhaustion": True,
        },
        "sub_plans": [
            {
                "sub_plan_id": "sub_plan_1",
                "title": "答复补检计划",
                "goal": str(seeded_search_elements.get("objective") or "").strip() or "围绕答复报告补检要素生成检索计划",
                "semantic_query_text": "",
                "search_elements": seeded_search_elements.get("search_elements") if isinstance(seeded_search_elements.get("search_elements"), list) else [],
                "retrieval_steps": [],
                "query_blueprints": [],
                "classification_hints": [],
            }
        ],
    }


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
            "以下是从 AI 答复报告带入的补检上下文，请基于这些信息生成一份可审核的检索计划。",
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
            "请基于以上信息生成一份可审核的检索计划。",
        ]
    )


def seed_prompt_from_reply(
    reply_payload: Dict[str, Any],
    seeded_search_elements: Dict[str, Any],
) -> str:
    execution_spec = build_execution_spec_from_reply(reply_payload, seeded_search_elements)
    payload = {
        "goal": "基于 AI 答复报告中的补检/检索建议生成 AI 检索计划。如果信息足够，直接产出待确认计划；如果仍缺关键信息，只追问缺失项。不要开始真实检索执行。",
        "seeded_search_elements": seeded_search_elements,
        "execution_spec_seed": execution_spec,
        "user_context_markdown": build_reply_seed_user_message(reply_payload, seeded_search_elements),
    }
    return (
        "请根据以下上下文生成一份 AI 检索计划。"
        "要求：优先围绕答复报告中的补检目标与检索要素组织计划；不要输出顶部摘要，不要启动真实检索。\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
