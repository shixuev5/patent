"""
用于从专利分析产物初始化 AI 检索会话的辅助函数。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.ai_search.src.subagents.search_elements import normalize_date_text, normalize_search_elements_payload


def load_json_file(path_value: Any) -> Optional[Dict[str, Any]]:
    path_text = str(path_value or "").strip()
    if not path_text:
        return None
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def load_json_bytes(raw: Optional[bytes]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _string_list(values: Any) -> List[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    outputs: List[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in outputs:
            outputs.append(text)
    return outputs


def _applicant_names(biblio: Dict[str, Any]) -> List[str]:
    applicants = biblio.get("applicants")
    if isinstance(applicants, str):
        applicants = [applicants]
    if not isinstance(applicants, list):
        return []
    outputs: List[str] = []
    for item in applicants:
        if isinstance(item, dict):
            text = str(item.get("name") or "").strip()
        else:
            text = str(item or "").strip()
        if text and text not in outputs:
            outputs.append(text)
    return outputs


def seed_search_elements_from_analysis(analysis_payload: Dict[str, Any], patent_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    patent_data = patent_payload if isinstance(patent_payload, dict) else {}
    biblio = patent_data.get("bibliographic_data") if isinstance(patent_data.get("bibliographic_data"), dict) else {}
    search_strategy = analysis_payload.get("search_strategy") if isinstance(analysis_payload.get("search_strategy"), dict) else {}
    search_matrix = search_strategy.get("search_matrix") if isinstance(search_strategy.get("search_matrix"), list) else []
    report_core = analysis_payload.get("report_core") if isinstance(analysis_payload.get("report_core"), dict) else {}
    metadata = analysis_payload.get("metadata") if isinstance(analysis_payload.get("metadata"), dict) else {}

    invention_title = str(
        report_core.get("ai_title")
        or biblio.get("invention_title")
        or metadata.get("resolved_pn")
        or "当前专利"
    ).strip()
    resolved_pn = str(metadata.get("resolved_pn") or biblio.get("publication_number") or "").strip()
    objective = (
        f"围绕专利 {resolved_pn}《{invention_title}》检索可能构成对比文件的现有技术。"
        if resolved_pn
        else f"围绕《{invention_title}》检索可能构成对比文件的现有技术。"
    )

    mapped_elements: List[Dict[str, Any]] = []
    for item in search_matrix:
        if not isinstance(item, dict):
            continue
        element_name = str(item.get("element_name") or "").strip()
        if not element_name:
            continue
        notes_parts = _string_list(item.get("notes"))
        if not notes_parts and str(item.get("notes") or "").strip():
            notes_parts = [str(item.get("notes") or "").strip()]
        if str(item.get("block_id") or "").strip():
            notes_parts.append(f"block={str(item.get('block_id') or '').strip()}")
        if str(item.get("element_role") or "").strip():
            notes_parts.append(f"role={str(item.get('element_role') or '').strip()}")
        if str(item.get("priority_tier") or "").strip():
            notes_parts.append(f"priority={str(item.get('priority_tier') or '').strip()}")
        effect_cluster_ids = _string_list(item.get("effect_cluster_ids"))
        if effect_cluster_ids:
            notes_parts.append(f"effects={','.join(effect_cluster_ids)}")
        mapped_elements.append(
            {
                "element_name": element_name,
                "keywords_zh": _string_list(item.get("keywords_zh")),
                "keywords_en": _string_list(item.get("keywords_en")),
                "block_id": str(item.get("block_id") or "").strip(),
                "element_role": str(item.get("element_role") or "").strip(),
                "priority_tier": str(item.get("priority_tier") or "").strip(),
                "effect_cluster_ids": effect_cluster_ids,
                "notes": "；".join(part for part in notes_parts if part),
            }
        )

    return normalize_search_elements_payload(
        {
            "status": "complete" if objective and mapped_elements else "needs_answer",
            "objective": objective,
            "applicants": _applicant_names(biblio),
            "filing_date": normalize_date_text(biblio.get("application_date") or biblio.get("filing_date")),
            "priority_date": normalize_date_text(biblio.get("priority_date")),
            "search_elements": mapped_elements,
            "missing_items": [],
            "clarification_summary": "已从 AI 分析结果导入首轮检索要素。",
        }
    )


def seed_prompt_from_analysis(
    analysis_payload: Dict[str, Any],
    patent_payload: Optional[Dict[str, Any]],
    seeded_search_elements: Dict[str, Any],
    *,
    search_mode: str,
) -> str:
    patent_data = patent_payload if isinstance(patent_payload, dict) else {}
    biblio = patent_data.get("bibliographic_data") if isinstance(patent_data.get("bibliographic_data"), dict) else {}
    search_strategy = analysis_payload.get("search_strategy") if isinstance(analysis_payload.get("search_strategy"), dict) else {}
    semantic_strategy = search_strategy.get("semantic_strategy") if isinstance(search_strategy.get("semantic_strategy"), dict) else {}
    report_core = analysis_payload.get("report_core") if isinstance(analysis_payload.get("report_core"), dict) else {}
    metadata = analysis_payload.get("metadata") if isinstance(analysis_payload.get("metadata"), dict) else {}
    semantic_queries = semantic_strategy.get("queries") if isinstance(semantic_strategy.get("queries"), list) else []
    report = analysis_payload.get("report") if isinstance(analysis_payload.get("report"), dict) else {}

    seed_context = {
        "source": {
            "type": "analysis",
            "analysis_task_id": str(metadata.get("task_id") or "").strip(),
            "publication_number": str(metadata.get("resolved_pn") or biblio.get("publication_number") or "").strip(),
            "title": str(report_core.get("ai_title") or biblio.get("invention_title") or "").strip(),
        },
        "goal": "基于 AI 分析结果生成 AI 检索草稿。如果信息已足够，直接生成待确认检索计划；如果仍缺关键信息，只追问缺失项。不要开始真实检索执行。",
        "search_mode": search_mode,
        "analysis_summary": {
            "technical_problem": str(report_core.get("technical_problem") or report.get("technical_problem") or "").strip(),
            "technical_means": str(report_core.get("technical_means") or report.get("technical_means") or "").strip(),
            "technical_effects": report_core.get("technical_effects") if isinstance(report_core.get("technical_effects"), list) else [],
        },
        "seeded_search_elements": seeded_search_elements,
        "semantic_queries": semantic_queries,
    }
    return (
        "请根据以下 AI 分析结果生成一份 AI 检索草稿。"
        "要求：先整理检索要素，再决定是直接产出待确认计划，还是仅追问缺失项；不要启动真实检索。"
        "严格遵守给定的 search_mode；若是 topic_search，不要进入 claim decomposition。\n\n"
        f"{json.dumps(seed_context, ensure_ascii=False, indent=2)}"
    )
