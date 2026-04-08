"""
Helpers for initializing AI search sessions from patent-analysis artifacts.
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
        text = str(item.get("name") or "").strip() if isinstance(item, dict) else str(item or "").strip()
        if text and text not in outputs:
            outputs.append(text)
    return outputs


def _resolved_title(report_core: Dict[str, Any], biblio: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    return str(
        report_core.get("ai_title")
        or biblio.get("invention_title")
        or metadata.get("resolved_pn")
        or "当前专利"
    ).strip()


def _resolved_pn(metadata: Dict[str, Any], biblio: Dict[str, Any]) -> str:
    return str(metadata.get("resolved_pn") or biblio.get("publication_number") or "").strip()


def _search_matrix_items(analysis_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    search_strategy = analysis_payload.get("search_strategy") if isinstance(analysis_payload.get("search_strategy"), dict) else {}
    search_matrix = search_strategy.get("search_matrix") if isinstance(search_strategy.get("search_matrix"), list) else []
    return [item for item in search_matrix if isinstance(item, dict)]


def _semantic_queries(analysis_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    search_strategy = analysis_payload.get("search_strategy") if isinstance(analysis_payload.get("search_strategy"), dict) else {}
    semantic_strategy = search_strategy.get("semantic_strategy") if isinstance(search_strategy.get("semantic_strategy"), dict) else {}
    queries = semantic_strategy.get("queries") if isinstance(semantic_strategy.get("queries"), list) else []
    return [item for item in queries if isinstance(item, dict)]


def _mapped_element(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    element_name = str(item.get("element_name") or "").strip()
    if not element_name:
        return None
    notes_parts = _string_list(item.get("notes"))
    if not notes_parts and str(item.get("notes") or "").strip():
        notes_parts = [str(item.get("notes") or "").strip()]
    return {
        "element_name": element_name,
        "keywords_zh": _string_list(item.get("keywords_zh")),
        "keywords_en": _string_list(item.get("keywords_en")),
        "block_id": str(item.get("block_id") or "").strip().upper(),
        "notes": "；".join(part for part in notes_parts if part),
    }


def seed_search_elements_from_analysis(analysis_payload: Dict[str, Any], patent_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    patent_data = patent_payload if isinstance(patent_payload, dict) else {}
    biblio = patent_data.get("bibliographic_data") if isinstance(patent_data.get("bibliographic_data"), dict) else {}
    report_core = analysis_payload.get("report_core") if isinstance(analysis_payload.get("report_core"), dict) else {}
    metadata = analysis_payload.get("metadata") if isinstance(analysis_payload.get("metadata"), dict) else {}

    invention_title = _resolved_title(report_core, biblio, metadata)
    resolved_pn = _resolved_pn(metadata, biblio)
    objective = (
        f"围绕专利 {resolved_pn}《{invention_title}》检索可能构成对比文件的现有技术。"
        if resolved_pn
        else f"围绕《{invention_title}》检索可能构成对比文件的现有技术。"
    )

    mapped_elements = [mapped for mapped in (_mapped_element(item) for item in _search_matrix_items(analysis_payload)) if mapped]
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


def build_analysis_sub_plans(analysis_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    search_matrix = _search_matrix_items(analysis_payload)
    semantic_queries = _semantic_queries(analysis_payload)
    global_a = [item for item in search_matrix if str(item.get("block_id") or "").strip().upper() == "A"]
    global_c = [
        item
        for item in search_matrix
        if str(item.get("block_id") or "").strip().upper() == "C" and not _string_list(item.get("effect_cluster_ids"))
    ]
    sub_plans: List[Dict[str, Any]] = []
    if semantic_queries:
        for index, query in enumerate(semantic_queries, start=1):
            block_id = str(query.get("block_id") or f"B{index}").strip().upper() or f"B{index}"
            effect_cluster_ids = set(_string_list(query.get("effect_cluster_ids")))
            query_elements: List[Dict[str, Any]] = []
            for item in global_a:
                mapped = _mapped_element(item)
                if mapped:
                    query_elements.append(mapped)
            for item in search_matrix:
                item_block = str(item.get("block_id") or "").strip().upper()
                if item_block == block_id:
                    mapped = _mapped_element(item)
                    if mapped:
                        query_elements.append(mapped)
                elif item_block == "C":
                    item_effects = set(_string_list(item.get("effect_cluster_ids")))
                    if not item_effects or (effect_cluster_ids and item_effects.intersection(effect_cluster_ids)):
                        mapped = _mapped_element(item)
                        if mapped:
                            query_elements.append(mapped)
            for item in global_c:
                mapped = _mapped_element(item)
                if mapped and mapped not in query_elements:
                    query_elements.append(mapped)
            goal = str(query.get("effect") or query.get("goal") or query.get("content") or f"核心效果 {index}").strip() or f"核心效果 {index}"
            sub_plans.append(
                {
                    "sub_plan_id": f"sub_plan_{index}",
                    "title": goal,
                    "goal": goal,
                    "semantic_query_text": str(query.get("content") or "").strip(),
                    "search_elements": query_elements,
                    "retrieval_steps": [
                        {
                            "step_id": f"sub_plan_{index}_step_1",
                            "title": f"{goal} / 首轮宽召回",
                            "purpose": f"围绕{goal}执行首轮宽召回，验证语言与特征组合是否有效。",
                            "feature_combination": "基于当前子计划检索要素组合",
                            "language_strategy": "中文优先，必要时补英文同义表达",
                            "ipc_cpc_mode": "按需补充 IPC/CPC",
                            "ipc_cpc_codes": [],
                            "expected_recall": "首轮召回可用于判断噪声与方向性的候选文献",
                            "fallback_action": "若结果过宽则补限定特征或分类号；若过窄则放宽同义词与语言组合",
                            "query_blueprint_refs": [f"sub_plan_{index}_batch_1"],
                            "phase_key": "execute_search",
                        }
                    ],
                    "query_blueprints": [
                        {
                            "batch_id": f"sub_plan_{index}_batch_1",
                            "goal": goal,
                            "semantic_text": str(query.get("content") or "").strip(),
                            "sub_plan_id": f"sub_plan_{index}",
                            "effect_cluster_ids": list(effect_cluster_ids),
                        }
                    ],
                    "classification_hints": [],
                }
            )
    else:
        all_elements = [mapped for mapped in (_mapped_element(item) for item in search_matrix) if mapped]
        if all_elements:
            sub_plans.append(
                {
                    "sub_plan_id": "sub_plan_1",
                    "title": "子计划 1",
                    "goal": "执行首轮检索",
                    "semantic_query_text": "",
                    "search_elements": all_elements,
                    "retrieval_steps": [
                        {
                            "step_id": "sub_plan_1_step_1",
                            "title": "子计划 1 / 首轮宽召回",
                            "purpose": "围绕当前检索要素执行首轮宽召回，确认有效语言与噪声水平。",
                            "feature_combination": "基于全部已识别检索要素组合",
                            "language_strategy": "中文优先，必要时补英文",
                            "ipc_cpc_mode": "先不使用 IPC/CPC，必要时再补",
                            "ipc_cpc_codes": [],
                            "expected_recall": "拿到可审查的首轮候选池",
                            "fallback_action": "结果过宽时补限定特征；结果过窄时扩词或切换语言策略",
                            "query_blueprint_refs": ["sub_plan_1_batch_1"],
                            "phase_key": "execute_search",
                        }
                    ],
                    "query_blueprints": [
                        {
                            "batch_id": "sub_plan_1_batch_1",
                            "goal": "执行首轮检索",
                            "sub_plan_id": "sub_plan_1",
                        }
                    ],
                    "classification_hints": [],
                }
            )
    return sub_plans


def build_execution_spec_from_analysis(
    analysis_payload: Dict[str, Any],
    patent_payload: Optional[Dict[str, Any]],
    seeded_search_elements: Dict[str, Any],
) -> Dict[str, Any]:
    patent_data = patent_payload if isinstance(patent_payload, dict) else {}
    biblio = patent_data.get("bibliographic_data") if isinstance(patent_data.get("bibliographic_data"), dict) else {}
    report_core = analysis_payload.get("report_core") if isinstance(analysis_payload.get("report_core"), dict) else {}
    metadata = analysis_payload.get("metadata") if isinstance(analysis_payload.get("metadata"), dict) else {}
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
                "analysis_task_id": str(metadata.get("task_id") or "").strip(),
                "publication_number": _resolved_pn(metadata, biblio),
                "title": _resolved_title(report_core, biblio, metadata),
            },
        },
        "constraints": {},
        "execution_policy": {
            "dynamic_replanning": True,
            "planner_visibility": "summary_only",
            "max_rounds": 3,
            "max_no_progress_rounds": 2,
            "max_selected_documents": 5,
            "decision_on_exhaustion": True,
        },
        "sub_plans": build_analysis_sub_plans(analysis_payload),
    }


def build_analysis_seed_user_message(
    analysis_payload: Dict[str, Any],
    patent_payload: Optional[Dict[str, Any]],
    seeded_search_elements: Dict[str, Any],
) -> str:
    patent_data = patent_payload if isinstance(patent_payload, dict) else {}
    biblio = patent_data.get("bibliographic_data") if isinstance(patent_data.get("bibliographic_data"), dict) else {}
    report_core = analysis_payload.get("report_core") if isinstance(analysis_payload.get("report_core"), dict) else {}
    report = analysis_payload.get("report") if isinstance(analysis_payload.get("report"), dict) else {}
    metadata = analysis_payload.get("metadata") if isinstance(analysis_payload.get("metadata"), dict) else {}
    technical_effects = report_core.get("technical_effects") if isinstance(report_core.get("technical_effects"), list) else []
    if not technical_effects:
        technical_effects = report.get("technical_effects") if isinstance(report.get("technical_effects"), list) else []
    sub_plans = build_analysis_sub_plans(analysis_payload)
    plan_lines: List[str] = []
    for item in sub_plans:
        title = str(item.get("title") or item.get("goal") or item.get("sub_plan_id") or "").strip()
        semantic_text = str(item.get("semantic_query_text") or "").strip()
        plan_lines.append(f"- {title}")
        if semantic_text:
            plan_lines.append(f"  语义检索文本：{semantic_text}")
    element_lines = []
    for item in seeded_search_elements.get("search_elements") or []:
        if not isinstance(item, dict):
            continue
        block_id = str(item.get("block_id") or "").strip().upper()
        label = f"[Block {block_id}] " if block_id else ""
        element_lines.append(f"- {label}{str(item.get('element_name') or '').strip()}")
    effect_lines = [f"- {str(item or '').strip()}" for item in technical_effects if str(item or "").strip()]
    return "\n".join(
        [
            "以下是从 AI 分析带入的检索上下文，请基于这些信息生成一份可审核的检索计划。",
            "",
            "## 来源",
            f"- AI 分析任务：{str(metadata.get('task_id') or '').strip() or '-'}",
            f"- 专利号：{_resolved_pn(metadata, biblio) or '-'}",
            f"- 标题：{_resolved_title(report_core, biblio, metadata) or '-'}",
            "",
            "## 本轮检索目标",
            f"- {str(seeded_search_elements.get('objective') or '').strip() or '-'}",
            "",
            "## 核心创新点",
            f"- 技术问题：{str(report_core.get('technical_problem') or report.get('technical_problem') or '').strip() or '-'}",
            f"- 技术手段：{str(report_core.get('technical_means') or report.get('technical_means') or '').strip() or '-'}",
            "",
            "## 核心效果",
            *(effect_lines or ["- -"]),
            "",
            "## 可用检索要素",
            *(element_lines or ["- -"]),
            "",
            "## 已识别子计划",
            *(plan_lines or ["- 子计划 1"]),
            "",
            "## 已知边界",
            f"- 申请人：{'、'.join(seeded_search_elements.get('applicants') or []) or '-'}",
            f"- 申请日：{seeded_search_elements.get('filing_date') or '-'}",
            f"- 优先权日：{seeded_search_elements.get('priority_date') or '-'}",
            "",
            "请基于以上信息生成一份可审核的检索计划。",
        ]
    )


def seed_prompt_from_analysis(
    analysis_payload: Dict[str, Any],
    patent_payload: Optional[Dict[str, Any]],
    seeded_search_elements: Dict[str, Any],
) -> str:
    execution_spec = build_execution_spec_from_analysis(analysis_payload, patent_payload, seeded_search_elements)
    payload = {
        "goal": "基于 AI 分析结果生成 AI 检索计划。如果信息足够，直接产出待确认计划；如果仍缺关键信息，只追问缺失项。不要开始真实检索执行。",
        "seeded_search_elements": seeded_search_elements,
        "execution_spec_seed": execution_spec,
        "user_context_markdown": build_analysis_seed_user_message(analysis_payload, patent_payload, seeded_search_elements),
    }
    return (
        "请根据以下上下文生成一份 AI 检索计划。"
        "要求：输出单条可审核计划，允许包含多个子计划；不要输出顶部摘要，不要启动真实检索。\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
