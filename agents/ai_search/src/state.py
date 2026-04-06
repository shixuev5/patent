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
PHASE_CLAIM_DECOMPOSITION = "claim_decomposition"
PHASE_SEARCH_STRATEGY = "search_strategy"
PHASE_AWAITING_PLAN_CONFIRMATION = "awaiting_plan_confirmation"
PHASE_EXECUTE_SEARCH = "execute_search"
PHASE_COARSE_SCREEN = "coarse_screen"
PHASE_CLOSE_READ = "close_read"
PHASE_GENERATE_FEATURE_TABLE = "generate_feature_table"
PHASE_COMPLETED = "completed"
PHASE_FAILED = "failed"
PHASE_CANCELLED = "cancelled"

PHASE_SEARCHING = PHASE_EXECUTE_SEARCH
PHASE_RESULTS_READY = PHASE_GENERATE_FEATURE_TABLE

SEARCH_MODE_TOPIC = "topic_search"
SEARCH_MODE_CLAIM_AWARE = "claim_aware_search"

AI_SEARCH_MODES = {
    SEARCH_MODE_TOPIC,
    SEARCH_MODE_CLAIM_AWARE,
}

AI_SEARCH_PHASES = {
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_DRAFTING_PLAN,
    PHASE_CLAIM_DECOMPOSITION,
    PHASE_SEARCH_STRATEGY,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_EXECUTE_SEARCH,
    PHASE_COARSE_SCREEN,
    PHASE_CLOSE_READ,
    PHASE_GENERATE_FEATURE_TABLE,
    PHASE_COMPLETED,
    PHASE_FAILED,
    PHASE_CANCELLED,
}

AI_SEARCH_PROGRESS = {
    PHASE_COLLECTING_REQUIREMENTS: 10,
    PHASE_AWAITING_USER_ANSWER: 20,
    PHASE_DRAFTING_PLAN: 35,
    PHASE_CLAIM_DECOMPOSITION: 40,
    PHASE_SEARCH_STRATEGY: 45,
    PHASE_AWAITING_PLAN_CONFIRMATION: 35,
    PHASE_EXECUTE_SEARCH: 55,
    PHASE_COARSE_SCREEN: 68,
    PHASE_CLOSE_READ: 82,
    PHASE_GENERATE_FEATURE_TABLE: 92,
    PHASE_COMPLETED: 100,
    PHASE_FAILED: 100,
    PHASE_CANCELLED: 100,
}

AI_SEARCH_STEP = {
    PHASE_COLLECTING_REQUIREMENTS: "整理检索需求",
    PHASE_AWAITING_USER_ANSWER: "补全检索要素",
    PHASE_DRAFTING_PLAN: "起草检索计划",
    PHASE_CLAIM_DECOMPOSITION: "拆解权利要求限制",
    PHASE_SEARCH_STRATEGY: "生成权项检索策略",
    PHASE_AWAITING_PLAN_CONFIRMATION: "等待计划确认",
    PHASE_EXECUTE_SEARCH: "执行专利检索",
    PHASE_COARSE_SCREEN: "粗筛候选文献",
    PHASE_CLOSE_READ: "精读并提取证据",
    PHASE_GENERATE_FEATURE_TABLE: "生成特征对比表",
    PHASE_COMPLETED: "当前轮完成",
    PHASE_FAILED: "当前轮失败",
    PHASE_CANCELLED: "会话已终止",
}

ACTIVE_EXECUTION_PHASES = {
    PHASE_EXECUTE_SEARCH,
    PHASE_COARSE_SCREEN,
    PHASE_CLOSE_READ,
    PHASE_GENERATE_FEATURE_TABLE,
}

MAIN_AGENT_PHASE_TOOL_POLICY: Dict[str, Dict[str, set[str]]] = {
    PHASE_COLLECTING_REQUIREMENTS: {
        "tools": {"read_todos", "write_todos", "get_search_elements", "get_gap_context", "evaluate_gap_progress", "start_claim_decomposition", "start_plan_drafting", "ask_user_question"},
        "subagents": {"search-elements"},
    },
    PHASE_CLAIM_DECOMPOSITION: {
        "tools": {"read_todos", "write_todos", "get_search_elements", "get_claim_context", "get_gap_context", "evaluate_gap_progress", "start_search_strategy", "start_plan_drafting", "ask_user_question"},
        "subagents": {"claim-decomposer", "search-elements"},
    },
    PHASE_SEARCH_STRATEGY: {
        "tools": {"read_todos", "write_todos", "get_search_elements", "get_claim_context", "get_gap_context", "evaluate_gap_progress", "start_plan_drafting", "ask_user_question"},
        "subagents": {"claim-search-strategist", "search-elements"},
    },
    PHASE_DRAFTING_PLAN: {
        "tools": {
            "read_todos",
            "write_todos",
            "get_search_elements",
            "get_claim_context",
            "get_gap_context",
            "evaluate_gap_progress",
            "start_claim_decomposition",
            "start_search_strategy",
            "start_plan_drafting",
            "save_search_plan",
            "request_plan_confirmation",
            "begin_execution",
            "ask_user_question",
        },
        "subagents": {"search-elements", "claim-decomposer", "claim-search-strategist"},
    },
    PHASE_AWAITING_USER_ANSWER: {
        "tools": {"ask_user_question"},
        "subagents": set(),
    },
    PHASE_AWAITING_PLAN_CONFIRMATION: {
        "tools": {"request_plan_confirmation"},
        "subagents": set(),
    },
    PHASE_EXECUTE_SEARCH: {
        "tools": {"read_todos", "get_execution_state", "get_gap_context", "evaluate_gap_progress", "decide_search_transition", "list_documents", "start_coarse_screen", "start_plan_drafting", "complete_execution"},
        "subagents": {"query-executor"},
    },
    PHASE_COARSE_SCREEN: {
        "tools": {"read_todos", "get_execution_state", "get_gap_context", "evaluate_gap_progress", "list_documents", "start_close_read", "start_plan_drafting", "complete_execution"},
        "subagents": {"coarse-screener"},
    },
    PHASE_CLOSE_READ: {
        "tools": {"read_todos", "get_execution_state", "get_gap_context", "evaluate_gap_progress", "list_documents", "start_feature_table_generation", "start_plan_drafting", "complete_execution"},
        "subagents": {"close-reader"},
    },
    PHASE_GENERATE_FEATURE_TABLE: {
        "tools": {"read_todos", "get_execution_state", "get_gap_context", "evaluate_gap_progress", "list_documents", "complete_execution", "start_plan_drafting", "start_search_strategy"},
        "subagents": {"feature-comparer"},
    },
    PHASE_COMPLETED: {
        "tools": {"read_todos", "get_execution_state", "get_gap_context", "evaluate_gap_progress", "list_documents"},
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
        PHASE_CLAIM_DECOMPOSITION: {"save_search_elements"},
        PHASE_SEARCH_STRATEGY: {"save_search_elements"},
    },
    "claim-decomposer": {
        PHASE_COLLECTING_REQUIREMENTS: {"load_structured_claims", "expand_claim_dependency", "build_claim_packets", "save_claim_decomposition"},
        PHASE_CLAIM_DECOMPOSITION: {"load_structured_claims", "expand_claim_dependency", "build_claim_packets", "save_claim_decomposition"},
        PHASE_DRAFTING_PLAN: {"load_structured_claims", "expand_claim_dependency", "build_claim_packets", "save_claim_decomposition"},
    },
    "claim-search-strategist": {
        PHASE_SEARCH_STRATEGY: {"get_claim_context", "get_gap_context", "build_gap_strategy_seed", "save_claim_search_strategy"},
        PHASE_DRAFTING_PLAN: {"get_claim_context", "get_gap_context", "build_gap_strategy_seed", "save_claim_search_strategy"},
        PHASE_GENERATE_FEATURE_TABLE: {"get_claim_context", "get_gap_context", "build_gap_strategy_seed", "save_claim_search_strategy"},
    },
    "query-executor": {
        PHASE_EXECUTE_SEARCH: {
            "run_search_round",
            "search_trace",
            "search_semantic",
            "search_boolean",
            "count_boolean",
            "fetch_patent_details",
            "prepare_lane_queries",
        },
    },
    "coarse-screener": {
        PHASE_COARSE_SCREEN: {"run_coarse_screen_batch"},
    },
    "close-reader": {
        PHASE_CLOSE_READ: {"run_close_read_batch", "ls", "read_file", "glob", "grep"},
    },
    "feature-comparer": {
        PHASE_GENERATE_FEATURE_TABLE: {"run_feature_compare"},
    },
}

TOPIC_MODE_BLOCKED_MAIN_AGENT_TOOLS = {
    "get_claim_context",
    "start_claim_decomposition",
    "start_search_strategy",
}

TOPIC_MODE_BLOCKED_MAIN_AGENT_SUBAGENTS = {
    "claim-decomposer",
    "claim-search-strategist",
}

CLAIM_AWARE_PHASES = {
    PHASE_CLAIM_DECOMPOSITION,
    PHASE_SEARCH_STRATEGY,
}


def default_ai_search_meta(thread_id: str) -> Dict[str, Any]:
    return {
        "thread_id": thread_id,
        "current_phase": PHASE_COLLECTING_REQUIREMENTS,
        "search_mode": SEARCH_MODE_TOPIC,
        "active_plan_version": None,
        "pending_question_id": None,
        "pending_confirmation_plan_version": None,
        "selected_document_count": 0,
        "current_feature_table_id": None,
        "todos": [],
        "current_task": None,
        "pinned": False,
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


def get_ai_search_mode(task: Any) -> str:
    meta = get_ai_search_meta(task)
    explicit_mode = str(meta.get("search_mode") or "").strip()
    if explicit_mode in AI_SEARCH_MODES:
        return explicit_mode
    phase = str(meta.get("current_phase") or "").strip()
    if phase in CLAIM_AWARE_PHASES:
        return SEARCH_MODE_CLAIM_AWARE
    return SEARCH_MODE_TOPIC


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


def allowed_main_agent_tools(phase: str, search_mode: str = SEARCH_MODE_TOPIC) -> set[str]:
    policy = MAIN_AGENT_PHASE_TOOL_POLICY.get(phase) or {}
    tools = set(policy.get("tools") or set())
    if search_mode == SEARCH_MODE_TOPIC:
        tools.difference_update(TOPIC_MODE_BLOCKED_MAIN_AGENT_TOOLS)
    return tools


def allowed_main_agent_subagents(phase: str, search_mode: str = SEARCH_MODE_TOPIC) -> set[str]:
    policy = MAIN_AGENT_PHASE_TOOL_POLICY.get(phase) or {}
    subagents = set(policy.get("subagents") or set())
    if search_mode == SEARCH_MODE_TOPIC:
        subagents.difference_update(TOPIC_MODE_BLOCKED_MAIN_AGENT_SUBAGENTS)
    return subagents


def allowed_role_tools(role: str, phase: str) -> Optional[set[str]]:
    role_policy = ROLE_PHASE_TOOL_POLICY.get(role)
    if role_policy is None:
        return None
    if phase in role_policy:
        return set(role_policy[phase])
    return set()
