from __future__ import annotations

import asyncio
import sys
import types

import pytest
from fastapi import HTTPException

stub_ai_search_agents = types.ModuleType("backend.ai_search.agents")
stub_ai_search_agents.build_close_reader_agent = lambda: None
stub_ai_search_agents.build_coarse_screener_agent = lambda: None
stub_ai_search_agents.build_feature_comparer_agent = lambda: None
stub_ai_search_agents.build_planning_agent = lambda storage, task_id: None
stub_ai_search_agents.extract_latest_ai_message = lambda values: ""
stub_ai_search_agents.extract_structured_response = lambda values: {}
sys.modules.setdefault("backend.ai_search.agents", stub_ai_search_agents)

stub_search_clients_pkg = types.ModuleType("agents.common.search_clients")
stub_search_clients_pkg.__path__ = []
stub_search_clients_factory = types.ModuleType("agents.common.search_clients.factory")

class _StubSearchClientFactory:
    @staticmethod
    def get_client(name):
        raise AssertionError(f"unexpected search client usage: {name}")

stub_search_clients_factory.SearchClientFactory = _StubSearchClientFactory
stub_search_clients_pkg.factory = stub_search_clients_factory
sys.modules.setdefault("agents.common.search_clients", stub_search_clients_pkg)
sys.modules.setdefault("agents.common.search_clients.factory", stub_search_clients_factory)

stub_retrieval_pkg = types.ModuleType("agents.common.retrieval")
stub_retrieval_pkg.__path__ = []
stub_local_retriever = types.ModuleType("agents.common.retrieval.local_evidence_retriever")

class _StubLocalEvidenceRetriever:
    def __init__(self, *_args, **_kwargs):
        raise AssertionError("unexpected local evidence retriever usage")

stub_local_retriever.LocalEvidenceRetriever = _StubLocalEvidenceRetriever
stub_retrieval_pkg.local_evidence_retriever = stub_local_retriever
sys.modules.setdefault("agents.common.retrieval", stub_retrieval_pkg)
sys.modules.setdefault("agents.common.retrieval.local_evidence_retriever", stub_local_retriever)

from backend.ai_search import service as ai_search_service_module
from backend.ai_search.models import (
    PENDING_QUESTION_EXISTS_CODE,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_DRAFTING_PLAN,
    PHASE_SEARCHING,
    SEARCH_IN_PROGRESS_CODE,
    STALE_PLAN_CONFIRMATION_CODE,
)
from backend.ai_search.state import merge_ai_search_meta
from backend.storage import TaskStatus
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage.sqlite_storage import SQLiteTaskStorage


def _mount_service(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_service.db")
    manager = PipelineTaskManager(storage)
    monkeypatch.setattr(ai_search_service_module, "task_manager", manager)
    monkeypatch.setattr(ai_search_service_module, "_enforce_daily_quota", lambda owner_id, task_type=None: None)
    return ai_search_service_module.AiSearchService(), storage


async def _collect_stream(stream):
    items = []
    async for item in stream:
        items.append(item)
    return items


def _set_phase(storage: SQLiteTaskStorage, task_id: str, phase: str, **meta_updates):
    task = storage.get_task(task_id)
    assert task is not None
    storage.update_task(
        task_id,
        status=TaskStatus.PAUSED.value if phase in {PHASE_AWAITING_USER_ANSWER, PHASE_AWAITING_PLAN_CONFIRMATION} else TaskStatus.PROCESSING.value,
        metadata=merge_ai_search_meta(task, current_phase=phase, **meta_updates),
    )


def test_create_session_and_snapshot(monkeypatch, tmp_path):
    service, _storage = _mount_service(monkeypatch, tmp_path)

    created = service.create_session("guest_ai_search")
    listed = service.list_sessions("guest_ai_search")
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert listed.total == 1
    assert snapshot.phase == "collecting_requirements"
    assert snapshot.session.taskId == created.taskId
    assert snapshot.messages[0]["content"] == "请描述检索目标、核心技术方案、关注特征和约束条件。"


def test_stream_message_rejects_when_search_is_running(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_phase(storage, created.sessionId, PHASE_SEARCHING)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "继续调整计划")))

    assert exc_info.value.detail["code"] == SEARCH_IN_PROGRESS_CODE


def test_stream_message_rejects_when_question_pending(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_phase(
        storage,
        created.sessionId,
        PHASE_AWAITING_USER_ANSWER,
        pending_question_id="q-1",
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "普通消息")))

    assert exc_info.value.detail["code"] == PENDING_QUESTION_EXISTS_CODE


def test_stream_plan_confirmation_rejects_stale_version(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    storage.create_ai_search_plan(
        {
            "task_id": created.sessionId,
            "plan_version": 1,
            "status": "superseded",
            "objective": "旧计划",
            "search_elements_json": {"status": "complete"},
            "plan_json": {"plan_version": 1},
        }
    )
    storage.create_ai_search_plan(
        {
            "task_id": created.sessionId,
            "plan_version": 2,
            "status": "awaiting_confirmation",
            "objective": "新计划",
            "search_elements_json": {"status": "complete"},
            "plan_json": {"plan_version": 2},
        }
    )
    _set_phase(
        storage,
        created.sessionId,
        PHASE_AWAITING_PLAN_CONFIRMATION,
        active_plan_version=2,
        pending_confirmation_plan_version=2,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_collect_stream(service.stream_plan_confirmation(created.sessionId, "guest_ai_search", 1)))

    assert exc_info.value.detail["code"] == STALE_PLAN_CONFIRMATION_CODE


def test_stream_message_supersedes_waiting_plan(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    storage.create_ai_search_plan(
        {
            "task_id": created.sessionId,
            "plan_version": 1,
            "status": "awaiting_confirmation",
            "objective": "原计划",
            "search_elements_json": {"status": "complete"},
            "plan_json": {"plan_version": 1, "query_batches": []},
        }
    )
    _set_phase(
        storage,
        created.sessionId,
        PHASE_AWAITING_PLAN_CONFIRMATION,
        active_plan_version=1,
        pending_confirmation_plan_version=1,
    )

    monkeypatch.setattr(
        service,
        "_run_planning_agent",
        lambda task_id, thread_id, payload: {"interrupted": False, "values": {"messages": []}},
    )

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "把日期范围缩窄到最近五年")))

    updated_plan = storage.get_ai_search_plan(created.sessionId, 1)
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert updated_plan is not None
    assert updated_plan["status"] == "superseded"
    assert snapshot.phase == PHASE_DRAFTING_PLAN
    assert any("run.completed" in item for item in events)
