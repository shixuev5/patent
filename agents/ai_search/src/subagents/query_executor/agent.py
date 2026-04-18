"""检索执行子代理定义。"""

from __future__ import annotations

from typing import Any

from agents.ai_search.src.execution_state import ExecutionStepSummary
from agents.ai_search.src.subagents.common import StructuredPersistingSubagent
from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.runtime import build_guard_middleware, default_model
from agents.ai_search.src.subagents.query_executor.prompt import QUERY_EXECUTOR_SYSTEM_PROMPT
from agents.ai_search.src.subagents.query_executor.search_backend_tools import build_search_tools
from agents.ai_search.src.subagents.query_executor.tools import build_query_executor_tools


def _persist_execution_summary(context: object, output: ExecutionStepSummary, *, runtime: Any | None = None) -> None:
    context.persist_execution_step_summary(output.model_dump(mode="python"), runtime=runtime)


def build_query_executor_subagent() -> dict:
    return {
        "name": "query-executor",
        "description": "根据执行指令动态执行追踪检索、语义检索和布尔检索，并只返回摘要状态。",
        "runnable": StructuredPersistingSubagent(
            name="query-executor",
            model=default_model(),
            system_prompt=QUERY_EXECUTOR_SYSTEM_PROMPT,
            response_format=ExecutionStepSummary,
            persist_result=_persist_execution_summary,
            tools=build_query_executor_tools() + build_search_tools(),
            middleware=[build_guard_middleware()],
            context_schema=AiSearchRuntimeContext,
        ),
    }
