"""Execution-stage deterministic helpers."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from langgraph.types import interrupt

from agents.ai_search.src.execution_state import (
    DEFAULT_EXECUTION_POLICY,
    build_execution_todo,
    build_execution_todos,
    extract_outcome_signals,
    iter_plan_steps,
    normalize_execution_plan,
    resolve_plan_step,
    step_is_activated_by,
)
from agents.ai_search.src.orchestration.action_runtime import open_pending_action
from agents.ai_search.src.orchestration.phase_machine import enter_drafting_plan, phase_from_todo
from agents.ai_search.src.orchestration.session_views import build_gap_progress
from agents.ai_search.src.state import (
    PHASE_AWAITING_HUMAN_DECISION,
    PHASE_CLOSE_READ,
    PHASE_COMPLETED,
    PHASE_COARSE_SCREEN,
    PHASE_EXECUTE_SEARCH,
    PHASE_FEATURE_COMPARISON,
    phase_to_task_status,
)
from backend.time_utils import utc_now_z


def build_step_directive(context: Any, plan_version: int) -> Dict[str, Any]:
    plan = context.storage.get_ai_search_plan(context.task_id, int(plan_version)) or {}
    execution_spec = plan.get("execution_spec_json") if isinstance(plan.get("execution_spec_json"), dict) else {}
    normalized_plan = normalize_execution_plan(execution_spec, context.current_search_elements(plan_version))
    current_todo = context.current_todo()
    if not current_todo:
        return {
            "plan_version": int(plan_version),
            "current_todo": None,
            "current_step": None,
            "search_scope": normalized_plan.get("search_scope") or {},
            "query_blueprints": [],
            "history": [],
            "search_elements_snapshot": normalized_plan.get("search_elements_snapshot") or {},
            "gap_context": context.latest_gap_context(plan_version),
            "execution_policy": normalized_plan.get("execution_policy") or {},
        }
    sub_plan_id = str(current_todo.get("sub_plan_id") or "").strip()
    step_id = str(current_todo.get("step_id") or "").strip()
    sub_plan, step = resolve_plan_step(normalized_plan, sub_plan_id, step_id)
    query_refs = {str(ref or "").strip() for ref in (step.get("query_blueprint_refs") or []) if str(ref or "").strip()}
    query_blueprints = [
        item
        for item in (sub_plan.get("query_blueprints") or [])
        if isinstance(item, dict) and str(item.get("batch_id") or "").strip() in query_refs
    ]
    summaries = context.list_execution_step_summaries(int(plan_version), sub_plan_id=sub_plan_id)
    return {
        "plan_version": int(plan_version),
        "current_todo": current_todo,
        "current_step": step,
        "current_sub_plan": sub_plan,
        "search_scope": normalized_plan.get("search_scope") or {},
        "execution_policy": normalized_plan.get("execution_policy") or {},
        "query_blueprints": query_blueprints,
        "history": summaries,
        "search_elements_snapshot": normalized_plan.get("search_elements_snapshot") or {},
        "gap_context": context.latest_gap_context(plan_version),
    }


def build_execution_context(context: Any, plan_version: int = 0) -> Dict[str, Any]:
    version = int(plan_version or context.active_plan_version() or 0)
    run_id = context.active_run_id(version)
    candidate_count = len(context.storage.list_ai_search_documents(context.task_id, version)) if version > 0 else 0
    selected_count = len(context.storage.list_ai_search_documents(context.task_id, version, stages=["selected"])) if version > 0 else 0
    current_plan = context.storage.get_ai_search_plan(context.task_id, version) if version > 0 else None
    feature_comparison = context.storage.get_ai_search_feature_comparison(context.task_id, run_id) if run_id else None
    return {
        "phase": context.current_phase(),
        "active_plan_version": version or None,
        "current_plan": {
            "plan_version": int(current_plan.get("plan_version") or 0),
            "status": str(current_plan.get("status") or "").strip(),
            "execution_spec": current_plan.get("execution_spec_json") if isinstance(current_plan.get("execution_spec_json"), dict) else {},
        }
        if isinstance(current_plan, dict)
        else None,
        "current_todo": context.current_todo(),
        "step_directive": build_step_directive(context, version) if version > 0 else {},
        "execution_summaries": context.list_execution_step_summaries(version) if version > 0 else [],
        "document_stats": {
            "candidate_count": candidate_count,
            "selected_count": selected_count,
        },
        "feature_comparison": feature_comparison if isinstance(feature_comparison, dict) else {},
        "gap_progress": build_gap_progress(context, version),
    }


def build_conditional_todos_for_completed_step(context: Any, plan_version: int, todo_id: str) -> list[Dict[str, Any]]:
    target = context._todo_map().get(str(todo_id or "").strip())
    if not target:
        return []
    execution_plan = context.execution_plan_json(plan_version)
    if not execution_plan:
        return []

    normalized = normalize_execution_plan(execution_plan, context.current_search_elements(plan_version))
    completed_step_ids = {
        str(item.get("step_id") or "").strip()
        for item in context._current_todos()
        if str(item.get("status") or "").strip() == "completed"
    }
    completed_step_ids.add(str(target.get("step_id") or "").strip())
    existing_todo_ids = {
        str(item.get("todo_id") or "").strip()
        for item in context._current_todos()
        if str(item.get("todo_id") or "").strip()
    }
    outcome_signals = extract_outcome_signals(context.latest_execution_summary_for_todo(plan_version, todo_id))
    todos: list[Dict[str, Any]] = []
    for sub_plan, step in iter_plan_steps(normalized):
        if not step_is_activated_by(step, completed_step_ids=completed_step_ids, outcome_signals=outcome_signals):
            continue
        todo = build_execution_todo(plan_version, sub_plan, step)
        if str(todo.get("todo_id") or "").strip() in existing_todo_ids:
            continue
        todos.append(todo)
    return todos


def _gap_signature(progress: Dict[str, Any]) -> Dict[str, int]:
    return {
        "limitation_gap_count": int(progress.get("limitation_gap_count") or 0),
        "coverage_gap_count": int(progress.get("coverage_gap_count") or 0),
        "follow_up_hint_count": int(progress.get("follow_up_hint_count") or 0),
        "weak_evidence_count": int(progress.get("weak_evidence_count") or 0),
    }


def _gap_signature_score(signature: Dict[str, Any]) -> int:
    if not isinstance(signature, dict):
        return 0
    return sum(int(signature.get(key) or 0) for key in ("limitation_gap_count", "coverage_gap_count", "follow_up_hint_count", "weak_evidence_count"))


def _readiness_rank(value: Any) -> int:
    mapping = {"unknown": 0, "needs_more_evidence": 1, "insufficient": 1, "partial": 2, "developing": 2, "ready": 3, "sufficient": 3, "enough": 3}
    return mapping.get(str(value or "").strip().lower(), 0)


def evaluate_exhaustion_payload(context: Any, plan_version: Optional[int] = None) -> Dict[str, Any]:
    run = context.active_run(plan_version)
    version = int(run.get("plan_version") or plan_version or context.active_plan_version() or 0) if run else int(plan_version or context.active_plan_version() or 0)
    state = context._run_state(run)
    progress = build_gap_progress(context, version)
    policy = context.execution_policy(version)
    summaries = context.list_execution_step_summaries(version) if version > 0 else []
    processed_count = min(int(state.get("processed_execution_summary_count") or 0), len(summaries))
    current_round_summaries = summaries[processed_count:]
    new_unique_candidates = sum(int(item.get("new_unique_candidates") or 0) for item in current_round_summaries if isinstance(item, dict))
    selected_count = int(progress.get("selected_count") or 0)
    readiness = str(progress.get("readiness") or "unknown").strip() or "unknown"
    recommended_action = str(progress.get("recommended_action") or "").strip()
    gap_signature = _gap_signature(progress)
    previous_selected = state.get("last_selected_count")
    previous_readiness = state.get("last_readiness")
    previous_gap_signature = state.get("last_gap_signature") if isinstance(state.get("last_gap_signature"), dict) else None
    has_previous_baseline = previous_selected is not None or previous_readiness is not None or previous_gap_signature is not None
    readiness_improved = has_previous_baseline and _readiness_rank(readiness) > _readiness_rank(previous_readiness)
    gap_improved = has_previous_baseline and _gap_signature_score(gap_signature) < _gap_signature_score(previous_gap_signature or {})
    selected_grew = has_previous_baseline and selected_count > int(previous_selected or 0)
    is_no_progress = bool(
        new_unique_candidates == 0
        or (has_previous_baseline and not selected_grew and not readiness_improved and not gap_improved)
        or recommended_action == "replan_search"
    )
    no_progress_round_count = int(state.get("no_progress_round_count") or 0) + 1 if is_no_progress else 0
    execution_round_count = int(state.get("execution_round_count") or 0) + 1
    triggered_limit = ""
    if selected_count >= int(policy.get("max_selected_documents") or DEFAULT_EXECUTION_POLICY["max_selected_documents"]):
        triggered_limit = "max_selected_documents"
    elif no_progress_round_count >= int(policy.get("max_no_progress_rounds") or DEFAULT_EXECUTION_POLICY["max_no_progress_rounds"]):
        triggered_limit = "max_no_progress_rounds"
    elif execution_round_count >= int(policy.get("max_rounds") or DEFAULT_EXECUTION_POLICY["max_rounds"]):
        triggered_limit = "max_rounds"
    decision_reason = ""
    if triggered_limit == "max_selected_documents":
        decision_reason = "selected_documents_limit_reached"
    elif triggered_limit == "max_no_progress_rounds":
        decision_reason = "no_progress_limit_reached"
    elif triggered_limit == "max_rounds":
        decision_reason = "round_limit_reached"
    summary_parts = [
        f"已完成 {execution_round_count} 轮检索评估",
        f"连续无进展 {no_progress_round_count} 轮" if is_no_progress or no_progress_round_count > 0 else "",
        f"当前已选对比文献 {selected_count} 篇",
        f"本轮新增唯一候选 {new_unique_candidates} 篇",
        f"当前建议动作：{recommended_action or 'unknown'}",
    ]
    decision_summary = "；".join(part for part in summary_parts if part)
    should_request_decision = bool(triggered_limit) and bool(policy.get("decision_on_exhaustion", DEFAULT_EXECUTION_POLICY["decision_on_exhaustion"]))
    return {
        "plan_version": version,
        "execution_policy": policy,
        "is_no_progress": is_no_progress,
        "triggered_limit": triggered_limit,
        "decision_reason": decision_reason,
        "decision_summary": decision_summary,
        "should_request_decision": should_request_decision,
        "new_unique_candidates": new_unique_candidates,
        "execution_round_count": execution_round_count,
        "no_progress_round_count": no_progress_round_count,
        "selected_count": selected_count,
        "readiness": readiness,
        "recommended_action": recommended_action,
        "gap_signature": gap_signature,
        "processed_execution_summary_count": len(summaries),
    }


def commit_round_evaluation(context: Any, plan_version: Optional[int] = None, *, runtime: Any | None = None) -> Dict[str, Any]:
    run = context.active_run(plan_version)
    if not run:
        return {}
    payload = evaluate_exhaustion_payload(context, plan_version)
    state = context._run_state(run)
    state.update(
        {
            "execution_round_count": int(payload.get("execution_round_count") or 0),
            "no_progress_round_count": int(payload.get("no_progress_round_count") or 0),
            "last_selected_count": int(payload.get("selected_count") or 0),
            "last_readiness": str(payload.get("readiness") or "unknown").strip() or "unknown",
            "last_gap_signature": payload.get("gap_signature") if isinstance(payload.get("gap_signature"), dict) else {},
            "last_new_unique_candidates": int(payload.get("new_unique_candidates") or 0),
            "last_recommended_action": str(payload.get("recommended_action") or "").strip() or None,
            "processed_execution_summary_count": int(payload.get("processed_execution_summary_count") or 0),
            "last_exhaustion_reason": str(payload.get("decision_reason") or "").strip() or None,
            "last_exhaustion_summary": str(payload.get("decision_summary") or "").strip() or None,
        }
    )
    context.storage.update_ai_search_run(context.task_id, str(run.get("run_id") or ""), human_decision_state=state)
    context.notify_snapshot_changed(runtime, reason="execution_round")
    return payload


def enter_human_decision(context: Any, *, reason: str, summary: str, runtime: Any | None = None) -> None:
    run = context.active_run()
    if not run:
        return
    selected_count = len(context.storage.list_ai_search_documents(context.task_id, str(run.get("run_id") or ""), stages=["selected"]))
    state = context._run_state(run)
    state.update(
        {
            "human_decision_reason": str(reason or "").strip() or None,
            "human_decision_summary": str(summary or "").strip() or None,
            "last_exhaustion_reason": str(reason or "").strip() or None,
            "last_exhaustion_summary": str(summary or "").strip() or None,
        }
    )
    context.storage.update_ai_search_run(
        context.task_id,
        str(run.get("run_id") or ""),
        phase=PHASE_AWAITING_HUMAN_DECISION,
        status=phase_to_task_status(PHASE_AWAITING_HUMAN_DECISION),
        active_retrieval_todo_id=None,
        selected_document_count=selected_count,
        human_decision_state=state,
    )
    context.create_pending_action(
        "human_decision",
        {
            "available": True,
            "reason": str(reason or "").strip(),
            "summary": str(summary or "").strip(),
            "roundCount": int(state.get("execution_round_count") or 0),
            "noProgressRoundCount": int(state.get("no_progress_round_count") or 0),
            "selectedCount": selected_count,
            "recommendedActions": ["continue_search", "complete_current_results"],
        },
        run_id=str(run.get("run_id") or ""),
        plan_version=int(run.get("plan_version") or 0),
        source="execution_exhaustion",
        runtime=runtime,
    )
    context.update_task_phase(
        PHASE_AWAITING_HUMAN_DECISION,
        runtime=runtime,
        active_plan_version=int(run.get("plan_version") or 0),
        run_id=str(run.get("run_id") or ""),
        selected_document_count=selected_count,
    )


def advance_workflow(
    context: Any,
    *,
    action: str,
    plan_version: int = 0,
    todo_id: str = "",
    next_todo_id: str = "",
    next_action: str = "",
    reason: str = "",
    runtime: Any | None = None,
) -> Dict[str, Any]:
    version = int(plan_version or context.active_plan_version() or 0)
    if action == "enter_drafting_plan":
        return enter_drafting_plan(context, runtime=runtime)
    if action == "begin_execution":
        if version <= 0:
            return {"status": "missing_plan"}
        plan = context.storage.get_ai_search_plan(context.task_id, version) or {}
        execution_spec = plan.get("execution_spec_json") if isinstance(plan.get("execution_spec_json"), dict) else {}
        run = context.ensure_run(version, phase=PHASE_EXECUTE_SEARCH)
        if not context._current_todos():
            context.replace_todos(build_execution_todos(version, normalize_execution_plan(execution_spec, context.current_search_elements(version))), runtime=runtime)
        todo = context.first_pending_todo(phase_key=PHASE_EXECUTE_SEARCH) or context.first_pending_todo()
        if not todo:
            return {"status": "no_pending_todos"}
        todo_id = str(todo.get("todo_id") or "").strip()
        phase = phase_from_todo(todo)
        context.update_task_phase(phase, runtime=runtime, active_plan_version=version, run_id=str(run.get("run_id") or ""), current_task=todo_id)
        context.update_todo(todo_id, "in_progress", current_task=todo_id, resume_from="run_execution_step.load", state_updates={"plan_version": version}, runtime=runtime)
        return {"plan_version": version, "todo_id": todo_id, "phase": phase}
    if action == "step_completed":
        current = context._todo_map().get(str(todo_id or "").strip()) if str(todo_id or "").strip() else context.current_todo()
        if not current:
            return {"status": "missing_current_todo"}
        current_id = str(current.get("todo_id") or "").strip()
        context.update_todo(current_id, "completed", current_task=None, runtime=runtime)
        target = current
        activated = build_conditional_todos_for_completed_step(context, version, current_id)
        if activated:
            context.append_todos(activated, current_task=None, runtime=runtime)
            first = activated[0]
            activated_id = str(first.get("todo_id") or "").strip()
            phase = phase_from_todo(first)
            context.update_task_phase(phase, runtime=runtime, active_plan_version=version, run_id=context.active_run_id(version), current_task=activated_id)
            context.update_todo(activated_id, "in_progress", current_task=activated_id, resume_from="run_execution_step.load", state_updates={"plan_version": version}, runtime=runtime)
            return {"completed_todo_id": current_id, "activated_todo_ids": [str(item.get("todo_id") or "").strip() for item in activated], "phase": phase}
        resolved_next_action = str(next_action or "").strip() or "start_next_step"
        if resolved_next_action == "enter_coarse_screen":
            action = "enter_coarse_screen"
        elif resolved_next_action == "enter_close_read":
            action = "enter_close_read"
        elif resolved_next_action == "enter_feature_comparison":
            action = "enter_feature_comparison"
        else:
            target = context._todo_map().get(str(next_todo_id or "").strip()) if str(next_todo_id or "").strip() else context.next_pending_todo(current_id)
            if target:
                next_id = str(target.get("todo_id") or "").strip()
                phase = phase_from_todo(target)
                context.update_task_phase(phase, runtime=runtime, active_plan_version=version, run_id=context.active_run_id(version), current_task=next_id)
                context.update_todo(next_id, "in_progress", current_task=next_id, resume_from="run_execution_step.load", state_updates={"plan_version": version}, runtime=runtime)
                return {"completed_todo_id": current_id, "todo_id": next_id, "phase": phase}
            return {"completed_todo_id": current_id, "next_action": "none"}
    if action == "request_replan":
        current = context.current_todo()
        if not current:
            return {"status": "missing_current_todo"}
        round_evaluation = commit_round_evaluation(context, version, runtime=runtime)
        if bool(round_evaluation.get("should_request_decision")):
            summary = str(round_evaluation.get("decision_summary") or str(reason or "").strip()).strip() or "自动检索已停止，需要人工决策。"
            run = context.active_run(version)
            selected_count = len(context.storage.list_ai_search_documents(context.task_id, version, stages=["selected"])) if version > 0 else 0
            run_state = context._run_state(run)
            payload = {
                "available": True,
                "reason": str(round_evaluation.get("decision_reason") or "no_progress_limit_reached").strip(),
                "summary": summary,
                "roundCount": int(run_state.get("execution_round_count") or round_evaluation.get("execution_round_count") or 0),
                "noProgressRoundCount": int(run_state.get("no_progress_round_count") or round_evaluation.get("no_progress_round_count") or 0),
                "selectedCount": selected_count,
                "recommendedActions": ["continue_search", "complete_current_results"],
            }
            if run:
                updated_state = {
                    **run_state,
                    "human_decision_reason": payload["reason"] or None,
                    "human_decision_summary": payload["summary"] or None,
                    "last_exhaustion_reason": payload["reason"] or None,
                    "last_exhaustion_summary": payload["summary"] or None,
                }
                context.storage.update_ai_search_run(
                    context.task_id,
                    str(run.get("run_id") or ""),
                    phase=PHASE_AWAITING_HUMAN_DECISION,
                    status=phase_to_task_status(PHASE_AWAITING_HUMAN_DECISION),
                    active_retrieval_todo_id=None,
                    selected_document_count=selected_count,
                    human_decision_state=updated_state,
                )
            context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": context.task_id,
                    "plan_version": version or None,
                    "role": "assistant",
                    "kind": "chat",
                    "content": summary,
                    "stream_status": "completed",
                    "metadata": {"reason": round_evaluation.get("decision_reason"), "kind": "human_decision"},
                }
            )
            open_pending_action(
                context,
                action_type="human_decision",
                source="execution_exhaustion",
                payload=payload,
                run_id=context.active_run_id(version),
                plan_version=version,
                runtime=runtime,
            )
            context.update_task_phase(
                PHASE_AWAITING_HUMAN_DECISION,
                runtime=runtime,
                active_plan_version=version,
                run_id=context.active_run_id(version),
                selected_document_count=selected_count,
                current_task=None,
            )
            decision = interrupt(payload)
            normalized_decision = str((decision or {}).get("decision") if isinstance(decision, dict) else decision or "").strip()
            if normalized_decision not in {"continue_search", "complete_current_results"}:
                normalized_decision = "continue_search"
            context.resolve_pending_action(
                "human_decision",
                resolution={"decision": normalized_decision},
                runtime=runtime,
            )
            return {
                "todo_id": str(current.get("todo_id") or "").strip(),
                "reason": round_evaluation.get("decision_reason"),
                "decision": normalized_decision,
                "phase": PHASE_AWAITING_HUMAN_DECISION,
            }
        todo_id = str(current.get("todo_id") or "").strip()
        context.update_todo(todo_id, "paused", current_task=None, last_error=str(reason or "").strip(), resume_from="await_plan_confirmation", state_updates={"replan_requested_at": utc_now_z()}, runtime=runtime)
        if version > 0:
            context.storage.update_ai_search_plan(context.task_id, version, status="superseded", superseded_at=utc_now_z())
        context.update_task_phase("drafting_plan", runtime=runtime, active_plan_version=version, run_id=context.active_run_id(version), current_task=None)
        return {"todo_id": todo_id, "reason": str(reason or "").strip(), "phase": "drafting_plan"}
    if action == "enter_coarse_screen":
        candidate_count = len(context.storage.list_ai_search_documents(context.task_id, version))
        if candidate_count <= 0:
            return {"status": "no_candidates_for_coarse_screen"}
        context.update_task_phase(PHASE_COARSE_SCREEN, runtime=runtime, active_plan_version=version, run_id=context.active_run_id(version), current_task=None)
        return {"plan_version": version, "candidate_count": candidate_count, "phase": PHASE_COARSE_SCREEN}
    if action == "enter_close_read":
        shortlisted_count = len(context.storage.list_ai_search_documents(context.task_id, version, stages=["shortlisted"]))
        if shortlisted_count <= 0:
            return {"status": "no_shortlisted_documents"}
        context.update_task_phase(PHASE_CLOSE_READ, runtime=runtime, active_plan_version=version, run_id=context.active_run_id(version), current_task=None)
        return {"plan_version": version, "shortlisted_count": shortlisted_count, "phase": PHASE_CLOSE_READ}
    if action == "enter_feature_comparison":
        selected_count = len(context.storage.list_ai_search_documents(context.task_id, version, stages=["selected"]))
        if selected_count <= 0:
            return {"status": "no_selected_documents"}
        context.update_task_phase(PHASE_FEATURE_COMPARISON, runtime=runtime, active_plan_version=version, run_id=context.active_run_id(version), current_task=None)
        return {"plan_version": version, "selected_count": selected_count, "phase": PHASE_FEATURE_COMPARISON}
    raise ValueError(f"不支持的 workflow action: {action}")


def complete_session(
    context: Any,
    *,
    summary: str = "",
    plan_version: int = 0,
    force_from_decision: bool = False,
    runtime: Any | None = None,
) -> Dict[str, Any]:
    version = int(plan_version or context.active_plan_version() or 0)
    current_phase = context.current_phase()
    if current_phase == PHASE_FEATURE_COMPARISON and not force_from_decision:
        progress = build_gap_progress(context, version)
        if str(progress.get("recommended_action") or "") == "replan_search":
            return {
                "blocked": True,
                "reason": "gap_replan_required",
                "recommended_action": progress.get("recommended_action"),
                "should_continue_search": progress.get("should_continue_search"),
            }
    selected_count = len(context.storage.list_ai_search_documents(context.task_id, version, stages=["selected"])) if version > 0 else 0
    if summary.strip():
        context.storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": context.task_id,
                "plan_version": version or None,
                "role": "assistant",
                "kind": "chat",
                "content": summary.strip(),
                "stream_status": "completed",
                "metadata": {},
            }
        )
    context.update_task_phase(PHASE_COMPLETED, runtime=runtime, active_plan_version=version or None, run_id=context.active_run_id(version), selected_document_count=selected_count, current_task=None)
    return {"selected_count": selected_count, "phase": PHASE_COMPLETED}
