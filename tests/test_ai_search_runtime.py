from __future__ import annotations

from types import SimpleNamespace

from agents.ai_search.src.runtime import AiSearchGuardMiddleware


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
