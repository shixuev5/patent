"""智能检索主控代理定义。"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend

from agents.ai_search.src.checkpointer import AiSearchCheckpointSaver
from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.main_agent.tools import build_main_agent_tools
from agents.ai_search.src.main_agent.prompt import MAIN_AGENT_SYSTEM_PROMPT
from agents.ai_search.src.runtime import build_guard_middleware, large_model


def build_main_agent(storage: Any, task_id: str):
    checkpointer = AiSearchCheckpointSaver(storage)

    return create_deep_agent(
        model=large_model(),
        tools=build_main_agent_tools(),
        system_prompt=MAIN_AGENT_SYSTEM_PROMPT,
        middleware=[build_guard_middleware()],
        checkpointer=checkpointer,
        backend=StateBackend,
        context_schema=AiSearchRuntimeContext,
        name=f"ai-search-main-agent-{task_id}",
    )
