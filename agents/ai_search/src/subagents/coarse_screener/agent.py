"""Coarse-screener specialist definition."""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime import build_guard_middleware, build_streaming_middleware, default_model
from agents.ai_search.src.subagents.coarse_screener.prompt import COARSE_SCREEN_SYSTEM_PROMPT
from agents.ai_search.src.subagents.coarse_screener.schemas import CoarseScreenOutput


def build_coarse_screener_agent(storage: object | None = None, task_id: str = ""):
    tools = AiSearchAgentContext(storage, task_id).build_coarse_screener_tools() if storage and task_id else []
    return create_deep_agent(
        model=default_model(),
        tools=tools,
        system_prompt=COARSE_SCREEN_SYSTEM_PROMPT,
        middleware=[build_guard_middleware("coarse-screener", storage, task_id), build_streaming_middleware("coarse-screener")],
        response_format=CoarseScreenOutput,
        backend=StateBackend,
        name="ai-search-coarse-screener",
    )


def build_coarse_screener_subagent(storage: object, task_id: str) -> dict:
    context = AiSearchAgentContext(storage, task_id)
    return {
        "name": "coarse-screener",
        "description": "根据标题、摘要、分类号和来源批次对候选文献做轻量粗筛。",
        "system_prompt": COARSE_SCREEN_SYSTEM_PROMPT,
        "model": default_model(),
        "tools": context.build_coarse_screener_tools(),
        "middleware": [build_guard_middleware("coarse-screener", storage, task_id), build_streaming_middleware("coarse-screener")],
    }
