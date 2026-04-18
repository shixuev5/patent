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
from agents.ai_search.src.subagents.close_reader.agent import build_close_reader_subagent
from agents.ai_search.src.subagents.coarse_screener.agent import build_coarse_screener_subagent
from agents.ai_search.src.subagents.feature_comparer.agent import build_feature_comparer_subagent
from agents.ai_search.src.subagents.planner.agent import build_planner_subagent
from agents.ai_search.src.subagents.plan_prober.agent import build_plan_prober_subagent
from agents.ai_search.src.subagents.query_executor.agent import build_query_executor_subagent
from agents.ai_search.src.subagents.search_elements.agent import build_search_elements_subagent


def build_main_agent(storage: Any, task_id: str):
    checkpointer = AiSearchCheckpointSaver(storage)

    return create_deep_agent(
        model=large_model(),
        tools=build_main_agent_tools(),
        system_prompt=MAIN_AGENT_SYSTEM_PROMPT,
        middleware=[build_guard_middleware()],
        subagents=[
            build_search_elements_subagent(),
            build_plan_prober_subagent(),
            build_planner_subagent(),
            build_query_executor_subagent(),
            build_coarse_screener_subagent(),
            build_close_reader_subagent(),
            build_feature_comparer_subagent(),
        ],
        checkpointer=checkpointer,
        backend=StateBackend,
        context_schema=AiSearchRuntimeContext,
        name=f"ai-search-main-agent-{task_id}",
    )
