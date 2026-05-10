"""Unified pending-action state machine helpers."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, Optional

from agents.ai_search.src.state import (
    PHASE_AWAITING_HUMAN_DECISION,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
)
from backend.time_utils import utc_now_z

PENDING_ACTION_STATUS_PENDING = "pending"
PENDING_ACTION_TERMINAL_STATUSES = {"resolved", "cancelled", "superseded", "expired"}
PENDING_ACTION_PHASE_MAP = {
    "question": PHASE_AWAITING_USER_ANSWER,
    "plan_confirmation": PHASE_AWAITING_PLAN_CONFIRMATION,
    "human_decision": PHASE_AWAITING_HUMAN_DECISION,
}


def _storage_and_task_id(target: Any, task_id: str = "") -> tuple[Any, str]:
    storage = getattr(target, "storage", target)
    resolved_task_id = str(task_id or getattr(target, "task_id", "") or "").strip()
    if not resolved_task_id:
        raise ValueError("pending action requires task_id")
    return storage, resolved_task_id


def _normalized_payload(payload: Any) -> Dict[str, Any]:
    return dict(payload) if isinstance(payload, dict) else {}


def current_pending_action(target: Any, *, task_id: str = "", statuses: Optional[Iterable[str]] = None) -> Optional[Dict[str, Any]]:
    storage, resolved_task_id = _storage_and_task_id(target, task_id)
    active_statuses = [str(item or "").strip() for item in (statuses or [PENDING_ACTION_STATUS_PENDING]) if str(item or "").strip()]
    if hasattr(storage, "get_current_ai_search_pending_action"):
        return storage.get_current_ai_search_pending_action(resolved_task_id, statuses=active_statuses)
    for action_type in ("question", "plan_confirmation", "human_decision", "resume"):
        pending = storage.get_ai_search_pending_action(resolved_task_id, action_type, status=PENDING_ACTION_STATUS_PENDING)
        if pending:
            return pending
    return None


def get_pending_action(target: Any, action_id: str) -> Optional[Dict[str, Any]]:
    storage, _ = _storage_and_task_id(target)
    if hasattr(storage, "get_ai_search_pending_action_by_id"):
        return storage.get_ai_search_pending_action_by_id(action_id)
    return None


def update_pending_action(target: Any, action_id: str, **updates: Any) -> bool:
    storage, _ = _storage_and_task_id(target)
    if hasattr(storage, "update_ai_search_pending_action"):
        return bool(storage.update_ai_search_pending_action(action_id, **updates))
    if "status" in updates:
        return bool(storage.resolve_ai_search_pending_action(action_id, status=str(updates.get("status") or "resolved")))
    return False


def sync_phase_from_pending_action(
    context: Any,
    action: Optional[Dict[str, Any]] = None,
    *,
    runtime: Any | None = None,
) -> Optional[str]:
    pending = action if isinstance(action, dict) else current_pending_action(context)
    if not pending or str(pending.get("status") or "").strip() != PENDING_ACTION_STATUS_PENDING:
        return None
    next_phase = PENDING_ACTION_PHASE_MAP.get(str(pending.get("action_type") or "").strip())
    if not next_phase:
        return None
    resolved_plan_version = int(pending.get("plan_version") or 0) or None
    if not resolved_plan_version and str(pending.get("action_type") or "").strip() != "plan_confirmation":
        resolved_plan_version = int(context.active_plan_version() or 0) or None
    context.update_task_phase(
        next_phase,
        runtime=runtime,
        active_plan_version=resolved_plan_version,
        run_id=str(pending.get("run_id") or "").strip() or None,
    )
    return next_phase


def open_pending_action(
    context: Any,
    *,
    action_type: str,
    source: str,
    payload: Dict[str, Any],
    run_id: str = "",
    plan_version: int = 0,
    runtime: Any | None = None,
) -> Dict[str, Any]:
    resolved_type = str(action_type or "").strip()
    resolved_source = str(source or "").strip()
    resolved_payload = _normalized_payload(payload)
    resolved_run_id = str(run_id or "").strip() or None
    resolved_plan_version = int(plan_version or 0) or None
    pending = current_pending_action(context)
    if pending:
        same_pending = (
            str(pending.get("action_type") or "").strip() == resolved_type
            and str(pending.get("run_id") or "").strip() == str(resolved_run_id or "")
            and int(pending.get("plan_version") or 0) == int(resolved_plan_version or 0)
            and _normalized_payload(pending.get("payload")) == resolved_payload
        )
        if same_pending:
            return pending
    action_id = uuid.uuid4().hex[:12]
    if pending:
        supersede_pending_action(context, action_id=str(pending.get("action_id") or ""), superseded_by=action_id)
    record = {
        "action_id": action_id,
        "task_id": context.task_id,
        "run_id": resolved_run_id,
        "plan_version": resolved_plan_version,
        "action_type": resolved_type,
        "status": PENDING_ACTION_STATUS_PENDING,
        "source": resolved_source,
        "payload": resolved_payload,
        "resolution": {},
        "created_at": utc_now_z(),
    }
    context.storage.create_ai_search_pending_action(record)
    created = get_pending_action(context, action_id) or record
    sync_phase_from_pending_action(context, created, runtime=runtime)
    context.notify_snapshot_changed(runtime, reason="pending_action")
    return created


def resolve_pending_action(
    context: Any,
    *,
    action_id: str = "",
    expected_action_type: str = "",
    resolution: Optional[Dict[str, Any]] = None,
    status: str = "resolved",
    runtime: Any | None = None,
) -> Optional[Dict[str, Any]]:
    action = get_pending_action(context, action_id) if str(action_id or "").strip() else current_pending_action(context)
    if not action:
        return None
    if expected_action_type and str(action.get("action_type") or "").strip() != str(expected_action_type or "").strip():
        raise ValueError(f"pending action type mismatch: expected {expected_action_type}")
    if str(action.get("status") or "").strip() != PENDING_ACTION_STATUS_PENDING:
        return action
    update_pending_action(
        context,
        str(action.get("action_id") or ""),
        status=str(status or "resolved").strip() or "resolved",
        resolution=_normalized_payload(resolution),
        resolved_at=utc_now_z(),
        superseded_by=None,
    )
    context.notify_snapshot_changed(runtime, reason="pending_action")
    return get_pending_action(context, str(action.get("action_id") or "")) or action


def supersede_pending_action(
    context: Any,
    *,
    action_id: str = "",
    superseded_by: str = "",
    runtime: Any | None = None,
) -> Optional[Dict[str, Any]]:
    action = get_pending_action(context, action_id) if str(action_id or "").strip() else current_pending_action(context)
    if not action or str(action.get("status") or "").strip() != PENDING_ACTION_STATUS_PENDING:
        return action
    update_pending_action(
        context,
        str(action.get("action_id") or ""),
        status="superseded",
        resolved_at=utc_now_z(),
        superseded_by=str(superseded_by or "").strip() or None,
    )
    context.notify_snapshot_changed(runtime, reason="pending_action")
    return get_pending_action(context, str(action.get("action_id") or "")) or action


def cancel_pending_action(
    context: Any,
    *,
    action_id: str = "",
    runtime: Any | None = None,
) -> Optional[Dict[str, Any]]:
    action = get_pending_action(context, action_id) if str(action_id or "").strip() else current_pending_action(context)
    if not action or str(action.get("status") or "").strip() != PENDING_ACTION_STATUS_PENDING:
        return action
    update_pending_action(
        context,
        str(action.get("action_id") or ""),
        status="cancelled",
        resolved_at=utc_now_z(),
        superseded_by=None,
    )
    context.notify_snapshot_changed(runtime, reason="pending_action")
    return get_pending_action(context, str(action.get("action_id") or "")) or action


def build_pending_action_view(action: Optional[Dict[str, Any]], *, camel_case: bool = False) -> Optional[Dict[str, Any]]:
    if not isinstance(action, dict):
        return None
    payload = _normalized_payload(action.get("payload"))
    plan_version = int(action.get("plan_version") or payload.get("plan_version") or payload.get("planVersion") or 0) or None
    run_id = str(action.get("run_id") or payload.get("run_id") or payload.get("runId") or "").strip() or None
    todo_id = str(payload.get("todo_id") or payload.get("todoId") or "").strip() or None
    base = {
        "action_id": str(action.get("action_id") or "").strip() or None,
        "action_type": str(action.get("action_type") or "").strip() or None,
        "source": str(action.get("source") or "").strip() or None,
        "status": str(action.get("status") or "").strip() or None,
        "payload": payload,
        "plan_version": plan_version,
        "run_id": run_id,
        "todo_id": todo_id,
        "created_at": action.get("created_at"),
        "updated_at": action.get("updated_at"),
        "resolved_at": action.get("resolved_at"),
    }
    if not camel_case:
        return base
    view = {
        "actionId": base["action_id"],
        "actionType": base["action_type"],
        "source": base["source"],
        "status": base["status"],
        "payload": payload,
        "planVersion": base["plan_version"],
        "runId": base["run_id"],
        "todoId": base["todo_id"],
        "createdAt": base["created_at"],
        "updatedAt": base["updated_at"],
        "resolvedAt": base["resolved_at"],
    }
    return {**payload, **view}
