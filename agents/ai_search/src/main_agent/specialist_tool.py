"""Project-owned specialist dispatcher for the AI Search main agent."""

from __future__ import annotations

from typing import Any, Dict, List, cast

from deepagents.backends.state import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain.tools import ToolRuntime
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field

from agents.ai_search.src.runtime import extract_latest_ai_message
from agents.ai_search.src.subagents.close_reader.agent import build_close_reader_subagent
from agents.ai_search.src.subagents.coarse_screener.agent import build_coarse_screener_subagent
from agents.ai_search.src.subagents.feature_comparer.agent import build_feature_comparer_subagent
from agents.ai_search.src.subagents.query_executor.agent import build_query_executor_subagent

_EXCLUDED_STATE_KEYS = {"messages", "structured_response", "memory_contents", "todos", "skills_metadata"}


class SearchSpecialistTaskInput(BaseModel):
    description: str = Field(..., description="给 specialist 的任务说明。")
    specialist_type: str = Field(..., description="允许值：query-executor、coarse-screener、close-reader、feature-comparer。")


def _specialist_specs() -> List[Dict[str, Any]]:
    return [
        build_query_executor_subagent(),
        build_coarse_screener_subagent(),
        build_close_reader_subagent(),
        build_feature_comparer_subagent(),
    ]


def _compile_specialist(spec: Dict[str, Any]) -> Runnable:
    model = spec["model"]
    middleware = [
        TodoListMiddleware(),
        FilesystemMiddleware(backend=StateBackend),
        SummarizationMiddleware(model=model, backend=StateBackend),
        PatchToolCallsMiddleware(),
        *list(spec.get("middleware", [])),
        AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
    ]
    return create_agent(
        model,
        system_prompt=str(spec.get("system_prompt") or ""),
        tools=spec.get("tools") or [],
        middleware=middleware,
        name=str(spec.get("name") or ""),
        context_schema=spec.get("context_schema"),
    )


def build_search_specialist_tool() -> Any:
    specs = {str(spec["name"]): spec for spec in _specialist_specs()}
    specialists: Dict[str, Runnable] = {}
    descriptions = "\n".join(f"- {spec['name']}: {spec['description']}" for spec in specs.values())

    def _get_specialist(name: str) -> Runnable:
        if name not in specs:
            allowed = ", ".join(f"`{item}`" for item in specs)
            raise ValueError(f"未知 specialist `{name}`，允许值为 {allowed}。")
        if name not in specialists:
            specialists[name] = _compile_specialist(specs[name])
        return specialists[name]

    def _state_for_specialist(description: str, runtime: ToolRuntime) -> Dict[str, Any]:
        return {
            **{
                key: value
                for key, value in runtime.state.items()
                if key not in _EXCLUDED_STATE_KEYS
            },
            "messages": [HumanMessage(content=description)],
        }

    def _to_command(result: Dict[str, Any], tool_call_id: str) -> Command:
        if "messages" not in result:
            raise ValueError("specialist 必须返回包含 messages 的状态。")
        state_update = {key: value for key, value in result.items() if key not in _EXCLUDED_STATE_KEYS}
        content = extract_latest_ai_message(result)
        return Command(update={**state_update, "messages": [ToolMessage(content, tool_call_id=tool_call_id)]})

    def run_search_specialist(
        description: str,
        specialist_type: str,
        runtime: ToolRuntime,
    ) -> Command:
        """调度执行阶段 specialist，并把结果作为工具返回给主 agent。"""
        if not runtime.tool_call_id:
            raise ValueError("specialist 调用缺少 tool_call_id。")
        specialist = _get_specialist(str(specialist_type or "").strip())
        result = specialist.invoke(_state_for_specialist(description, runtime), context=runtime.context)
        return _to_command(cast(Dict[str, Any], result), runtime.tool_call_id)

    async def arun_search_specialist(
        description: str,
        specialist_type: str,
        runtime: ToolRuntime,
    ) -> Command:
        """异步调度执行阶段 specialist，并把结果作为工具返回给主 agent。"""
        if not runtime.tool_call_id:
            raise ValueError("specialist 调用缺少 tool_call_id。")
        specialist = _get_specialist(str(specialist_type or "").strip())
        result = await specialist.ainvoke(_state_for_specialist(description, runtime), context=runtime.context)
        return _to_command(cast(Dict[str, Any], result), runtime.tool_call_id)

    run_search_specialist.__annotations__["runtime"] = ToolRuntime
    arun_search_specialist.__annotations__["runtime"] = ToolRuntime

    return StructuredTool.from_function(
        name="run_search_specialist",
        func=run_search_specialist,
        coroutine=arun_search_specialist,
        description=(
            "调度 AI 检索执行阶段 specialist。可用 specialist：\n"
            f"{descriptions}\n"
            "只在 execute_search、coarse_screen、close_read、feature_comparison 阶段按策略调用。"
        ),
        infer_schema=False,
        args_schema=SearchSpecialistTaskInput,
    )
