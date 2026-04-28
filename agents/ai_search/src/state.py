"""
Helpers for AI search task metadata and snapshot shaping.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from backend.storage import TaskStatus

PHASE_COLLECTING_REQUIREMENTS = "collecting_requirements"
PHASE_AWAITING_USER_ANSWER = "awaiting_user_answer"
PHASE_DRAFTING_PLAN = "drafting_plan"
PHASE_AWAITING_PLAN_CONFIRMATION = "awaiting_plan_confirmation"
PHASE_EXECUTE_SEARCH = "execute_search"
PHASE_COARSE_SCREEN = "coarse_screen"
PHASE_CLOSE_READ = "close_read"
PHASE_FEATURE_COMPARISON = "feature_comparison"
PHASE_AWAITING_HUMAN_DECISION = "awaiting_human_decision"
PHASE_COMPLETED = "completed"
PHASE_FAILED = "failed"
PHASE_CANCELLED = "cancelled"

AI_SEARCH_PHASES = {
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_DRAFTING_PLAN,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_EXECUTE_SEARCH,
    PHASE_COARSE_SCREEN,
    PHASE_CLOSE_READ,
    PHASE_FEATURE_COMPARISON,
    PHASE_AWAITING_HUMAN_DECISION,
    PHASE_COMPLETED,
    PHASE_FAILED,
    PHASE_CANCELLED,
}

AI_SEARCH_PROGRESS = {
    PHASE_COLLECTING_REQUIREMENTS: 10,
    PHASE_AWAITING_USER_ANSWER: 20,
    PHASE_DRAFTING_PLAN: 35,
    PHASE_AWAITING_PLAN_CONFIRMATION: 35,
    PHASE_EXECUTE_SEARCH: 55,
    PHASE_COARSE_SCREEN: 68,
    PHASE_CLOSE_READ: 82,
    PHASE_FEATURE_COMPARISON: 92,
    PHASE_AWAITING_HUMAN_DECISION: 95,
    PHASE_COMPLETED: 100,
    PHASE_FAILED: 100,
    PHASE_CANCELLED: 100,
}

AI_SEARCH_STEP = {
    PHASE_COLLECTING_REQUIREMENTS: "整理检索需求",
    PHASE_AWAITING_USER_ANSWER: "补全检索要素",
    PHASE_DRAFTING_PLAN: "起草检索计划",
    PHASE_AWAITING_PLAN_CONFIRMATION: "等待计划确认",
    PHASE_EXECUTE_SEARCH: "执行专利检索",
    PHASE_COARSE_SCREEN: "粗筛候选文献",
    PHASE_CLOSE_READ: "精读并提取证据",
    PHASE_FEATURE_COMPARISON: "特征对比分析",
    PHASE_AWAITING_HUMAN_DECISION: "等待人工决策",
    PHASE_COMPLETED: "当前轮完成",
    PHASE_FAILED: "当前轮失败",
    PHASE_CANCELLED: "会话已终止",
}

ACTIVE_EXECUTION_PHASES = {
    PHASE_EXECUTE_SEARCH,
    PHASE_COARSE_SCREEN,
    PHASE_CLOSE_READ,
    PHASE_FEATURE_COMPARISON,
}

MAIN_AGENT_PHASE_TOOL_POLICY: Dict[str, Dict[str, set[str]]] = {
    PHASE_COLLECTING_REQUIREMENTS: {
        "tools": {"get_session_context", "get_planning_context", "start_plan_drafting", "request_user_question"},
        "subagents": {"search-elements"},
    },
    PHASE_DRAFTING_PLAN: {
        "tools": {
            "get_session_context",
            "get_planning_context",
            "start_plan_drafting",
            "publish_planner_draft",
            "request_plan_confirmation",
            "advance_workflow",
            "request_user_question",
        },
        "subagents": {"search-elements", "plan-prober", "planner"},
    },
    PHASE_AWAITING_USER_ANSWER: {
        "tools": {"request_user_question"},
        "subagents": set(),
    },
    PHASE_AWAITING_PLAN_CONFIRMATION: {
        "tools": {"request_plan_confirmation"},
        "subagents": set(),
    },
    PHASE_EXECUTE_SEARCH: {
        "tools": {
            "get_session_context",
            "get_execution_context",
            "start_plan_drafting",
            "advance_workflow",
            "complete_session",
        },
        "subagents": {"query-executor"},
    },
    PHASE_COARSE_SCREEN: {
        "tools": {"get_session_context", "get_execution_context", "start_plan_drafting", "advance_workflow", "complete_session"},
        "subagents": {"coarse-screener"},
    },
    PHASE_CLOSE_READ: {
        "tools": {"get_session_context", "get_execution_context", "start_plan_drafting", "advance_workflow", "complete_session"},
        "subagents": {"close-reader"},
    },
    PHASE_FEATURE_COMPARISON: {
        "tools": {
            "get_session_context",
            "get_execution_context",
            "complete_session",
            "advance_workflow",
            "start_plan_drafting",
            "request_human_decision",
        },
        "subagents": {"feature-comparer"},
    },
    PHASE_AWAITING_HUMAN_DECISION: {
        "tools": {"get_session_context", "get_planning_context", "start_plan_drafting", "complete_session"},
        "subagents": set(),
    },
    PHASE_COMPLETED: {
        "tools": {"get_session_context", "get_execution_context"},
        "subagents": set(),
    },
    PHASE_FAILED: {
        "tools": set(),
        "subagents": set(),
    },
    PHASE_CANCELLED: {
        "tools": set(),
        "subagents": set(),
    },
}

ROLE_PHASE_TOOL_POLICY: Dict[str, Dict[str, set[str]]] = {
    "search-elements": {
        PHASE_COLLECTING_REQUIREMENTS: {"save_search_elements"},
        PHASE_DRAFTING_PLAN: {"save_search_elements"},
    },
    "query-executor": {
        PHASE_EXECUTE_SEARCH: {
            "run_execution_step",
            "search_trace",
            "search_semantic",
            "search_boolean",
            "count_boolean",
            "fetch_patent_details",
            "prepare_lane_queries",
        },
    },
    "plan-prober": {
        PHASE_DRAFTING_PLAN: {
            "probe_search_semantic",
            "probe_search_boolean",
            "probe_count_boolean",
            "save_probe_findings",
        },
    },
    "planner": {
        PHASE_DRAFTING_PLAN: {"save_planner_draft"},
    },
    "coarse-screener": {
        PHASE_COARSE_SCREEN: {"run_coarse_screen_batch"},
    },
    "close-reader": {
        PHASE_CLOSE_READ: {"run_close_read_batch", "ls", "read_file", "glob", "grep"},
    },
    "feature-comparer": {
        PHASE_FEATURE_COMPARISON: {"run_feature_compare"},
    },
}


def default_ai_search_meta(thread_id: str) -> Dict[str, Any]:
    return {
        "thread_id": thread_id,
        "current_phase": PHASE_COLLECTING_REQUIREMENTS,
        "active_plan_version": None,
        "selected_document_count": 0,
        "planner_draft": None,
        "draft_todos": [],
        "pinned": False,
        "current_run_id": None,
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
    if phase in {PHASE_AWAITING_USER_ANSWER, PHASE_AWAITING_PLAN_CONFIRMATION, PHASE_AWAITING_HUMAN_DECISION}:
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
    return {
        "planVersion": int(plan.get("plan_version") or 0),
        "status": str(plan.get("status") or "").strip(),
        "reviewMarkdown": str(plan.get("review_markdown") or "").strip(),
    }


def latest_search_elements(messages: list[dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for item in reversed(messages):
        if str(item.get("kind") or "") == "search_elements_update":
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                return metadata
    return None


def allowed_main_agent_tools(phase: str) -> set[str]:
    policy = MAIN_AGENT_PHASE_TOOL_POLICY.get(phase) or {}
    return set(policy.get("tools") or set())


def allowed_main_agent_subagents(phase: str) -> set[str]:
    policy = MAIN_AGENT_PHASE_TOOL_POLICY.get(phase) or {}
    return set(policy.get("subagents") or set())


def allowed_role_tools(role: str, phase: str) -> Optional[set[str]]:
    role_policy = ROLE_PHASE_TOOL_POLICY.get(role)
    if role_policy is None:
        return None
    if phase in role_policy:
        return set(role_policy[phase])
    return set()
