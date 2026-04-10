"""Aggregated read models for main-agent orchestration."""

from __future__ import annotations

from typing import Any, Dict, Optional

from agents.ai_search.src.orchestration.action_runtime import build_pending_action_view, current_pending_action
from agents.ai_search.src.state import get_ai_search_meta


def build_session_context(context: Any) -> Dict[str, Any]:
    task = context.storage.get_task(context.task_id)
    meta = get_ai_search_meta(task)
    run = context.active_run()
    pending_action = current_pending_action(context)
    return {
        "phase": context.current_phase(),
        "source_mode": str(meta.get("seed_mode") or "").strip() or None,
        "active_plan_version": int(meta.get("active_plan_version") or 0) or None,
        "pending_action": build_pending_action_view(pending_action),
        "human_decision_state": run.get("human_decision_state") if isinstance(run, dict) and isinstance(run.get("human_decision_state"), dict) else {},
        "run": {
            "run_id": str(run.get("run_id") or "").strip() or None,
            "phase": str(run.get("phase") or "").strip() or None,
            "status": str(run.get("status") or "").strip() or None,
            "active_retrieval_todo_id": str(run.get("active_retrieval_todo_id") or "").strip() or None,
            "selected_document_count": int(run.get("selected_document_count") or 0),
        }
        if isinstance(run, dict)
        else None,
    }


def build_gap_progress(context: Any, plan_version: int = 0) -> Dict[str, Any]:
    version = int(plan_version or context.active_plan_version() or 0)
    gap_context = context.latest_gap_context(version)
    close_read_result = gap_context.get("close_read_result") if isinstance(gap_context.get("close_read_result"), dict) else {}
    feature_compare_payload = gap_context.get("feature_compare_result")
    feature_compare_result = feature_compare_payload if isinstance(feature_compare_payload, dict) else {}
    limitation_gaps = close_read_result.get("limitation_gaps") if isinstance(close_read_result.get("limitation_gaps"), list) else []
    coverage_gaps = feature_compare_result.get("coverage_gaps") if isinstance(feature_compare_result.get("coverage_gaps"), list) else []
    document_assessments = close_read_result.get("document_assessments") if isinstance(close_read_result.get("document_assessments"), list) else []
    follow_up_hints: list[str] = []
    for values in (close_read_result.get("follow_up_hints"), feature_compare_result.get("follow_up_search_hints")):
        if not isinstance(values, list):
            continue
        for item in values:
            text = str(item or "").strip()
            if text and text not in follow_up_hints:
                follow_up_hints.append(text)
    readiness = str(feature_compare_result.get("creativity_readiness") or "").strip().lower()
    run_id = context.active_run_id(version)
    selected_count = len(context.storage.list_ai_search_documents(context.task_id, run_id, stages=["selected"])) if run_id else 0
    weak_evidence_count = 0
    for item in document_assessments:
        if not isinstance(item, dict):
            continue
        confidence = float(item.get("confidence") or 0.0)
        sufficiency = str(item.get("evidence_sufficiency") or "").strip().lower()
        if confidence < 0.55 or sufficiency in {"partial", "insufficient", "weak"}:
            weak_evidence_count += 1
    has_material_gap = bool(limitation_gaps or coverage_gaps or follow_up_hints or weak_evidence_count > 0)
    if readiness in {"ready", "sufficient", "enough"} and not has_material_gap and selected_count > 0:
        recommended_action = "complete_execution"
        should_continue_search = False
    elif has_material_gap:
        recommended_action = "replan_search"
        should_continue_search = True
    elif selected_count > 0:
        recommended_action = "feature_comparison"
        should_continue_search = False
    else:
        recommended_action = "replan_draft_plan"
        should_continue_search = True
    return {
        "plan_version": version,
        "current_phase": context.current_phase(),
        "selected_count": selected_count,
        "readiness": readiness or "unknown",
        "limitation_gap_count": len(limitation_gaps),
        "coverage_gap_count": len(coverage_gaps),
        "follow_up_hint_count": len(follow_up_hints),
        "weak_evidence_count": weak_evidence_count,
        "should_continue_search": should_continue_search,
        "recommended_action": recommended_action,
        "gap_context": gap_context,
    }


def summarize_plan(plan: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(plan, dict):
        return None
    return {
        "plan_version": int(plan.get("plan_version") or 0),
        "status": str(plan.get("status") or "").strip(),
        "review_markdown": str(plan.get("review_markdown") or "").strip(),
        "execution_spec": plan.get("execution_spec_json") if isinstance(plan.get("execution_spec_json"), dict) else {},
    }
