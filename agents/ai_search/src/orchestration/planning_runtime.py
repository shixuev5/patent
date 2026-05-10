"""Planning-stage deterministic helpers."""

from __future__ import annotations

import json
from typing import Any, Dict

from agents.ai_search.src.main_agent.schemas import SearchPlanExecutionSpecInput
from agents.ai_search.src.execution_state import normalize_execution_plan
from agents.ai_search.src.orchestration.session_views import build_gap_progress
from agents.ai_search.src.main_agent.search_plan_schemas import SearchPlanExecutionSpecDraftInput
from agents.ai_search.src.search_elements import normalize_search_elements_payload
from backend.time_utils import utc_now_z

ALLOWED_AI_SEARCH_DATABASES = {"zhihuiya", "openalex", "semanticscholar", "crossref"}


def _normalize_database_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return ["zhihuiya"]
    outputs: list[str] = []
    for value in values:
        database = str(value or "").strip().lower()
        if database in ALLOWED_AI_SEARCH_DATABASES and database not in outputs:
            outputs.append(database)
    return outputs or ["zhihuiya"]


def _canonicalize_planner_execution_spec(
    execution_spec: Dict[str, Any],
    *,
    search_elements_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    draft = SearchPlanExecutionSpecDraftInput.model_validate(execution_spec).model_dump(mode="python")
    search_scope = draft.get("search_scope") if isinstance(draft.get("search_scope"), dict) else {}
    sub_plans = draft.get("sub_plans") if isinstance(draft.get("sub_plans"), list) else []
    canonical_sub_plans = []
    for item in sub_plans:
        if not isinstance(item, dict):
            continue
        retrieval_steps = item.get("retrieval_steps") if isinstance(item.get("retrieval_steps"), list) else []
        canonical_steps = []
        for step in retrieval_steps:
            if not isinstance(step, dict):
                continue
            canonical_steps.append({**step, "phase_key": "execute_search"})
        canonical_sub_plans.append(
            {
                "sub_plan_id": str(item.get("sub_plan_id") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "goal": str(item.get("goal") or "").strip(),
                "semantic_query_text": str(item.get("semantic_query_text") or "").strip(),
                "retrieval_steps": canonical_steps,
                "query_blueprints": item.get("query_blueprints") if isinstance(item.get("query_blueprints"), list) else [],
            }
        )
    return SearchPlanExecutionSpecInput.model_validate(
        {
            "search_scope": {
                **search_scope,
                "objective": str(search_scope.get("objective") or search_elements_snapshot.get("objective") or "").strip(),
                "applicants": search_scope.get("applicants") if isinstance(search_scope.get("applicants"), list) else search_elements_snapshot.get("applicants") or [],
                "filing_date": search_scope.get("filing_date") or search_elements_snapshot.get("filing_date"),
                "priority_date": search_scope.get("priority_date") or search_elements_snapshot.get("priority_date"),
                "languages": search_scope.get("languages") if isinstance(search_scope.get("languages"), list) else [],
                "databases": _normalize_database_list(search_scope.get("databases")),
                "excluded_items": search_scope.get("excluded_items") if isinstance(search_scope.get("excluded_items"), list) else [],
                "source": search_scope.get("source") if isinstance(search_scope.get("source"), dict) else {},
            },
            "constraints": draft.get("constraints") if isinstance(draft.get("constraints"), dict) else {},
            "execution_policy": draft.get("execution_policy") if isinstance(draft.get("execution_policy"), dict) else {},
            "sub_plans": canonical_sub_plans,
            "search_elements_snapshot": search_elements_snapshot,
        }
    ).model_dump(mode="python")


def _search_elements_snapshot(context: Any, execution_spec: Dict[str, Any]) -> Dict[str, Any]:
    explicit = execution_spec.get("search_elements_snapshot") if isinstance(execution_spec.get("search_elements_snapshot"), dict) else {}
    current = context.current_search_elements() if hasattr(context, "current_search_elements") else {}
    search_scope = execution_spec.get("search_scope") if isinstance(execution_spec.get("search_scope"), dict) else {}
    snapshot = normalize_search_elements_payload(explicit or current or {})
    if snapshot.get("status") == "needs_answer" or not str(snapshot.get("objective") or "").strip():
        snapshot = normalize_search_elements_payload(
            {
                "status": "complete",
                "objective": str(search_scope.get("objective") or "").strip(),
                "applicants": search_scope.get("applicants") if isinstance(search_scope.get("applicants"), list) else [],
                "filing_date": search_scope.get("filing_date"),
                "priority_date": search_scope.get("priority_date"),
                "search_elements": explicit.get("search_elements") if isinstance(explicit.get("search_elements"), list) else [],
                "missing_items": explicit.get("missing_items") if isinstance(explicit.get("missing_items"), list) else [],
            }
        )
    return snapshot


def compile_confirmed_search_plan(
    context: Any,
    *,
    review_markdown: str,
    execution_spec: Dict[str, Any],
    runtime: Any | None = None,
) -> Dict[str, Any]:
    review_markdown = str(review_markdown or "").strip()
    if not review_markdown:
        raise ValueError("已确认检索计划缺少 review_markdown。")
    if not isinstance(execution_spec, dict) or not execution_spec:
        raise ValueError("已确认检索计划缺少 execution_spec。")
    search_elements_snapshot = _search_elements_snapshot(context, execution_spec)
    canonical_execution_spec = _canonicalize_planner_execution_spec(
        execution_spec,
        search_elements_snapshot=search_elements_snapshot,
    )
    normalized_plan = normalize_execution_plan(canonical_execution_spec)
    latest_plan = context.storage.get_ai_search_plan(context.task_id)
    latest_version = int(latest_plan.get("plan_version") or 0) if isinstance(latest_plan, dict) else 0
    target_plan_version = int(context.storage.get_next_ai_search_plan_version(context.task_id))
    if target_plan_version <= latest_version:
        target_plan_version = latest_version + 1
    if latest_plan:
        context.storage.update_ai_search_plan(
            context.task_id,
            int(latest_plan["plan_version"]),
            status="superseded",
            superseded_at=utc_now_z(),
        )
    context.storage.create_ai_search_plan(
        {
            "task_id": context.task_id,
            "plan_version": target_plan_version,
            "status": "confirmed",
            "review_markdown": review_markdown,
            "execution_spec_json": normalized_plan,
            "confirmed_at": utc_now_z(),
        }
    )
    for item in reversed(context.storage.list_ai_search_messages(context.task_id)):
        if str(item.get("kind") or "").strip() != "plan_confirmation":
            continue
        if str(item.get("content") or "").strip() != review_markdown:
            continue
        message_id = str(item.get("message_id") or "").strip()
        if message_id:
            context.storage.update_ai_search_message(message_id, plan_version=target_plan_version)
        break
    current_todo = context.current_todo()
    if current_todo:
        context.update_todo(
            str(current_todo.get("todo_id") or "").strip(),
            "paused",
            current_task=None,
            resume_from="await_plan_confirmation",
            runtime=runtime,
        )
    context.update_task_phase("drafting_plan", runtime=runtime, active_plan_version=target_plan_version)
    return {"plan_version": target_plan_version, "status": "confirmed"}


def build_planning_context(context: Any, plan_version: int = 0) -> Dict[str, Any]:
    version = int(plan_version or context.active_plan_version() or 0)
    current_plan = context.storage.get_ai_search_plan(context.task_id, version) if version > 0 else context.storage.get_ai_search_plan(context.task_id)
    current_plan = current_plan if isinstance(current_plan, dict) else {}
    task = context.storage.get_task(context.task_id)
    ai_search_meta = ((getattr(task, "metadata", {}) or {}).get("ai_search", {}) or {})
    return {
        "phase": context.current_phase(),
        "active_plan_version": version or None,
        "source_mode": str(ai_search_meta.get("seed_mode") or "").strip() or None,
        "search_elements": context.current_search_elements(version),
        "source_context": {
            "title": str(ai_search_meta.get("source_title") or "").strip() or None,
            "publication_number": str(ai_search_meta.get("source_pn") or "").strip() or None,
            "applicants": context.current_search_elements(version).get("applicants") if isinstance(context.current_search_elements(version), dict) else [],
        },
        "current_plan": {
            "plan_version": int(current_plan.get("plan_version") or 0),
            "status": str(current_plan.get("status") or "").strip(),
            "review_markdown": str(current_plan.get("review_markdown") or "").strip(),
            "execution_spec": current_plan.get("execution_spec_json") if isinstance(current_plan.get("execution_spec_json"), dict) else {},
        }
        if isinstance(current_plan, dict)
        else None,
        "gap_progress": build_gap_progress(context, version),
        "gap_context": context.latest_gap_context(version),
        "analysis_seed": {
            "status": str(ai_search_meta.get("analysis_seed_status") or "").strip() or None,
            "source_task_id": str(ai_search_meta.get("source_task_id") or "").strip() or None,
            "source_title": str(ai_search_meta.get("source_title") or "").strip() or None,
            "source_pn": str(ai_search_meta.get("source_pn") or "").strip() or None,
        },
    }
