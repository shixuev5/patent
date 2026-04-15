from __future__ import annotations

import asyncio
from types import SimpleNamespace

from deepagents.backends.state import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import TodoListMiddleware
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

from agents.ai_search.src.runtime import AiSearchGuardMiddleware, AiSearchStreamingMiddleware
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


def test_planner_blocks_read_file():
    middleware = AiSearchGuardMiddleware("main-agent")
    request = SimpleNamespace(tool_call={"name": "read_file", "id": "call-1", "args": {}})

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "工具 `read_file` 对 `main-agent` 不可用。"


def test_close_reader_blocks_write_file_but_allows_grep():
    middleware = AiSearchGuardMiddleware("close-reader")
    blocked_request = SimpleNamespace(tool_call={"name": "write_file", "id": "call-2", "args": {}})
    allowed_request = SimpleNamespace(tool_call={"name": "grep", "id": "call-3", "args": {}})

    blocked = middleware.wrap_tool_call(blocked_request, lambda _request: "ok")
    allowed = middleware.wrap_tool_call(allowed_request, lambda _request: "ok")

    assert blocked.content == "工具 `write_file` 对 `close-reader` 不可用。"
    assert allowed == "ok"


def test_main_agent_phase_protocol_blocks_wrong_subagent():
    middleware = AiSearchGuardMiddleware(
        "main-agent",
        storage=_StubStorage(PHASE_DRAFTING_PLAN),
        task_id="task-1",
    )
    request = SimpleNamespace(tool_call={"name": "task", "id": "call-4", "args": {"subagent_type": "query-executor"}})

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "子 agent `query-executor` 不能在阶段 `drafting_plan` 由 `main-agent` 调用。"


def test_main_agent_blocks_legacy_tool_outside_phase_policy():
    middleware = AiSearchGuardMiddleware(
        "main-agent",
        storage=_StubStorage(PHASE_DRAFTING_PLAN),
        task_id="task-topic",
    )
    request = SimpleNamespace(tool_call={"name": "legacy_planner_tool", "id": "call-topic-1", "args": {}})

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "工具 `legacy_planner_tool` 不能在阶段 `drafting_plan` 由 `main-agent` 调用。"


def test_main_agent_blocks_removed_subagent_type():
    middleware = AiSearchGuardMiddleware(
        "main-agent",
        storage=_StubStorage(PHASE_DRAFTING_PLAN),
        task_id="task-topic-2",
    )
    request = SimpleNamespace(tool_call={"name": "task", "id": "call-topic-2", "args": {"subagent_type": "legacy-search-worker"}})

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "子 agent `legacy-search-worker` 不允许由 `main-agent` 调用。"


def test_query_executor_phase_protocol_blocks_execution_tools_outside_search_phase():
    middleware = AiSearchGuardMiddleware(
        "query-executor",
        storage=_StubStorage(PHASE_DRAFTING_PLAN),
        task_id="task-2",
    )
    request = SimpleNamespace(tool_call={"name": "search_semantic", "id": "call-5", "args": {}})

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "工具 `search_semantic` 不能在阶段 `drafting_plan` 由 `query-executor` 调用。"


def test_planner_phase_protocol_blocks_plan_save_tool():
    middleware = AiSearchGuardMiddleware(
        "planner",
        storage=_StubStorage(PHASE_DRAFTING_PLAN),
        task_id="task-planner",
    )
    request = SimpleNamespace(tool_call={"name": "publish_planner_draft", "id": "call-planner-1", "args": {}})

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "工具 `publish_planner_draft` 不能在阶段 `drafting_plan` 由 `planner` 调用。"


def test_close_reader_phase_protocol_allows_readonly_filesystem_in_close_read():
    middleware = AiSearchGuardMiddleware(
        "close-reader",
        storage=_StubStorage(PHASE_CLOSE_READ),
        task_id="task-3",
    )
    request = SimpleNamespace(tool_call={"name": "grep", "id": "call-6", "args": {}})

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result == "ok"


def test_main_agent_allows_interrupt_tool_resume_in_awaiting_user_answer():
    middleware = AiSearchGuardMiddleware(
        "main-agent",
        storage=_StubStorage(PHASE_AWAITING_USER_ANSWER),
        task_id="task-4",
    )
    request = SimpleNamespace(tool_call={"name": "request_user_question", "id": "call-7", "args": {}})

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result == "ok"


def test_main_agent_allows_interrupt_tool_resume_in_awaiting_plan_confirmation():
    middleware = AiSearchGuardMiddleware(
        "main-agent",
        storage=_StubStorage(PHASE_AWAITING_PLAN_CONFIRMATION),
        task_id="task-5",
    )
    request = SimpleNamespace(tool_call={"name": "request_plan_confirmation", "id": "call-8", "args": {}})

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result == "ok"


def test_main_agent_async_tool_guard_blocks_read_file():
    middleware = AiSearchGuardMiddleware("main-agent")
    request = SimpleNamespace(tool_call={"name": "read_file", "id": "call-async-1", "args": {}})

    async def _handler(_request):
        raise AssertionError("handler should not be called for blocked async tools")

    result = asyncio.run(middleware.awrap_tool_call(request, _handler))

    assert result.content == "工具 `read_file` 对 `main-agent` 不可用。"


def test_close_reader_subagent_middleware_names_are_unique():
    spec = build_close_reader_subagent(object(), "task-runtime")
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


def test_streaming_middleware_emits_tool_events_for_sync_tool_calls():
    events = []
    middleware = AiSearchStreamingMiddleware("planner")
    request = SimpleNamespace(
        tool_call={"name": "get_planning_context", "id": "call-tool-1", "args": {}},
        runtime=SimpleNamespace(stream_writer=lambda payload: events.append(payload)),
    )

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result == "ok"
    assert [event["type"] for event in events] == ["tool.started", "tool.completed"]
    assert events[0]["payload"]["summary"] == "读取规划上下文"
    assert events[0]["payload"]["subagentLabel"] == "检索规划"
    assert events[0]["payload"]["displayKind"] == "detail"
    assert events[0]["payload"]["displayGroupKey"] == "planner"
    assert events[0]["payload"]["dedupeKey"] == "call-tool-1"


def test_streaming_middleware_emits_failed_tool_event_for_async_tool_calls():
    events = []
    middleware = AiSearchStreamingMiddleware("query-executor")
    request = SimpleNamespace(
        tool_call={"name": "run_execution_step", "id": "call-tool-2", "args": {"operation": "commit"}},
        runtime=SimpleNamespace(stream_writer=lambda payload: events.append(payload)),
    )

    async def _handler(_request):
        raise RuntimeError("boom")

    try:
        asyncio.run(middleware.awrap_tool_call(request, _handler))
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected runtime error")

    assert [event["type"] for event in events] == ["tool.started", "tool.failed"]
    assert events[0]["payload"]["summary"] == "提交执行步骤摘要"
    assert events[0]["payload"]["displayKind"] == "detail"
    assert events[0]["payload"]["displayGroupKey"] == "query-executor"
    assert events[1]["payload"]["dedupeKey"] == "call-tool-2"
    assert events[1]["payload"]["status"] == "failed"
