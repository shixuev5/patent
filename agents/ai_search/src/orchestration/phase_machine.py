"""Phase transitions and routing helpers."""

from __future__ import annotations

from typing import Any, Dict

from agents.ai_search.src.orchestration.action_runtime import supersede_pending_action
from agents.ai_search.src.state import (
    PHASE_CLOSE_READ,
    PHASE_DRAFTING_PLAN,
    PHASE_EXECUTE_SEARCH,
    PHASE_FEATURE_COMPARISON,
)


def phase_from_todo(todo: Dict[str, Any] | None) -> str:
    phase_key = str((todo or {}).get("phase_key") or "").strip()
    if phase_key == PHASE_CLOSE_READ:
        return PHASE_CLOSE_READ
    if phase_key == PHASE_FEATURE_COMPARISON:
        return PHASE_FEATURE_COMPARISON
    return PHASE_EXECUTE_SEARCH


def enter_drafting_plan(context: Any, *, runtime: Any | None = None, clear_pending_actions: bool = True) -> Dict[str, Any]:
    if clear_pending_actions:
        context.clear_planner_draft(runtime=runtime)
        supersede_pending_action(context, runtime=runtime)
    context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime)
    return {"phase": PHASE_DRAFTING_PLAN}
