"""检索执行子代理定义。"""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime import build_guard_middleware, build_streaming_middleware, default_model
from agents.ai_search.src.subagents.query_executor.prompt import QUERY_EXECUTOR_SYSTEM_PROMPT
from agents.ai_search.src.subagents.query_executor.schemas import ExecutionStepSummary
from agents.ai_search.src.subagents.query_executor.search_backend_tools import build_search_tools


def build_query_executor_agent(storage: object, task_id: str):
    context = AiSearchAgentContext(storage, task_id)
    return create_deep_agent(
        model=default_model(),
        tools=context.build_query_executor_tools() + build_search_tools(context),
        system_prompt=QUERY_EXECUTOR_SYSTEM_PROMPT,
        middleware=[build_guard_middleware("query-executor", storage, task_id), build_streaming_middleware("query-executor")],
        response_format=ExecutionStepSummary,
        backend=StateBackend,
        name=f"ai-search-query-executor-{task_id}",
    )


def build_query_executor_subagent(storage: object, task_id: str) -> dict:
    context = AiSearchAgentContext(storage, task_id)
    return {
        "name": "query-executor",
        "description": "根据执行指令动态执行追踪检索、语义检索和布尔检索，并只返回摘要状态。",
        "system_prompt": QUERY_EXECUTOR_SYSTEM_PROMPT,
        "model": default_model(),
        "tools": context.build_query_executor_tools() + build_search_tools(context),
        "middleware": [build_guard_middleware("query-executor", storage, task_id), build_streaming_middleware("query-executor")],
    }
