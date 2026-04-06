from __future__ import annotations

from types import SimpleNamespace

from agents.ai_search.src.runtime import AiSearchGuardMiddleware
from agents.ai_search.src.state import PHASE_CLOSE_READ, PHASE_DRAFTING_PLAN, PHASE_EXECUTE_SEARCH


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


def test_query_executor_phase_protocol_blocks_execution_tools_outside_search_phase():
    middleware = AiSearchGuardMiddleware(
        "query-executor",
        storage=_StubStorage(PHASE_DRAFTING_PLAN),
        task_id="task-2",
    )
    request = SimpleNamespace(tool_call={"name": "search_semantic", "id": "call-5", "args": {}})

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result.content == "工具 `search_semantic` 不能在阶段 `drafting_plan` 由 `query-executor` 调用。"


def test_close_reader_phase_protocol_allows_readonly_filesystem_in_close_read():
    middleware = AiSearchGuardMiddleware(
        "close-reader",
        storage=_StubStorage(PHASE_CLOSE_READ),
        task_id="task-3",
    )
    request = SimpleNamespace(tool_call={"name": "grep", "id": "call-6", "args": {}})

    result = middleware.wrap_tool_call(request, lambda _request: "ok")

    assert result == "ok"
