"""Metadata helpers for conversational AI search sessions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

PHASE_IDLE = "idle"
PHASE_RUNNING = "running"
PHASE_COMPLETED = "completed"
PHASE_FAILED = "failed"
PHASE_CANCELLED = "cancelled"

TASK_STATUS_PROCESSING = "processing"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELLED = "cancelled"

AI_SEARCH_PHASES = {
    PHASE_IDLE,
    PHASE_RUNNING,
    PHASE_COMPLETED,
    PHASE_FAILED,
    PHASE_CANCELLED,
}

AI_SEARCH_PROGRESS = {
    PHASE_IDLE: 5,
    PHASE_RUNNING: 50,
    PHASE_COMPLETED: 100,
    PHASE_FAILED: 100,
    PHASE_CANCELLED: 100,
}

AI_SEARCH_STEP = {
    PHASE_IDLE: "等待检索指令",
    PHASE_RUNNING: "执行检索",
    PHASE_COMPLETED: "会话已完成",
    PHASE_FAILED: "当前轮失败",
    PHASE_CANCELLED: "会话已终止",
}


def default_ai_search_meta(thread_id: str) -> Dict[str, Any]:
    return {
        "thread_id": thread_id,
        "current_phase": PHASE_IDLE,
        "active_plan_version": 1,
        "selected_document_count": 0,
        "pinned": False,
        "current_run_id": None,
        "stop_policy": {},
        "query_count": 0,
        "search_rounds": 0,
        "no_new_result_rounds": 0,
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
    if phase == PHASE_COMPLETED:
        return TASK_STATUS_COMPLETED
    if phase == PHASE_FAILED:
        return TASK_STATUS_FAILED
    if phase == PHASE_CANCELLED:
        return TASK_STATUS_CANCELLED
    return TASK_STATUS_PROCESSING


def phase_progress(phase: str) -> int:
    return int(AI_SEARCH_PROGRESS.get(str(phase or "").strip(), AI_SEARCH_PROGRESS[PHASE_IDLE]))


def phase_step(phase: str) -> str:
    return str(AI_SEARCH_STEP.get(str(phase or "").strip(), AI_SEARCH_STEP[PHASE_IDLE]))
