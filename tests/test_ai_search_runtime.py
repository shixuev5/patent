from __future__ import annotations

import asyncio
from types import SimpleNamespace

import deepagents.middleware.subagents as subagents_module
from deepagents.backends.state import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import TodoListMiddleware
from langchain.tools import ToolRuntime
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.messages import AIMessage
from langgraph.types import Command

from agents.ai_search.src.runtime import AiSearchGuardMiddleware
from agents.ai_search.src.runtime_context import build_runtime_context, ensure_deepagents_context_support
from agents.ai_search.src.subagents.close_reader.agent import build_close_reader_subagent
from agents.ai_search.src.state import (
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_CLOSE_READ,
    PHASE_DRAFTING_PLAN,
    PHASE_EXECUTE_SEARCH,
)


class _StubStorage:
    def __init__(self, phase: str):
        self._task = SimpleNamespace(metadata={"ai_search": {"current_phase": phase}})

    def get_task(self, _task_id: str):
        return self._task


def _request(tool_name: str, *, tool_id: str, args: dict | None = None, phase: str | None = None, role: str = "main-agent"):
    runtime = SimpleNamespace(config={"metadata": {"lc_agent_name": role}})
    if phase is not None:
        runtime.context = build_runtime_context(_StubStorage(phase), "task-runtime")
    return SimpleNamespace(tool_call={"name": tool_name, "id": tool_id, "args": args or {}}, runtime=runtime)


def test_planner_blocks_read_file():
    middleware = AiSearchGuardMiddleware()
    request = _request("read_file", tool_id="call-1")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "工具 `read_file` 对 `main-agent` 不可用。"


def test_close_reader_blocks_write_file_but_allows_grep():
    middleware = AiSearchGuardMiddleware()
    blocked_request = _request("write_file", tool_id="call-2", role="close-reader")
    allowed_request = _request("grep", tool_id="call-3", role="close-reader")

    blocked = middleware.wrap_tool_call(blocked_request, lambda _request: "ok")
    allowed = middleware.wrap_tool_call(allowed_request, lambda _request: "ok")

    assert blocked.content == "工具 `write_file` 对 `close-reader` 不可用。"
    assert allowed == "ok"


def test_main_agent_phase_protocol_blocks_wrong_subagent():
    middleware = AiSearchGuardMiddleware()
    request = _request("task", tool_id="call-4", args={"subagent_type": "query-executor"}, phase=PHASE_DRAFTING_PLAN, role="ai-search-main-agent-task-runtime")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "子 agent `query-executor` 不能在阶段 `drafting_plan` 由 `main-agent` 调用。"


def test_main_agent_blocks_legacy_tool_outside_phase_policy():
    middleware = AiSearchGuardMiddleware()
    request = _request("legacy_planner_tool", tool_id="call-topic-1", phase=PHASE_DRAFTING_PLAN, role="ai-search-main-agent-task-runtime")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "工具 `legacy_planner_tool` 不能在阶段 `drafting_plan` 由 `main-agent` 调用。"


def test_main_agent_blocks_removed_subagent_type():
    middleware = AiSearchGuardMiddleware()
    request = _request("task", tool_id="call-topic-2", args={"subagent_type": "legacy-search-worker"}, phase=PHASE_DRAFTING_PLAN, role="ai-search-main-agent-task-runtime")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "子 agent `legacy-search-worker` 不允许由 `main-agent` 调用。"


def test_main_agent_allows_named_planner_subagent_call_in_drafting_plan():
    middleware = AiSearchGuardMiddleware()
    request = _request("planner", tool_id="call-topic-3", phase=PHASE_DRAFTING_PLAN, role="ai-search-main-agent-task-runtime")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result == "ok"


def test_query_executor_phase_protocol_blocks_execution_tools_outside_search_phase():
    middleware = AiSearchGuardMiddleware()
    request = _request("search_semantic", tool_id="call-5", phase=PHASE_DRAFTING_PLAN, role="query-executor")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "工具 `search_semantic` 不能在阶段 `drafting_plan` 由 `query-executor` 调用。"


def test_planner_phase_protocol_blocks_plan_save_tool():
    middleware = AiSearchGuardMiddleware()
    request = _request("publish_planner_draft", tool_id="call-planner-1", phase=PHASE_DRAFTING_PLAN, role="planner")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "工具 `publish_planner_draft` 不能在阶段 `drafting_plan` 由 `planner` 调用。"


def test_search_elements_phase_protocol_allows_save_tool():
    middleware = AiSearchGuardMiddleware()
    request = _request("save_search_elements", tool_id="call-search-elements-1", phase=PHASE_DRAFTING_PLAN, role="search-elements")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result == "ok"


def test_planner_phase_protocol_allows_draft_commit_tool():
    middleware = AiSearchGuardMiddleware()
    request = _request("save_planner_draft", tool_id="call-planner-2", phase=PHASE_DRAFTING_PLAN, role="planner")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result == "ok"


def test_plan_prober_phase_protocol_allows_probe_commit_tool():
    middleware = AiSearchGuardMiddleware()
    request = _request("save_probe_findings", tool_id="call-prober-1", phase=PHASE_DRAFTING_PLAN, role="plan-prober")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result == "ok"


def test_close_reader_phase_protocol_allows_readonly_filesystem_in_close_read():
    middleware = AiSearchGuardMiddleware()
    request = _request("grep", tool_id="call-6", phase=PHASE_CLOSE_READ, role="ai-search-close-reader-task-runtime")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result == "ok"


def test_main_agent_allows_interrupt_tool_resume_in_awaiting_user_answer():
    middleware = AiSearchGuardMiddleware()
    request = _request("request_user_question", tool_id="call-7", phase=PHASE_AWAITING_USER_ANSWER, role="ai-search-main-agent-task-runtime")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result == "ok"


def test_main_agent_allows_interrupt_tool_resume_in_awaiting_plan_confirmation():
    middleware = AiSearchGuardMiddleware()
    request = _request("request_plan_confirmation", tool_id="call-8", phase=PHASE_AWAITING_PLAN_CONFIRMATION, role="ai-search-main-agent-task-runtime")

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result == "ok"


def test_main_agent_async_tool_guard_blocks_read_file():
    middleware = AiSearchGuardMiddleware()
    request = _request("read_file", tool_id="call-async-1")

    async def _handler(_request):
        raise AssertionError("handler should not be called for blocked async tools")

    result = asyncio.run(middleware.awrap_tool_call(request, _handler))

    assert result.content == "工具 `read_file` 对 `main-agent` 不可用。"


def test_close_reader_subagent_middleware_names_are_unique():
    spec = build_close_reader_subagent()
    middleware = [
        TodoListMiddleware(),
        FilesystemMiddleware(backend=StateBackend),
        SummarizationMiddleware(model=spec["model"], backend=StateBackend),
        AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
        PatchToolCallsMiddleware(),
        *list(spec.get("middleware", [])),
    ]

    names = [item.name for item in middleware]

    assert len(set(names)) == len(names)


def test_task_tool_async_invocation_preserves_injected_runtime():
    ensure_deepagents_context_support()

    class _DummyRunnable:
        async def ainvoke(self, state, context=None):
            assert state["messages"][-1].content == "整理检索要素"
            assert context == "runtime-context"
            return {"messages": [AIMessage(content="子 agent 已完成")], "phase": "drafting_plan"}

        def invoke(self, state, context=None):
            assert state["messages"][-1].content == "整理检索要素"
            assert context == "runtime-context"
            return {"messages": [AIMessage(content="子 agent 已完成")], "phase": "drafting_plan"}

    tool = subagents_module._build_task_tool(
        [{"name": "search-elements", "description": "检索要素整理", "runnable": _DummyRunnable()}]
    )
    runtime = ToolRuntime(
        state={"messages": []},
        context="runtime-context",
        config={},
        stream_writer=lambda _chunk: None,
        tool_call_id="call-search-elements-1",
        store=None,
    )

    result = asyncio.run(
        tool.ainvoke(
            {
                "type": "tool_call",
                "id": "call-search-elements-1",
                "name": "task",
                "args": {
                    "description": "整理检索要素",
                    "subagent_type": "search-elements",
                    "runtime": runtime,
                },
            }
        )
    )

    assert tool._injected_args_keys == frozenset({"runtime"})
    assert isinstance(result, Command)
    assert result.update["phase"] == "drafting_plan"
    assert result.update["messages"][0].content == "子 agent 已完成"
