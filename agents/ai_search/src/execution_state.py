"""
AI 检索执行状态模型与步骤级计划补全。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Tuple

from pydantic import BaseModel, ConfigDict, Field


ALLOWED_STEP_PHASE_KEYS = {
    "execute_search",
    "coarse_screen",
    "close_read",
    "feature_comparison",
}
ALLOWED_ACTIVATION_MODES = {"immediate", "conditional"}

DEFAULT_EXECUTION_POLICY = {
    "dynamic_replanning": True,
    "planner_visibility": "step_summary_only",
    "max_step_attempts": 3,
    "max_rounds": 3,
    "max_no_progress_rounds": 2,
    "max_selected_documents": 5,
    "decision_on_exhaustion": True,
}


AiSearchRecallQuality = Literal["too_broad", "balanced", "too_narrow"]


class ExecutionPlanChangeAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requires_replan: bool = False
    reason: str = ""


class ExecutionOutcomeSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_goal_reached: bool = False
    recall_quality: AiSearchRecallQuality = "balanced"
    triggered_by_adjustment: bool = False


class ExecutionStepSummary(BaseModel):
    todo_id: str
    step_id: str
    sub_plan_id: str
    plan_change_assessment: ExecutionPlanChangeAssessment = Field(default_factory=ExecutionPlanChangeAssessment)
    candidate_pool_size: int = 0
    new_unique_candidates: int = 0
    outcome_signals: ExecutionOutcomeSignals = Field(default_factory=ExecutionOutcomeSignals)


class PlanProbeFindings(BaseModel):
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


def _normalize_activation_conditions(value: Any) -> Dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    any_of = source.get("any_of") if isinstance(source.get("any_of"), list) else []
    normalized_any_of: List[Dict[str, Any]] = []
    for item in any_of:
        if not isinstance(item, dict):
            continue
        signal = str(item.get("signal") or "").strip()
        if not signal:
            continue
        normalized: Dict[str, Any] = {"signal": signal}
        if "equals" in item:
            normalized["equals"] = item.get("equals")
        normalized_any_of.append(normalized)
    return {"any_of": normalized_any_of}


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
    phase_key = str(step.get("phase_key") or "execute_search").strip().lower() or "execute_search"
    if phase_key not in ALLOWED_STEP_PHASE_KEYS:
        raise ValueError(f"sub_plan `{sub_plan_id}` step `{step_id}` 使用了不支持的 phase_key `{phase_key}`。")
    activation_mode = str(step.get("activation_mode") or "immediate").strip().lower() or "immediate"
    if activation_mode not in ALLOWED_ACTIVATION_MODES:
        raise ValueError(f"sub_plan `{sub_plan_id}` step `{step_id}` 使用了不支持的 activation_mode `{activation_mode}`。")
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
        "activation_mode": activation_mode,
        "depends_on_step_ids": _string_list(step.get("depends_on_step_ids")),
        "activation_conditions": _normalize_activation_conditions(step.get("activation_conditions")),
        "activation_summary": str(step.get("activation_summary") or "").strip(),
        "probe_summary": step.get("probe_summary") if isinstance(step.get("probe_summary"), dict) else {},
    }


def normalize_execution_plan(plan_json: Dict[str, Any]) -> Dict[str, Any]:
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
                "retrieval_steps": retrieval_steps,
                "query_blueprints": query_blueprints,
            }
        )

    execution_policy = source.get("execution_policy") if isinstance(source.get("execution_policy"), dict) else {}
    normalized_execution_policy = {
        "dynamic_replanning": bool(execution_policy.get("dynamic_replanning", DEFAULT_EXECUTION_POLICY["dynamic_replanning"])),
        "planner_visibility": str(execution_policy.get("planner_visibility") or DEFAULT_EXECUTION_POLICY["planner_visibility"]),
        "max_step_attempts": max(int(execution_policy.get("max_step_attempts") or DEFAULT_EXECUTION_POLICY["max_step_attempts"]), 1),
        "max_rounds": max(int(execution_policy.get("max_rounds") or DEFAULT_EXECUTION_POLICY["max_rounds"]), 1),
        "max_no_progress_rounds": max(
            int(execution_policy.get("max_no_progress_rounds") or DEFAULT_EXECUTION_POLICY["max_no_progress_rounds"]),
            1,
        ),
        "max_selected_documents": max(
            int(execution_policy.get("max_selected_documents") or DEFAULT_EXECUTION_POLICY["max_selected_documents"]),
            1,
        ),
        "decision_on_exhaustion": bool(execution_policy.get("decision_on_exhaustion", DEFAULT_EXECUTION_POLICY["decision_on_exhaustion"])),
    }

    search_scope = source.get("search_scope") if isinstance(source.get("search_scope"), dict) else {}
    constraints = source.get("constraints") if isinstance(source.get("constraints"), dict) else {}
    search_elements_snapshot = source.get("search_elements_snapshot") if isinstance(source.get("search_elements_snapshot"), dict) else {}
    return {
        **source,
        "search_scope": search_scope,
        "constraints": constraints,
        "sub_plans": normalized_sub_plans,
        "execution_policy": normalized_execution_policy,
        "search_elements_snapshot": search_elements_snapshot,
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
            if str(step.get("activation_mode") or "immediate").strip().lower() == "conditional":
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


def build_execution_todo(plan_version: int, sub_plan: Dict[str, Any], step: Dict[str, Any]) -> Dict[str, Any]:
    sub_plan_id = str(sub_plan.get("sub_plan_id") or "").strip()
    step_id = str(step.get("step_id") or "").strip()
    todo_id = build_execution_todo_id(plan_version, sub_plan_id, step_id)
    return {
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
            "activation_mode": str(step.get("activation_mode") or "immediate").strip().lower() or "immediate",
        },
    }


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


def extract_outcome_signals(summary: Dict[str, Any] | None) -> Dict[str, Any]:
    source = summary if isinstance(summary, dict) else {}
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    raw = source.get("outcome_signals") if isinstance(source.get("outcome_signals"), dict) else metadata.get("outcome_signals")
    payload = raw if isinstance(raw, dict) else {}
    recall_quality = str(payload.get("recall_quality") or "").strip().lower()
    if recall_quality not in {"too_broad", "balanced", "too_narrow"}:
        recall_quality = "balanced"
    return {
        "primary_goal_reached": bool(payload.get("primary_goal_reached")),
        "recall_quality": recall_quality,
        "triggered_by_adjustment": bool(payload.get("triggered_by_adjustment")),
    }


def step_is_activated_by(
    step: Dict[str, Any],
    *,
    completed_step_ids: set[str],
    outcome_signals: Dict[str, Any],
) -> bool:
    if str(step.get("activation_mode") or "immediate").strip().lower() != "conditional":
        return False
    depends_on = set(_string_list(step.get("depends_on_step_ids")))
    if depends_on and not depends_on.issubset(completed_step_ids):
        return False
    conditions = step.get("activation_conditions") if isinstance(step.get("activation_conditions"), dict) else {}
    any_of = conditions.get("any_of") if isinstance(conditions.get("any_of"), list) else []
    if not any_of:
        return True
    for item in any_of:
        if not isinstance(item, dict):
            continue
        signal = str(item.get("signal") or "").strip()
        if not signal:
            continue
        expected = item.get("equals")
        if outcome_signals.get(signal) == expected:
            return True
    return False
