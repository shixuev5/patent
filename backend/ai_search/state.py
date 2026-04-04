"""
Helpers for AI search task metadata and snapshot shaping.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from backend.storage import TaskStatus

from .models import (
    AI_SEARCH_PROGRESS,
    AI_SEARCH_STEP,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_CANCELLED,
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_COMPLETED,
    PHASE_DRAFTING_PLAN,
    PHASE_FAILED,
    PHASE_RESULTS_READY,
    PHASE_SEARCHING,
)


def default_ai_search_meta(thread_id: str) -> Dict[str, Any]:
    return {
        "thread_id": thread_id,
        "current_phase": PHASE_COLLECTING_REQUIREMENTS,
        "active_plan_version": None,
        "pending_question_id": None,
        "pending_confirmation_plan_version": None,
        "selected_document_count": 0,
        "current_feature_table_id": None,
    }


def get_ai_search_meta(task: Any) -> Dict[str, Any]:
    metadata = getattr(task, "metadata", {}) if task else {}
    if not isinstance(metadata, dict):
        metadata = {}
    ai_search = metadata.get("ai_search")
    return deepcopy(ai_search) if isinstance(ai_search, dict) else {}


def merge_ai_search_meta(task: Any, **updates: Any) -> Dict[str, Any]:
    metadata = getattr(task, "metadata", {}) if task else {}
    if not isinstance(metadata, dict):
        metadata = {}
    merged = deepcopy(metadata)
    current = merged.get("ai_search")
    ai_search = deepcopy(current) if isinstance(current, dict) else {}
    for key, value in updates.items():
        ai_search[key] = value
    merged["ai_search"] = ai_search
    return merged


def phase_to_task_status(phase: str) -> str:
    if phase in {PHASE_AWAITING_USER_ANSWER, PHASE_AWAITING_PLAN_CONFIRMATION}:
        return TaskStatus.PAUSED.value
    if phase == PHASE_COMPLETED:
        return TaskStatus.COMPLETED.value
    if phase == PHASE_FAILED:
        return TaskStatus.FAILED.value
    if phase == PHASE_CANCELLED:
        return TaskStatus.CANCELLED.value
    return TaskStatus.PROCESSING.value


def phase_progress(phase: str) -> int:
    return int(AI_SEARCH_PROGRESS.get(phase, AI_SEARCH_PROGRESS[PHASE_COLLECTING_REQUIREMENTS]))


def phase_step(phase: str) -> str:
    return str(AI_SEARCH_STEP.get(phase, AI_SEARCH_STEP[PHASE_COLLECTING_REQUIREMENTS]))


def build_plan_summary(plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(plan, dict):
        return {}
    plan_json = plan.get("plan_json") if isinstance(plan.get("plan_json"), dict) else plan
    query_batches = plan_json.get("query_batches") if isinstance(plan_json, dict) else None
    if not isinstance(query_batches, list):
        query_batches = []
    return {
        "planVersion": plan.get("plan_version") or plan_json.get("plan_version"),
        "objective": plan.get("objective") or plan_json.get("objective"),
        "selectionCriteria": plan_json.get("selection_criteria") if isinstance(plan_json, dict) else None,
        "negativeConstraints": plan_json.get("negative_constraints") if isinstance(plan_json, dict) else None,
        "executionNotes": plan_json.get("execution_notes") if isinstance(plan_json, dict) else None,
        "queryBatches": query_batches,
    }


def latest_search_elements(messages: list[dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for item in reversed(messages):
        if str(item.get("kind") or "") == "search_elements_update":
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                return metadata
    return None
