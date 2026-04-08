"""
AI 检索执行状态模型与步骤级计划补全。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, Field


ALLOWED_STEP_PHASE_KEYS = {
    "execute_search",
    "coarse_screen",
    "close_read",
    "generate_feature_table",
}


class ExecutionStepSummary(BaseModel):
    todo_id: str
    step_id: str
    sub_plan_id: str
    result_summary: str = ""
    adjustments: List[str] = Field(default_factory=list)
    plan_change_assessment: Dict[str, Any] = Field(default_factory=dict)
    next_recommendation: str = ""
    candidate_pool_size: int = 0
    new_unique_candidates: int = 0


class PlanProbeFindings(BaseModel):
    overall_observation: str = ""
    retrieval_step_refs: List[str] = Field(default_factory=list)
    signals: List[Dict[str, Any]] = Field(default_factory=list)


def _string_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    items: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in items:
            items.append(text)
    return items


def _normalize_blueprint(batch: Dict[str, Any], sub_plan_id: str, index: int) -> Dict[str, Any]:
    batch_id = str(batch.get("batch_id") or f"{sub_plan_id}-batch-{index}").strip() or f"{sub_plan_id}-batch-{index}"
    return {
        **batch,
        "batch_id": batch_id,
        "sub_plan_id": str(batch.get("sub_plan_id") or sub_plan_id).strip() or sub_plan_id,
    }


def _normalize_retrieval_step(step: Dict[str, Any], sub_plan_id: str, blueprint_ids: set[str], index: int) -> Dict[str, Any]:
    step_id = str(step.get("step_id") or f"{sub_plan_id}_step_{index}").strip() or f"{sub_plan_id}_step_{index}"
    query_blueprint_refs = _string_list(step.get("query_blueprint_refs"))
    if not query_blueprint_refs:
        raise ValueError(f"sub_plan `{sub_plan_id}` step `{step_id}` 缺少 query_blueprint_refs。")
    invalid_refs = [ref for ref in query_blueprint_refs if ref not in blueprint_ids]
    if invalid_refs:
        raise ValueError(
            f"sub_plan `{sub_plan_id}` step `{step_id}` 引用了未定义的 query_blueprints: {', '.join(invalid_refs)}。"
        )
    phase_key = str(step.get("phase_key") or "execute_search").strip() or "execute_search"
    if phase_key not in ALLOWED_STEP_PHASE_KEYS:
        raise ValueError(f"sub_plan `{sub_plan_id}` step `{step_id}` 使用了不支持的 phase_key `{phase_key}`。")
    return {
        "step_id": step_id,
        "title": str(step.get("title") or f"{sub_plan_id} / step {index}").strip() or f"{sub_plan_id} / step {index}",
        "purpose": str(step.get("purpose") or "").strip(),
        "feature_combination": str(step.get("feature_combination") or "").strip(),
        "language_strategy": str(step.get("language_strategy") or "").strip(),
        "ipc_cpc_mode": str(step.get("ipc_cpc_mode") or "").strip(),
        "ipc_cpc_codes": _string_list(step.get("ipc_cpc_codes")),
        "expected_recall": str(step.get("expected_recall") or "").strip(),
        "fallback_action": str(step.get("fallback_action") or "").strip(),
        "query_blueprint_refs": query_blueprint_refs,
        "phase_key": phase_key,
        "probe_summary": step.get("probe_summary") if isinstance(step.get("probe_summary"), dict) else {},
    }


def normalize_execution_plan(plan_json: Dict[str, Any], search_elements: Dict[str, Any]) -> Dict[str, Any]:
    source = plan_json if isinstance(plan_json, dict) else {}
    sub_plans = source.get("sub_plans") if isinstance(source.get("sub_plans"), list) else []
    if not sub_plans:
        raise ValueError("execution_spec.sub_plans 不能为空。")

    normalized_sub_plans: List[Dict[str, Any]] = []
    for sub_index, item in enumerate(sub_plans, start=1):
        if not isinstance(item, dict):
            continue
        sub_plan_id = str(item.get("sub_plan_id") or item.get("id") or f"sub_plan_{sub_index}").strip() or f"sub_plan_{sub_index}"
        title = str(item.get("title") or f"子计划 {sub_index}").strip() or f"子计划 {sub_index}"
        goal = str(item.get("goal") or title).strip() or title
        sub_plan_search_elements = item.get("search_elements") if isinstance(item.get("search_elements"), list) else []
        raw_query_blueprints = item.get("query_blueprints") if isinstance(item.get("query_blueprints"), list) else []
        query_blueprints = [
            _normalize_blueprint(batch, sub_plan_id, index)
            for index, batch in enumerate(raw_query_blueprints, start=1)
            if isinstance(batch, dict)
        ]
        blueprint_ids = {str(batch.get("batch_id") or "").strip() for batch in query_blueprints}
        raw_retrieval_steps = item.get("retrieval_steps") if isinstance(item.get("retrieval_steps"), list) else []
        if not raw_retrieval_steps:
            raise ValueError(f"sub_plan `{sub_plan_id}` 缺少 retrieval_steps。")
        retrieval_steps = [
            _normalize_retrieval_step(step, sub_plan_id, blueprint_ids, index)
            for index, step in enumerate(raw_retrieval_steps, start=1)
            if isinstance(step, dict)
        ]
        normalized_sub_plans.append(
            {
                "sub_plan_id": sub_plan_id,
                "title": title,
                "goal": goal,
                "semantic_query_text": str(item.get("semantic_query_text") or item.get("semanticQueryText") or "").strip(),
                "search_elements": [element for element in sub_plan_search_elements if isinstance(element, dict)],
                "retrieval_steps": retrieval_steps,
                "query_blueprints": query_blueprints,
                "classification_hints": [hint for hint in (item.get("classification_hints") or []) if isinstance(hint, dict)],
            }
        )

    execution_policy = source.get("execution_policy") if isinstance(source.get("execution_policy"), dict) else {}
    normalized_execution_policy = {
        "dynamic_replanning": bool(execution_policy.get("dynamic_replanning", True)),
        "planner_visibility": str(execution_policy.get("planner_visibility") or "step_summary_only"),
        "max_step_attempts": int(execution_policy.get("max_step_attempts") or 3),
    }

    search_scope = source.get("search_scope") if isinstance(source.get("search_scope"), dict) else {}
    constraints = source.get("constraints") if isinstance(source.get("constraints"), dict) else {}
    return {
        **source,
        "search_scope": search_scope,
        "constraints": constraints,
        "sub_plans": normalized_sub_plans,
        "execution_policy": normalized_execution_policy,
        "search_elements_snapshot": search_elements if isinstance(search_elements, dict) else {},
    }


def build_execution_todo_id(plan_version: int, sub_plan_id: str, step_id: str) -> str:
    return f"plan_{int(plan_version)}:{str(sub_plan_id or '').strip()}:{str(step_id or '').strip()}"


def build_execution_todo_description(step: Dict[str, Any]) -> str:
    ipc_codes = _string_list(step.get("ipc_cpc_codes"))
    ipc_text = str(step.get("ipc_cpc_mode") or "").strip() or "不使用 IPC/CPC"
    if ipc_codes:
        ipc_text = f"{ipc_text}（{', '.join(ipc_codes)}）"
    parts = [
        f"目的：{str(step.get('purpose') or '').strip() or '未填写'}",
        f"特征组合：{str(step.get('feature_combination') or '').strip() or '未填写'}",
        f"语言策略：{str(step.get('language_strategy') or '').strip() or '未填写'}",
        f"IPC/CPC：{ipc_text}",
        f"目标召回：{str(step.get('expected_recall') or '').strip() or '未填写'}",
        f"失败调整：{str(step.get('fallback_action') or '').strip() or '未填写'}",
    ]
    return "；".join(parts)


def build_execution_todos(plan_version: int, execution_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    todos: List[Dict[str, Any]] = []
    for sub_plan in execution_plan.get("sub_plans") or []:
        if not isinstance(sub_plan, dict):
            continue
        sub_plan_id = str(sub_plan.get("sub_plan_id") or "").strip()
        for step in sub_plan.get("retrieval_steps") or []:
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("step_id") or "").strip()
            todo_id = build_execution_todo_id(plan_version, sub_plan_id, step_id)
            todos.append(
                {
                    "todo_id": todo_id,
                    "sub_plan_id": sub_plan_id,
                    "step_id": step_id,
                    "phase_key": str(step.get("phase_key") or "execute_search").strip() or "execute_search",
                    "title": str(step.get("title") or "").strip(),
                    "description": build_execution_todo_description(step),
                    "status": "pending",
                    "attempt_count": 0,
                    "resume_from": "run_execution_step.load",
                    "last_error": "",
                    "started_at": None,
                    "completed_at": None,
                    "state": {
                        "plan_version": int(plan_version),
                        "sub_plan_id": sub_plan_id,
                        "step_id": step_id,
                        "phase_key": str(step.get("phase_key") or "execute_search").strip() or "execute_search",
                        "query_blueprint_refs": _string_list(step.get("query_blueprint_refs")),
                    },
                }
            )
    return todos


def iter_plan_steps(execution_plan: Dict[str, Any]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    items: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for sub_plan in execution_plan.get("sub_plans") or []:
        if not isinstance(sub_plan, dict):
            continue
        for step in sub_plan.get("retrieval_steps") or []:
            if isinstance(step, dict):
                items.append((sub_plan, step))
    return items


def resolve_plan_step(execution_plan: Dict[str, Any], sub_plan_id: str, step_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    target_sub_plan_id = str(sub_plan_id or "").strip()
    target_step_id = str(step_id or "").strip()
    for sub_plan, step in iter_plan_steps(execution_plan):
        if str(sub_plan.get("sub_plan_id") or "").strip() == target_sub_plan_id and str(step.get("step_id") or "").strip() == target_step_id:
            return sub_plan, step
    raise KeyError(f"未找到 execution step: {target_sub_plan_id}:{target_step_id}")
