"""智能检索主控代理定义。"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend

from agents.ai_search.src.checkpointer import AiSearchCheckpointSaver
from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.main_agent.prompt import MAIN_AGENT_SYSTEM_PROMPT
from agents.ai_search.src.runtime import build_guard_middleware, large_model
from agents.ai_search.src.subagents.close_reader import build_close_reader_subagent
from agents.ai_search.src.subagents.coarse_screener import build_coarse_screener_subagent
from agents.ai_search.src.subagents.feature_comparer import build_feature_comparer_subagent
from agents.ai_search.src.subagents.plan_prober import build_plan_prober_subagent
from agents.ai_search.src.subagents.query_executor import build_query_executor_subagent
from agents.ai_search.src.subagents.search_elements import build_search_elements_subagent


def build_main_agent(storage: Any, task_id: str):
    checkpointer = AiSearchCheckpointSaver(storage)
    context = AiSearchAgentContext(storage, task_id)

    return create_deep_agent(
        model=large_model(),
        tools=context.build_main_agent_tools(),
        system_prompt=MAIN_AGENT_SYSTEM_PROMPT,
        middleware=[build_guard_middleware("main-agent", storage, task_id)],
        subagents=[
            build_search_elements_subagent(storage, task_id),
            build_plan_prober_subagent(storage, task_id),
            build_query_executor_subagent(storage, task_id),
            build_coarse_screener_subagent(storage, task_id),
            build_close_reader_subagent(storage, task_id),
            build_feature_comparer_subagent(storage, task_id),
        ],
        checkpointer=checkpointer,
        backend=StateBackend,
        name=f"ai-search-main-agent-{task_id}",
    )
