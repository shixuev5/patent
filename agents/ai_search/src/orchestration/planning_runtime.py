"""Planning-stage deterministic helpers."""

from __future__ import annotations

import json
from typing import Any, Dict

from agents.ai_search.src.execution_state import normalize_execution_plan
from agents.ai_search.src.orchestration.session_views import build_gap_progress
from agents.ai_search.src.subagents.search_elements.normalize import normalize_search_elements_payload
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


def publish_planner_draft(context: Any, *, runtime: Any | None = None) -> Dict[str, Any]:
    draft = context.current_planner_draft()
    if not draft:
        raise ValueError("当前不存在 planner 草案。")
    review_markdown = str(draft.get("review_markdown") or "").strip()
    if not review_markdown:
        raise ValueError("planner draft 缺少 review_markdown。")
    execution_spec = draft.get("execution_spec") if isinstance(draft.get("execution_spec"), dict) else {}
    if not execution_spec:
        raise ValueError("planner draft 缺少 execution_spec。")
    search_elements_snapshot = normalize_search_elements_payload(context.current_search_elements() or {})
    normalized_plan = normalize_execution_plan(execution_spec, search_elements_snapshot)
    search_scope = normalized_plan.get("search_scope") if isinstance(normalized_plan.get("search_scope"), dict) else {}
    search_scope = {
        "objective": str(search_scope.get("objective") or search_elements_snapshot.get("objective") or "").strip(),
        "applicants": search_scope.get("applicants") if isinstance(search_scope.get("applicants"), list) else search_elements_snapshot.get("applicants") or [],
        "filing_date": search_scope.get("filing_date") or search_elements_snapshot.get("filing_date"),
        "priority_date": search_scope.get("priority_date") or search_elements_snapshot.get("priority_date"),
        "languages": search_scope.get("languages") if isinstance(search_scope.get("languages"), list) else [],
        "databases": _normalize_database_list(search_scope.get("databases")),
        "excluded_items": search_scope.get("excluded_items") if isinstance(search_scope.get("excluded_items"), list) else [],
        "source": search_scope.get("source") if isinstance(search_scope.get("source"), dict) else {},
    }
    latest_plan = context.storage.get_ai_search_plan(context.task_id)
    if latest_plan:
        context.storage.update_ai_search_plan(
            context.task_id,
            int(latest_plan["plan_version"]),
            status="superseded",
            superseded_at=utc_now_z(),
        )
    plan_version = context.storage.get_next_ai_search_plan_version(context.task_id)
    context.storage.create_ai_search_plan(
        {
            "task_id": context.task_id,
            "plan_version": plan_version,
            "status": "draft",
            "review_markdown": review_markdown,
            "execution_spec_json": {
                "search_scope": search_scope,
                "constraints": normalized_plan.get("constraints") or {},
                "execution_policy": normalized_plan.get("execution_policy") or {},
                "sub_plans": normalized_plan.get("sub_plans") or [],
            },
        }
    )
    current_todo = context.current_todo()
    if current_todo:
        context.update_todo(
            str(current_todo.get("todo_id") or "").strip(),
            "paused",
            current_task=None,
            resume_from="await_plan_confirmation",
            runtime=runtime,
        )
    context.update_task_phase("drafting_plan", runtime=runtime, active_plan_version=plan_version)
    context.clear_planner_draft(runtime=runtime)
    return {"plan_version": plan_version}


def build_planning_context(context: Any, plan_version: int = 0) -> Dict[str, Any]:
    version = int(plan_version or context.active_plan_version() or 0)
    current_plan = context.storage.get_ai_search_plan(context.task_id, version) if version > 0 else context.storage.get_ai_search_plan(context.task_id)
    task = context.storage.get_task(context.task_id)
    ai_search_meta = ((getattr(task, "metadata", {}) or {}).get("ai_search", {}) or {})
    return {
        "phase": context.current_phase(),
        "active_plan_version": version or None,
        "source_mode": str(ai_search_meta.get("seed_mode") or "").strip() or None,
        "search_elements": context.current_search_elements(version),
        "planner_draft": context.current_planner_draft(),
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
