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


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _effect_cluster_ids(item: Dict[str, Any]) -> List[str]:
    cluster_ids: List[str] = []
    for value in _string_list(item.get("effect_cluster_ids")):
        cluster_id = value.upper()
        if cluster_id and cluster_id not in cluster_ids:
            cluster_ids.append(cluster_id)
    return cluster_ids


def _mapped_element(item: Dict[str, Any], *, block_id_override: Optional[str] = None, notes_suffix: str = "") -> Optional[Dict[str, Any]]:
    element_name = str(item.get("element_name") or "").strip()
    if not element_name:
        return None
    notes_parts = _string_list(item.get("notes"))
    if not notes_parts and str(item.get("notes") or "").strip():
        notes_parts = [str(item.get("notes") or "").strip()]
    if notes_suffix:
        notes_parts.append(notes_suffix)
    return {
        "element_name": element_name,
        "keywords_zh": _string_list(item.get("keywords_zh")),
        "keywords_en": _string_list(item.get("keywords_en")),
        "ipc_cpc_ref": _string_list(item.get("ipc_cpc_ref")),
        "block_id": str(block_id_override or item.get("block_id") or "").strip().upper(),
        "notes": "；".join(part for part in notes_parts if part),
    }


def _technical_effect_items(analysis_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    report_core = analysis_payload.get("report_core") if isinstance(analysis_payload.get("report_core"), dict) else {}
    report = analysis_payload.get("report") if isinstance(analysis_payload.get("report"), dict) else {}
    effects = report_core.get("technical_effects") if isinstance(report_core.get("technical_effects"), list) else []
    if not effects:
        effects = report.get("technical_effects") if isinstance(report.get("technical_effects"), list) else []
    outputs: List[Dict[str, Any]] = []
    for index, item in enumerate(effects, start=1):
        if not isinstance(item, dict):
            continue
        effect_text = _safe_text(item.get("effect")) or f"技术效果 {index}"
        outputs.append(
            {
                "effect_index": index,
                "effect_text": effect_text,
                "score": _safe_int(item.get("tcs_score"), default=0),
                "contributing_features": _string_list(item.get("contributing_features")),
                "dependent_on": _string_list(item.get("dependent_on")),
                "evidence": _safe_text(item.get("evidence")),
                "rationale": _safe_text(item.get("rationale")),
            }
        )
    return outputs


def _dedupe_elements(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in elements:
        if not isinstance(item, dict):
            continue
        key = (_safe_text(item.get("block_id")).upper(), _safe_text(item.get("element_name")))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _elements_for_blocks(elements: List[Dict[str, Any]], *block_prefixes: str) -> List[Dict[str, Any]]:
    prefixes = tuple(str(value or "").strip().upper() for value in block_prefixes if str(value or "").strip())
    if not prefixes:
        return []
    return [
        item
        for item in elements
        if isinstance(item, dict) and str(item.get("block_id") or "").strip().upper().startswith(prefixes)
    ]


def _query_terms_from_elements(elements: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    must_terms_zh: List[str] = []
    must_terms_en: List[str] = []
    ipc_cpc_codes: List[str] = []
    for item in elements:
        if not isinstance(item, dict):
            continue
        element_name = _safe_text(item.get("element_name"))
        zh_terms = _string_list(item.get("keywords_zh"))
        en_terms = _string_list(item.get("keywords_en"))
        ipc_terms = _string_list(item.get("ipc_cpc_ref"))
        if not zh_terms and element_name:
            zh_terms = [element_name]
        for value in zh_terms:
            if value not in must_terms_zh:
                must_terms_zh.append(value)
        for value in en_terms:
            if value not in must_terms_en:
                must_terms_en.append(value)
        for value in ipc_terms:
            if value not in ipc_cpc_codes:
                ipc_cpc_codes.append(value)
    return {
        "must_terms_zh": must_terms_zh,
        "must_terms_en": must_terms_en,
        "ipc_cpc_codes": ipc_cpc_codes,
    }


def _merge_terms(*groups: List[str]) -> List[str]:
    outputs: List[str] = []
    for group in groups:
        for value in group or []:
            text = _safe_text(value)
            if text and text not in outputs:
                outputs.append(text)
    return outputs


def _block_c_semantic_text(goal: str, block_c_elements: List[Dict[str, Any]], semantic_query_text: str) -> str:
    block_c_labels = "、".join(_safe_text(item.get("element_name")) for item in block_c_elements if _safe_text(item.get("element_name")))
    parts = [
        f"围绕“{goal}”执行 Block C 条件分支检索。",
        semantic_query_text,
        f"重点加入 Block C 限定特征：{block_c_labels}。" if block_c_labels else "",
        "用于在主检索命中 Block B 或结果过宽时，执行从权防守检索或进一步降噪。",
    ]
    return " ".join(part for part in parts if part).strip()


def _render_elements_table(elements: List[Dict[str, Any]]) -> List[str]:
    if not elements:
        return ["> 未识别到可展示的检索要素。"]
    lines = [
        "| 逻辑块 | 检索要素 | 中文扩展 | 英文扩展 | 分类号 (IPC/CPC) |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    for item in elements:
        block_id = _safe_text(item.get("block_id")).upper() or "-"
        zh_terms = "；".join(_string_list(item.get("keywords_zh"))) or "-"
        en_terms = "；".join(_string_list(item.get("keywords_en"))) or "-"
        ipc_terms = "；".join(_string_list(item.get("ipc_cpc_ref"))) or "-"
        lines.append(
            f"| Block {block_id} | {_safe_text(item.get('element_name')) or '-'} | {zh_terms} | {en_terms} | {ipc_terms} |"
        )
    return lines


def _collect_main_plan_elements(
    search_matrix: List[Dict[str, Any]],
    *,
    block_id: str,
    effect_cluster_ids: List[str],
) -> List[Dict[str, Any]]:
    effect_cluster_set = {value.upper() for value in effect_cluster_ids if value}
    outputs: List[Dict[str, Any]] = []
    for item in search_matrix:
        if not isinstance(item, dict):
            continue
        item_block = _safe_text(item.get("block_id")).upper()
        item_effects = set(_effect_cluster_ids(item))
        include = False
        if item_block == "A" or item_block == block_id:
            include = True
        elif item_block in {"C", "E"} and (not item_effects or not effect_cluster_set or item_effects.intersection(effect_cluster_set)):
            include = True
        if include:
            mapped = _mapped_element(item)
            if mapped:
                outputs.append(mapped)
    return _dedupe_elements(outputs)


def _effect_plan_groups(analysis_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    search_matrix = _search_matrix_items(analysis_payload)
    semantic_queries = _semantic_queries(analysis_payload)
    groups: List[Dict[str, Any]] = []
    for index, query in enumerate(semantic_queries, start=1):
        block_id = _safe_text(query.get("block_id") or f"B{index}").upper() or f"B{index}"
        effect_cluster_ids = _effect_cluster_ids(query) or [f"E{index}"]
        effect_text = _safe_text(query.get("effect") or query.get("goal") or query.get("content") or f"核心效果 {index}") or f"核心效果 {index}"
        main_elements = _collect_main_plan_elements(
            search_matrix,
            block_id=block_id,
            effect_cluster_ids=effect_cluster_ids,
        )
        groups.append(
            {
                "index": index,
                "block_id": block_id,
                "effect_cluster_ids": effect_cluster_ids,
                "effect_text": effect_text,
                "semantic_query_text": _safe_text(query.get("content")),
                "main_elements": main_elements,
            }
        )
    return groups


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
    sub_plans: List[Dict[str, Any]] = []
    effect_plan_groups = _effect_plan_groups(analysis_payload)
    if effect_plan_groups:
        for group in effect_plan_groups:
            index = int(group.get("index") or len(sub_plans) + 1)
            goal = _safe_text(group.get("effect_text")) or f"核心效果 {index}"
            sub_plan_id = f"sub_plan_{index}"
            main_batch_id = f"{sub_plan_id}_batch_1"
            main_elements = list(group.get("main_elements") or [])
            block_a_elements = _elements_for_blocks(main_elements, "A")
            block_b_elements = _elements_for_blocks(main_elements, _safe_text(group.get("block_id")) or "B")
            block_c_elements = _elements_for_blocks(main_elements, "C")
            effect_elements = _elements_for_blocks(main_elements, "E")
            main_batch_elements = _dedupe_elements(block_a_elements + block_b_elements)
            conditional_batch_elements = _dedupe_elements(block_a_elements + block_c_elements)
            if not main_batch_elements:
                main_batch_elements = main_elements
            main_terms = _query_terms_from_elements(main_batch_elements)
            effect_terms = _query_terms_from_elements(effect_elements)
            conditional_terms = _query_terms_from_elements(conditional_batch_elements or block_c_elements)
            query_blueprints: List[Dict[str, Any]] = [
                {
                    "batch_id": main_batch_id,
                    "goal": goal,
                    "semantic_text": _safe_text(group.get("semantic_query_text")),
                    "sub_plan_id": sub_plan_id,
                    "effect_cluster_ids": list(group.get("effect_cluster_ids") or []),
                    "display_search_elements": main_batch_elements,
                    "must_terms_zh": main_terms.get("must_terms_zh") or [],
                    "must_terms_en": main_terms.get("must_terms_en") or [],
                    "should_terms_zh": effect_terms.get("must_terms_zh") or [],
                    "should_terms_en": effect_terms.get("must_terms_en") or [],
                    "ipc_cpc_codes": main_terms.get("ipc_cpc_codes") or [],
                }
            ]
            retrieval_steps: List[Dict[str, Any]] = [
                {
                    "step_id": f"{sub_plan_id}_step_1",
                    "title": f"{goal} / 核心特征击穿",
                    "purpose": f"围绕 5 分核心效果“{goal}”执行首轮宽召回，先验证 Block B 主特征是否能稳定召回对比文献。",
                    "feature_combination": "保留 Block A，并使用当前效果组的 Block B 主特征；默认不启用 Block C 限定。",
                    "language_strategy": "中文优先，必要时补英文同义表达。",
                    "ipc_cpc_mode": "按需补充 IPC/CPC",
                    "ipc_cpc_codes": main_terms.get("ipc_cpc_codes") or [],
                    "expected_recall": "拿到方向明确、便于判断是否已击穿 Block B 的首轮候选池。",
                    "fallback_action": "结果过窄则放宽同义词和语言组合；结构化执行结果会决定是否激活 Block C 条件分支。",
                    "query_blueprint_refs": [main_batch_id],
                    "phase_key": "execute_search",
                    "activation_mode": "immediate",
                    "depends_on_step_ids": [],
                    "activation_conditions": {"any_of": []},
                    "activation_summary": "计划确认后立即执行。",
                }
            ]
            if block_c_elements:
                conditional_batch_id = f"{sub_plan_id}_batch_2"
                query_blueprints.append(
                    {
                        "batch_id": conditional_batch_id,
                        "goal": f"{goal} / Block C 条件分支",
                        "semantic_text": _block_c_semantic_text(
                            goal,
                            block_c_elements,
                            _safe_text(group.get("semantic_query_text")),
                        ),
                        "sub_plan_id": sub_plan_id,
                        "effect_cluster_ids": list(group.get("effect_cluster_ids") or []),
                        "display_search_elements": conditional_batch_elements or block_c_elements,
                        "must_terms_zh": conditional_terms.get("must_terms_zh") or [],
                        "must_terms_en": conditional_terms.get("must_terms_en") or [],
                        "should_terms_zh": _merge_terms(
                            main_terms.get("must_terms_zh") or [],
                            effect_terms.get("must_terms_zh") or [],
                        ),
                        "should_terms_en": _merge_terms(
                            main_terms.get("must_terms_en") or [],
                            effect_terms.get("must_terms_en") or [],
                        ),
                        "ipc_cpc_codes": conditional_terms.get("ipc_cpc_codes") or [],
                    }
                )
                retrieval_steps.append(
                    {
                        "step_id": f"{sub_plan_id}_step_2",
                        "title": f"{goal} / 从权与协同限定特征包围",
                        "purpose": "当主检索已命中 Block B 或结果过宽时，叠加 Block C 限定特征，执行从权防守检索或结果降噪。",
                        "feature_combination": "在主检索基础上加入 Block C 限定特征，必要时结合分类号收敛到更可比的对比文件。",
                        "language_strategy": "延续当前命中语言，并补充 Block C 的中英文限定表达。",
                        "ipc_cpc_mode": "优先沿用主检索命中的 IPC/CPC，再按 Block C 补充。",
                        "ipc_cpc_codes": conditional_terms.get("ipc_cpc_codes") or [],
                        "expected_recall": "获得更聚焦的 A+B+C 候选方向，或为从权退路提供组合对比线索。",
                        "fallback_action": "若仍过宽则继续收紧 Block C 与分类号；若过窄则保留 Block C 核心词并放宽辅助词。",
                        "query_blueprint_refs": [conditional_batch_id],
                        "phase_key": "execute_search",
                        "activation_mode": "conditional",
                        "depends_on_step_ids": [f"{sub_plan_id}_step_1"],
                        "activation_conditions": {
                            "any_of": [
                                {"signal": "primary_goal_reached", "equals": True},
                                {"signal": "recall_quality", "equals": "too_broad"},
                            ]
                        },
                        "activation_summary": "命中 Block B 或结果过宽时，激活 Block C 条件分支做从权防守检索或降噪。",
                    }
                )
            sub_plans.append(
                {
                    "sub_plan_id": sub_plan_id,
                    "title": goal,
                    "goal": goal,
                    "semantic_query_text": _safe_text(group.get("semantic_query_text")),
                    "search_elements": main_elements,
                    "retrieval_steps": retrieval_steps,
                    "query_blueprints": query_blueprints,
                    "classification_hints": [],
                }
            )
    else:
        search_matrix = _search_matrix_items(analysis_payload)
        all_elements = [mapped for mapped in (_mapped_element(item) for item in search_matrix) if mapped]
        if all_elements:
            block_a_elements = _elements_for_blocks(all_elements, "A")
            block_c_elements = _elements_for_blocks(all_elements, "C")
            main_batch_elements = _dedupe_elements(
                [item for item in all_elements if str(item.get("block_id") or "").strip().upper() != "C"] or all_elements
            )
            conditional_batch_elements = _dedupe_elements(block_a_elements + block_c_elements) or block_c_elements
            main_terms = _query_terms_from_elements(main_batch_elements)
            conditional_terms = _query_terms_from_elements(conditional_batch_elements)
            query_blueprints: List[Dict[str, Any]] = [
                {
                    "batch_id": "sub_plan_1_batch_1",
                    "goal": "执行首轮检索",
                    "sub_plan_id": "sub_plan_1",
                    "display_search_elements": main_batch_elements,
                    "must_terms_zh": main_terms.get("must_terms_zh") or [],
                    "must_terms_en": main_terms.get("must_terms_en") or [],
                    "should_terms_zh": [],
                    "should_terms_en": [],
                    "ipc_cpc_codes": main_terms.get("ipc_cpc_codes") or [],
                }
            ]
            retrieval_steps: List[Dict[str, Any]] = [
                {
                    "step_id": "sub_plan_1_step_1",
                    "title": "子计划 1 / 首轮宽召回",
                    "purpose": "围绕当前检索要素执行首轮宽召回，确认有效语言与噪声水平。",
                    "feature_combination": "基于主检索要素组合，先不默认加入 Block C 限定。",
                    "language_strategy": "中文优先，必要时补英文",
                    "ipc_cpc_mode": "先不使用 IPC/CPC，必要时再补",
                    "ipc_cpc_codes": main_terms.get("ipc_cpc_codes") or [],
                    "expected_recall": "拿到可审查的首轮候选池",
                    "fallback_action": "结果过窄时扩词或切换语言策略；是否激活 Block C 由执行结果信号决定",
                    "query_blueprint_refs": ["sub_plan_1_batch_1"],
                    "phase_key": "execute_search",
                    "activation_mode": "immediate",
                    "depends_on_step_ids": [],
                    "activation_conditions": {"any_of": []},
                    "activation_summary": "计划确认后立即执行。",
                }
            ]
            if block_c_elements:
                query_blueprints.append(
                    {
                        "batch_id": "sub_plan_1_batch_2",
                        "goal": "执行 Block C 条件检索",
                        "sub_plan_id": "sub_plan_1",
                        "semantic_text": _block_c_semantic_text("执行首轮检索", block_c_elements, ""),
                        "display_search_elements": conditional_batch_elements,
                        "must_terms_zh": conditional_terms.get("must_terms_zh") or [],
                        "must_terms_en": conditional_terms.get("must_terms_en") or [],
                        "should_terms_zh": main_terms.get("must_terms_zh") or [],
                        "should_terms_en": main_terms.get("must_terms_en") or [],
                        "ipc_cpc_codes": conditional_terms.get("ipc_cpc_codes") or [],
                    }
                )
                retrieval_steps.append(
                    {
                        "step_id": "sub_plan_1_step_2",
                        "title": "子计划 1 / Block C 条件分支",
                        "purpose": "当主检索已找到核心方向或召回过宽时，加入 Block C 限定特征做降噪或从权防守检索。",
                        "feature_combination": "在主检索基础上加入 Block C 限定特征。",
                        "language_strategy": "沿用当前命中语言，并补充 Block C 中英文表达",
                        "ipc_cpc_mode": "沿用主检索分类号并按需补充",
                        "ipc_cpc_codes": conditional_terms.get("ipc_cpc_codes") or [],
                        "expected_recall": "获得更聚焦的 Block C 候选池",
                        "fallback_action": "继续微调 Block C 词与分类号，不新增计划外步骤",
                        "query_blueprint_refs": ["sub_plan_1_batch_2"],
                        "phase_key": "execute_search",
                        "activation_mode": "conditional",
                        "depends_on_step_ids": ["sub_plan_1_step_1"],
                        "activation_conditions": {
                            "any_of": [
                                {"signal": "primary_goal_reached", "equals": True},
                                {"signal": "recall_quality", "equals": "too_broad"},
                            ]
                        },
                        "activation_summary": "命中主目标或结果过宽时，激活 Block C 条件分支。",
                    }
                )
            sub_plans.append(
                {
                    "sub_plan_id": "sub_plan_1",
                    "title": "子计划 1",
                    "goal": "执行首轮检索",
                    "semantic_query_text": "",
                    "search_elements": all_elements,
                    "retrieval_steps": retrieval_steps,
                    "query_blueprints": query_blueprints,
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
    effect_plan_groups = _effect_plan_groups(analysis_payload)
    sub_plans = build_analysis_sub_plans(analysis_payload)
    element_lines = []
    for item in seeded_search_elements.get("search_elements") or []:
        if not isinstance(item, dict):
            continue
        block_id = str(item.get("block_id") or "").strip().upper()
        label = f"[Block {block_id}] " if block_id else ""
        element_lines.append(f"- {label}{str(item.get('element_name') or '').strip()}")
    effect_lines: List[str] = []
    if effect_plan_groups:
        for group in effect_plan_groups:
            effect_lines.append(f"### 核心效果{int(group.get('index') or 0) or 1}：{_safe_text(group.get('effect_text'))}")
            effect_lines.append("#### 语义检索文本")
            semantic_text = _safe_text(group.get("semantic_query_text"))
            if semantic_text:
                effect_lines.append("```text")
                effect_lines.append(semantic_text)
                effect_lines.append("```")
            else:
                effect_lines.append("> 未生成语义检索文本。")
            effect_lines.append("#### 5分效果检索要素表")
            effect_lines.extend(_render_elements_table(group.get("main_elements") or []))
            block_c_elements = _elements_for_blocks(group.get("main_elements") or [], "C")
            if block_c_elements:
                effect_lines.append("")
                effect_lines.append("#### Block C 条件分支要素表")
                effect_lines.extend(_render_elements_table(block_c_elements))
            effect_lines.append("")
    else:
        technical_effects = _technical_effect_items(analysis_payload)
        effect_lines = [f"- {_safe_text(item.get('effect_text'))}" for item in technical_effects if _safe_text(item.get("effect_text"))]

    plan_lines: List[str] = []
    for index, item in enumerate(sub_plans, start=1):
        title = _safe_text(item.get("title") or item.get("goal") or item.get("sub_plan_id")) or f"子计划 {index}"
        plan_lines.append(f"### 方案{index}：{title}")
        for step_index, step in enumerate(item.get("retrieval_steps") or [], start=1):
            if not isinstance(step, dict):
                continue
            activation_mode = _safe_text(step.get("activation_mode")) or "immediate"
            activation_label = "立即执行" if activation_mode == "immediate" else "条件触发"
            summary = _safe_text(step.get("activation_summary"))
            line = f"- Step {step_index}（{activation_label}）：{_safe_text(step.get('title')) or f'步骤 {step_index}'}"
            if summary:
                line += f"。{summary}"
            plan_lines.append(line)
        plan_lines.append("")
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
