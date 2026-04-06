"""Claim-search-strategist specialist definition."""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime import build_guard_middleware, large_model
from agents.ai_search.src.subagents.claim_search_strategist.prompt import CLAIM_SEARCH_STRATEGIST_SYSTEM_PROMPT
from agents.ai_search.src.subagents.claim_search_strategist.schemas import ClaimSearchStrategyOutput


def build_claim_search_strategist_agent(storage: object | None = None, task_id: str = ""):
    tools = AiSearchAgentContext(storage, task_id).build_claim_search_strategist_tools() if storage and task_id else []
    return create_deep_agent(
        model=large_model(),
        tools=tools,
        system_prompt=CLAIM_SEARCH_STRATEGIST_SYSTEM_PROMPT,
        middleware=[build_guard_middleware("claim-search-strategist", storage, task_id)],
        response_format=ClaimSearchStrategyOutput,
        backend=StateBackend,
        name="ai-search-claim-search-strategist",
    )


def build_claim_search_strategist_subagent(storage: object, task_id: str) -> dict:
    context = AiSearchAgentContext(storage, task_id)
    return {
        "name": "claim-search-strategist",
        "description": "根据 claim limitation groups 规划 claim-aware 检索策略和 batch 结构。",
        "system_prompt": CLAIM_SEARCH_STRATEGIST_SYSTEM_PROMPT,
        "model": large_model(),
        "tools": context.build_claim_search_strategist_tools(),
        "middleware": [build_guard_middleware("claim-search-strategist", storage, task_id)],
    }
