from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.models import CurrentUser
from backend.routes import ai_search as ai_search_route
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage.sqlite_storage import SQLiteTaskStorage
from backend.ai_search import service as ai_search_service_module


def _mount_app(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_api.db")
    manager = PipelineTaskManager(storage)
    monkeypatch.setattr(ai_search_service_module, "task_manager", manager)
    monkeypatch.setattr(ai_search_service_module, "_enforce_daily_quota", lambda owner_id, task_type=None: None)
    monkeypatch.setattr(ai_search_service_module, "emit_system_log", lambda **kwargs: None)

    service = ai_search_service_module.AiSearchService()
    monkeypatch.setattr(ai_search_route, "service", service)

    app = FastAPI()
    app.include_router(ai_search_route.router)
    app.dependency_overrides[ai_search_route._get_current_user] = lambda: CurrentUser(user_id="guest_ai_search")
    return app, service


def test_stream_message_endpoint_completes_full_flow(monkeypatch, tmp_path):
    app, _service = _mount_app(monkeypatch, tmp_path)

    class _FakeState:
        values = {"messages": [{"role": "assistant", "content": "好的，我先整理检索计划。"}]}
        interrupts = ()

    class _FakeChunk:
        def __init__(self, content: str):
            self.content = content

    class _FakeAgent:
        def __init__(self):
            self.checkpointer = object()

        async def astream(self, payload, config=None, **kwargs):
            assert payload == {"messages": [{"role": "user", "content": "请帮我检索相关方案"}]}
            assert config["configurable"]["thread_id"].startswith("ai-search-")
            assert config["configurable"]["checkpoint_ns"] == ai_search_service_module.MAIN_AGENT_CHECKPOINT_NS
            assert kwargs["stream_mode"] == ["messages", "custom"]
            await asyncio.sleep(0)
            yield ((), "messages", (_FakeChunk("好的，我先整理检索计划。"), {}))

        def get_state(self, config):
            assert config["configurable"]["checkpoint_ns"] == ai_search_service_module.MAIN_AGENT_CHECKPOINT_NS
            assert config["configurable"]["__pregel_checkpointer"] is self.checkpointer
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: _FakeAgent())
    monkeypatch.setattr(
        ai_search_service_module,
        "extract_latest_ai_message",
        lambda values: values["messages"][-1]["content"],
    )

    client = TestClient(app, raise_server_exceptions=False)

    created = client.post("/api/ai-search/sessions")
    assert created.status_code == 200
    session_id = created.json()["sessionId"]

    response = client.post(
        f"/api/ai-search/sessions/{session_id}/messages/stream",
        json={"content": "请帮我检索相关方案"},
    )

    assert response.status_code == 200
    body = response.text
    assert "assistant.message.completed" in body
    assert "run.completed" in body

    events = []
    for block in body.strip().split("\n\n"):
        if not block.startswith("data: "):
            continue
        events.append(json.loads(block[6:]))

    assert events[0]["type"] == "run.started"
    assert any(event["type"] == "assistant.message.started" for event in events)
    assert any(event["type"] == "assistant.message.delta" for event in events)
    assert any(
        event["type"] == "assistant.message.completed"
        and event["payload"]["content"] == "好的，我先整理检索计划。"
        for event in events
    )
    assert events[-1]["type"] == "run.completed"


def test_resume_endpoint_streams_resume_run(monkeypatch, tmp_path):
    app, service = _mount_app(monkeypatch, tmp_path)

    async def _fake_resume(session_id: str, owner_id: str):
        assert owner_id == "guest_ai_search"
        yield f"data: {json.dumps({'type': 'run.completed', 'sessionId': session_id, 'taskId': session_id, 'phase': 'execute_search', 'payload': {'interrupted': False}}, ensure_ascii=False)}\n\n"

    monkeypatch.setattr(service, "stream_resume", _fake_resume)

    client = TestClient(app, raise_server_exceptions=False)

    created = client.post("/api/ai-search/sessions")
    assert created.status_code == 200
    session_id = created.json()["sessionId"]

    response = client.post(f"/api/ai-search/sessions/{session_id}/resume/stream")

    assert response.status_code == 200
    assert "run.completed" in response.text
