from __future__ import annotations

import asyncio
from types import SimpleNamespace

from deepagents.backends.state import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import TodoListMiddleware
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

from agents.ai_search.src.runtime import AiSearchGuardMiddleware
from agents.ai_search.src.runtime_context import build_runtime_context
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
    runnable = spec["runnable"]
    middleware = [
        TodoListMiddleware(),
        FilesystemMiddleware(backend=StateBackend),
        SummarizationMiddleware(model=runnable.model, backend=StateBackend),
        AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
        PatchToolCallsMiddleware(),
        *list(getattr(runnable, "middleware", [])),
    ]

    names = [item.name for item in middleware]

    assert len(set(names)) == len(names)
