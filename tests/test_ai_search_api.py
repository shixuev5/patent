from __future__ import annotations

import asyncio
import json

from agents.ai_search.src.context import AiSearchAgentContext
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.models import CurrentUser
from backend.routes import ai_search as ai_search_route
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage.sqlite_storage import SQLiteTaskStorage
from backend.ai_search import agent_run_service as ai_search_agent_run_service_module
from backend.ai_search import service as ai_search_service_module
from backend.ai_search.models import AiSearchCreateSessionResponse
from agents.ai_search.src.state import merge_ai_search_meta


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


def _parse_events(body: str):
    events = []
    for block in body.strip().split("\n\n"):
        if not block.startswith("data: "):
            continue
        events.append(json.loads(block[6:]))
    return events


def _set_planner_draft(service, session_id: str):
    task = service.storage.get_task(session_id)
    service.storage.update_task(
        session_id,
        metadata=merge_ai_search_meta(
            task,
            planner_draft={
                "draft_id": "draft-1",
                "draft_version": 1,
                "phase": "drafting_plan",
                "review_markdown": "# 计划",
                "execution_spec": {},
            },
        ),
    )


def _set_current_plan(service, session_id: str, *, plan_version: int = 1, review_markdown: str = "# 计划") -> None:
    service.storage.create_ai_search_plan(
        {
            "task_id": session_id,
            "plan_version": plan_version,
            "status": "draft",
            "review_markdown": review_markdown,
            "execution_spec_json": {},
        }
    )
    task = service.storage.get_task(session_id)
    service.storage.update_task(
        session_id,
        metadata=merge_ai_search_meta(
            task,
            current_phase="drafting_plan",
            active_plan_version=plan_version,
        ),
    )


def test_stream_message_endpoint_surfaces_direct_reply_even_without_state_transition(monkeypatch, tmp_path):
    app, service = _mount_app(monkeypatch, tmp_path)

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
        ai_search_agent_run_service_module,
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
    assert "run.completed" in body

    events = _parse_events(body)

    assert events[0]["type"] == "run.started"
    assert any(event["type"] == "assistant.message.started" for event in events)
    assert not any(event["type"] == "assistant.message.delta" for event in events)
    assert any(event["type"] == "assistant.message.completed" for event in events)
    assert events[-1]["type"] == "run.completed"


def test_stream_message_endpoint_reconciles_plan_confirmation_when_plan_ready(monkeypatch, tmp_path):
    app, service = _mount_app(monkeypatch, tmp_path)

    client = TestClient(app, raise_server_exceptions=False)
    created = client.post("/api/ai-search/sessions")
    assert created.status_code == 200
    session_id = created.json()["sessionId"]

    class _FakeState:
        values = {}
        interrupts = ()

    class _FakeAgent:
        def __init__(self):
            self.checkpointer = object()

        async def astream(self, payload, config=None, **kwargs):
            _set_current_plan(service, session_id, plan_version=1, review_markdown="# 计划\n\n- 检索路线 A")
            await asyncio.sleep(0)
            if False:
                yield None

        def get_state(self, config):
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: _FakeAgent())

    response = client.post(
        f"/api/ai-search/sessions/{session_id}/messages/stream",
        json={"content": "请生成检索计划"},
    )

    assert response.status_code == 200
    events = _parse_events(response.text)
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["phase"] == "awaiting_plan_confirmation"

    snapshot = service.get_snapshot(session_id, "guest_ai_search")
    pending_action = snapshot.conversation["pendingAction"]
    assert pending_action["actionType"] == "plan_confirmation"
    assert snapshot.session.phase == "awaiting_plan_confirmation"
    assert snapshot.plan["currentPlan"]["status"] == "awaiting_confirmation"
    assert any(
        item["kind"] == "plan_confirmation" and item["content"] == "# 计划\n\n- 检索路线 A"
        for item in snapshot.conversation["messages"]
    )


def test_stream_message_endpoint_reconciles_pending_question_phase(monkeypatch, tmp_path):
    app, service = _mount_app(monkeypatch, tmp_path)

    client = TestClient(app, raise_server_exceptions=False)
    created = client.post("/api/ai-search/sessions")
    assert created.status_code == 200
    session_id = created.json()["sessionId"]

    class _FakeState:
        values = {}
        interrupts = ()

    class _FakeAgent:
        def __init__(self):
            self.checkpointer = object()

        async def astream(self, payload, config=None, **kwargs):
            context = AiSearchAgentContext(service.storage, session_id)
            context.create_pending_action(
                "question",
                {
                    "question_id": "question-1",
                    "prompt": "请补充申请人信息",
                    "reason": "申请人约束缺失",
                },
            )
            service._update_phase(session_id, "drafting_plan")
            await asyncio.sleep(0)
            if False:
                yield None

        def get_state(self, config):
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: _FakeAgent())

    response = client.post(
        f"/api/ai-search/sessions/{session_id}/messages/stream",
        json={"content": "请继续"},
    )

    assert response.status_code == 200
    events = _parse_events(response.text)
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["phase"] == "awaiting_user_answer"

    snapshot = service.get_snapshot(session_id, "guest_ai_search")
    pending_action = snapshot.conversation["pendingAction"]
    assert pending_action["actionType"] == "question"
    assert snapshot.session.phase == "awaiting_user_answer"


def test_stream_message_endpoint_surfaces_direct_assistant_reply(monkeypatch, tmp_path):
    app, service = _mount_app(monkeypatch, tmp_path)

    monkeypatch.setattr(
        service,
        "_run_main_agent",
        lambda task_id, thread_id, payload, **kwargs: {
            "interrupted": False,
            "values": {"messages": [{"role": "assistant", "content": "你好，请告诉我你的检索目标。"}]},
        },
    )
    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        ai_search_agent_run_service_module,
        "extract_latest_ai_message",
        lambda values: values["messages"][-1]["content"],
    )

    client = TestClient(app, raise_server_exceptions=False)
    created = client.post("/api/ai-search/sessions")
    assert created.status_code == 200
    session_id = created.json()["sessionId"]

    response = client.post(
        f"/api/ai-search/sessions/{session_id}/messages/stream",
        json={"content": "你好"},
    )

    assert response.status_code == 200
    events = _parse_events(response.text)
    assert events[-1]["type"] == "run.completed"
    assert any(event["type"] == "assistant.message.completed" for event in events)

    snapshot = service.get_snapshot(session_id, "guest_ai_search")
    assert snapshot.session.phase == "drafting_plan"
    assert snapshot.conversation["messages"][-1]["role"] == "assistant"
    assert snapshot.conversation["messages"][-1]["content"] == "你好，请告诉我你的检索目标。"


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


def test_create_from_analysis_endpoint_only_creates_seeded_session(monkeypatch, tmp_path):
    app, service = _mount_app(monkeypatch, tmp_path)

    monkeypatch.setattr(
        service,
        "create_session_from_analysis_seed",
        lambda owner_id, analysis_task_id: AiSearchCreateSessionResponse(
            sessionId="search-seed-1",
            taskId="search-seed-1",
            threadId="ai-search-search-seed-1",
            reused=True,
            sourceTaskId=analysis_task_id,
        ),
    )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/ai-search/sessions/from-analysis", json={"analysisTaskId": "analysis-1"})

    assert response.status_code == 200
    assert response.json()["sessionId"] == "search-seed-1"
    assert response.json()["reused"] is True
    assert response.json()["sourceTaskId"] == "analysis-1"


def test_analysis_seed_endpoint_streams_seed_run(monkeypatch, tmp_path):
    app, service = _mount_app(monkeypatch, tmp_path)

    async def _fake_seed(session_id: str, owner_id: str):
        assert owner_id == "guest_ai_search"
        yield f"data: {json.dumps({'type': 'run.completed', 'sessionId': session_id, 'taskId': session_id, 'phase': 'drafting_plan', 'payload': {'interrupted': False}}, ensure_ascii=False)}\n\n"

    monkeypatch.setattr(service, "stream_analysis_seed", _fake_seed)

    client = TestClient(app, raise_server_exceptions=False)
    created = client.post("/api/ai-search/sessions")
    session_id = created.json()["sessionId"]

    response = client.post(f"/api/ai-search/sessions/{session_id}/analysis-seed/stream")

    assert response.status_code == 200
    assert "run.completed" in response.text


def test_handoff_continue_endpoint_streams_continue_run(monkeypatch, tmp_path):
    app, service = _mount_app(monkeypatch, tmp_path)

    async def _fake_continue(session_id: str, owner_id: str):
        assert owner_id == "guest_ai_search"
        yield f"data: {json.dumps({'type': 'run.completed', 'sessionId': session_id, 'taskId': session_id, 'phase': 'drafting_plan', 'payload': {'interrupted': False}}, ensure_ascii=False)}\n\n"

    monkeypatch.setattr(service, "stream_decision_continue", _fake_continue)

    client = TestClient(app, raise_server_exceptions=False)
    created = client.post("/api/ai-search/sessions")
    session_id = created.json()["sessionId"]

    response = client.post(f"/api/ai-search/sessions/{session_id}/decision/continue")

    assert response.status_code == 200
    assert "run.completed" in response.text


def test_handoff_complete_endpoint_streams_complete_run(monkeypatch, tmp_path):
    app, service = _mount_app(monkeypatch, tmp_path)

    async def _fake_complete(session_id: str, owner_id: str):
        assert owner_id == "guest_ai_search"
        yield f"data: {json.dumps({'type': 'run.completed', 'sessionId': session_id, 'taskId': session_id, 'phase': 'completed', 'payload': {'interrupted': False}}, ensure_ascii=False)}\n\n"

    monkeypatch.setattr(service, "stream_decision_complete", _fake_complete)

    client = TestClient(app, raise_server_exceptions=False)
    created = client.post("/api/ai-search/sessions")
    session_id = created.json()["sessionId"]

    response = client.post(f"/api/ai-search/sessions/{session_id}/decision/complete")

    assert response.status_code == 200
    assert "run.completed" in response.text
