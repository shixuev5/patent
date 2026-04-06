"""Claim-decomposer specialist definition."""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime import build_guard_middleware, large_model
from agents.ai_search.src.subagents.claim_decomposer.prompt import CLAIM_DECOMPOSER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.claim_decomposer.schemas import ClaimDecompositionOutput


def build_claim_decomposer_agent(storage: object | None = None, task_id: str = ""):
    tools = AiSearchAgentContext(storage, task_id).build_claim_decomposer_tools() if storage and task_id else []
    return create_deep_agent(
        model=large_model(),
        tools=tools,
        system_prompt=CLAIM_DECOMPOSER_SYSTEM_PROMPT,
        middleware=[build_guard_middleware("claim-decomposer", storage, task_id)],
        response_format=ClaimDecompositionOutput,
        backend=StateBackend,
        name="ai-search-claim-decomposer",
    )


def build_claim_decomposer_subagent(storage: object, task_id: str) -> dict:
    context = AiSearchAgentContext(storage, task_id)
    return {
        "name": "claim-decomposer",
        "description": "把结构化权利要求拆成 limitation groups，供后续检索策略和证据对齐使用。",
        "system_prompt": CLAIM_DECOMPOSER_SYSTEM_PROMPT,
        "model": large_model(),
        "tools": context.build_claim_decomposer_tools(),
        "middleware": [build_guard_middleware("claim-decomposer", storage, task_id)],
    }
