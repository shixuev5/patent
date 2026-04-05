from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

stub_ai_search_agents = types.ModuleType("agents.ai_search.main")
stub_ai_search_agents.build_close_reader_agent = lambda: None
stub_ai_search_agents.build_coarse_screener_agent = lambda: None
stub_ai_search_agents.build_feature_comparer_agent = lambda: None
stub_ai_search_agents.build_planning_agent = lambda storage, task_id: None
stub_ai_search_agents.build_query_executor_agent = lambda: None
stub_ai_search_agents.extract_latest_ai_message = lambda values: ""
stub_ai_search_agents.extract_structured_response = lambda values: {}
sys.modules.setdefault("agents.ai_search.main", stub_ai_search_agents)

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
    SEARCH_IN_PROGRESS_CODE,
    STALE_PLAN_CONFIRMATION_CODE,
)
from agents.ai_search.src.state import (
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_DRAFTING_PLAN,
    PHASE_SEARCHING,
    merge_ai_search_meta,
)
from backend.storage import TaskStatus, TaskType
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage.sqlite_storage import SQLiteTaskStorage


def _mount_service(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_service.db")
    manager = PipelineTaskManager(storage)
    monkeypatch.setattr(ai_search_service_module, "task_manager", manager)
    monkeypatch.setattr(ai_search_service_module, "_enforce_daily_quota", lambda owner_id, task_type=None: None)
    monkeypatch.setattr(ai_search_service_module, "emit_system_log", lambda **kwargs: None)
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
    assert snapshot.messages[0]["content"] == "请描述检索目标、核心技术方案、关注特征，并尽量提供申请人、申请日或优先权日等约束条件。"


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


def test_snapshot_returns_extended_search_elements(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    storage.create_ai_search_message(
        {
            "message_id": "msg-elements-1",
            "task_id": created.sessionId,
            "role": "assistant",
            "kind": "search_elements_update",
            "content": "已整理检索要素",
            "metadata": {
                "status": "complete",
                "objective": "检索网络摄像机异常检测方案",
                "applicants": ["杭州海康威视数字技术股份有限公司"],
                "filing_date": "2024-03-01",
                "priority_date": "2023-10-15",
                "search_elements": [{"element_name": "异常检测", "keywords_zh": ["异常检测"]}],
                "missing_items": [],
                "clarification_summary": "已获得核心检索边界。",
            },
        }
    )

    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert snapshot.searchElements is not None
    assert snapshot.searchElements["applicants"] == ["杭州海康威视数字技术股份有限公司"]
    assert snapshot.searchElements["filing_date"] == "2024-03-01"
    assert snapshot.searchElements["priority_date"] == "2023-10-15"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_analysis_payload(*, pn: str = "CN123456A", include_semantic: bool = True, search_matrix=None) -> dict:
    payload = {
        "metadata": {
            "task_id": "analysis-task-1",
            "resolved_pn": pn,
        },
        "report_core": {
            "ai_title": "一种异常检测系统",
            "technical_problem": "降低漏报率",
            "technical_means": "结合时序特征和阈值校正",
            "technical_effects": [{"effect": "降低漏报率"}],
        },
        "search_strategy": {
            "search_matrix": search_matrix if search_matrix is not None else [
                {
                    "element_name": "异常检测",
                    "keywords_zh": ["异常检测"],
                    "keywords_en": ["anomaly detection"],
                    "block_id": "B1",
                    "element_role": "KeyFeature",
                    "priority_tier": "core",
                    "effect_cluster_ids": ["E1"],
                    "notes": "核心算法要素",
                }
            ],
        },
        "report": {},
    }
    if include_semantic:
        payload["search_strategy"]["semantic_strategy"] = {
            "queries": [{"block_id": "B1", "effect_cluster_ids": ["E1"], "content": "anomaly detection"}],
        }
    return payload


def _build_patent_payload(*, pn: str = "CN123456A", with_applicants: bool = True, with_dates: bool = True) -> dict:
    return {
        "bibliographic_data": {
            "publication_number": pn,
            "invention_title": "一种异常检测系统",
            "application_date": "2024.03.01" if with_dates else "",
            "priority_date": "2023.10.15" if with_dates else "",
            "applicants": [{"name": "杭州海康威视数字技术股份有限公司"}] if with_applicants else [],
        }
    }


def _create_completed_analysis_task(
    storage: SQLiteTaskStorage,
    *,
    owner_id: str,
    tmp_path,
    task_type: str = TaskType.PATENT_ANALYSIS.value,
    status: str = TaskStatus.COMPLETED.value,
    analysis_payload: dict | None = None,
    patent_payload: dict | None = None,
) -> Any:
    task = ai_search_service_module.task_manager.create_task(
        owner_id=owner_id,
        task_type=task_type,
        pn="CN123456A",
        title="CN123456A",
    )
    output_dir = Path(task.output_dir)
    analysis_json = analysis_payload or _build_analysis_payload()
    patent_json = patent_payload or _build_patent_payload()
    analysis_path = output_dir / "analysis.json"
    patent_path = output_dir / "patent.json"
    _write_json(analysis_path, analysis_json)
    _write_json(patent_path, patent_json)
    storage.update_task(
        task.id,
        status=status,
        metadata={
            "output_files": {
                "json": str(analysis_path),
                "pn": "CN123456A",
            }
        },
    )
    return storage.get_task(task.id)


def test_seed_search_elements_from_analysis_preserves_context_fields():
    payload = ai_search_service_module._seed_search_elements_from_analysis(
        _build_analysis_payload(),
        _build_patent_payload(),
    )

    assert payload["status"] == "complete"
    assert payload["applicants"] == ["杭州海康威视数字技术股份有限公司"]
    assert payload["filing_date"] == "2024-03-01"
    assert payload["priority_date"] == "2023-10-15"
    assert payload["search_elements"][0]["block_id"] == "B1"
    assert payload["search_elements"][0]["element_role"] == "KeyFeature"
    assert payload["search_elements"][0]["priority_tier"] == "core"
    assert payload["search_elements"][0]["effect_cluster_ids"] == ["E1"]


def test_seed_search_elements_from_analysis_keeps_optional_fields_missing():
    payload = ai_search_service_module._seed_search_elements_from_analysis(
        _build_analysis_payload(include_semantic=False),
        _build_patent_payload(with_applicants=False, with_dates=False),
    )

    assert payload["status"] == "complete"
    assert payload["applicants"] == []
    assert payload["filing_date"] is None
    assert payload["priority_date"] is None
    assert "申请日或优先权日" in payload["missing_items"]
    assert "未提供申请人" in payload["clarification_summary"]


def test_create_session_from_analysis_validates_source_task(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    other_owner_task = _create_completed_analysis_task(storage, owner_id="other-user", tmp_path=tmp_path)

    with pytest.raises(HTTPException) as not_found:
        service.create_session_from_analysis("guest_ai_search", other_owner_task.id)

    assert not_found.value.status_code == 404

    wrong_type_task = _create_completed_analysis_task(
        storage,
        owner_id="guest_ai_search",
        tmp_path=tmp_path,
        task_type=TaskType.AI_REVIEW.value,
    )
    with pytest.raises(HTTPException) as wrong_type:
        service.create_session_from_analysis("guest_ai_search", wrong_type_task.id)

    assert wrong_type.value.status_code == 404

    pending_task = _create_completed_analysis_task(
        storage,
        owner_id="guest_ai_search",
        tmp_path=tmp_path,
        status=TaskStatus.PROCESSING.value,
    )
    with pytest.raises(HTTPException) as pending:
        service.create_session_from_analysis("guest_ai_search", pending_task.id)

    assert pending.value.status_code == 409


def test_create_session_from_analysis_seeds_plan_confirmation(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    analysis_task = _create_completed_analysis_task(
        storage,
        owner_id="guest_ai_search",
        tmp_path=tmp_path,
        analysis_payload=_build_analysis_payload(include_semantic=False),
    )

    def _fake_planning(task_id, thread_id, payload):
        assert thread_id == f"ai-search-{task_id}"
        assert "AI 分析结果" in payload["messages"][0]["content"]
        storage.create_ai_search_plan(
            {
                "task_id": task_id,
                "plan_version": 1,
                "status": "awaiting_confirmation",
                "objective": "基于分析结果的检索计划",
                "search_elements_json": {"status": "complete"},
                "plan_json": {"plan_version": 1, "objective": "基于分析结果的检索计划", "query_batches": []},
            }
        )
        task = storage.get_task(task_id)
        storage.update_task(
            task_id,
            metadata=merge_ai_search_meta(
                task,
                current_phase=PHASE_AWAITING_PLAN_CONFIRMATION,
                active_plan_version=1,
                pending_confirmation_plan_version=1,
            ),
            status=TaskStatus.PAUSED.value,
        )
        return {"interrupted": True, "values": {"messages": []}}

    monkeypatch.setattr(service, "_run_planning_agent", _fake_planning)
    monkeypatch.setattr(ai_search_service_module, "extract_latest_ai_message", lambda values: "检索草稿已生成，请确认计划。")

    created = service.create_session_from_analysis("guest_ai_search", analysis_task.id)
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert snapshot.phase == PHASE_AWAITING_PLAN_CONFIRMATION
    assert snapshot.sourceSummary is not None
    assert snapshot.sourceSummary["sourceType"] == "analysis"
    assert snapshot.sourceSummary["sourceTaskId"] == analysis_task.id
    assert snapshot.sourceSummary["sourcePn"] == "CN123456A"
    assert snapshot.pendingConfirmation is not None
    assert snapshot.searchElements is not None
    assert snapshot.searchElements["search_elements"][0]["element_name"] == "异常检测"
    assert snapshot.messages[0]["content"] == "已从 AI 分析结果导入检索上下文，正在生成检索草稿。"


def test_create_session_from_analysis_can_pause_for_missing_information(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    analysis_task = _create_completed_analysis_task(
        storage,
        owner_id="guest_ai_search",
        tmp_path=tmp_path,
        analysis_payload=_build_analysis_payload(search_matrix=[]),
        patent_payload=_build_patent_payload(with_applicants=False, with_dates=False),
    )

    def _fake_planning(task_id, thread_id, payload):
        task = storage.get_task(task_id)
        storage.create_ai_search_message(
            {
                "message_id": "question-1",
                "task_id": task_id,
                "role": "assistant",
                "kind": "question",
                "content": "请补充至少一个核心技术要素。",
                "question_id": "q-seed-1",
                "stream_status": "completed",
                "metadata": {
                    "question_id": "q-seed-1",
                    "prompt": "请补充至少一个核心技术要素。",
                    "reason": "当前导入的分析结果缺少可直接执行检索的技术要素。",
                    "expected_answer_shape": "简洁列出 1-3 个核心技术要素",
                },
            }
        )
        storage.update_task(
            task_id,
            metadata=merge_ai_search_meta(
                task,
                current_phase=PHASE_AWAITING_USER_ANSWER,
                pending_question_id="q-seed-1",
            ),
            status=TaskStatus.PAUSED.value,
        )
        return {"interrupted": True, "values": {"messages": []}}

    monkeypatch.setattr(service, "_run_planning_agent", _fake_planning)
    monkeypatch.setattr(ai_search_service_module, "extract_latest_ai_message", lambda values: "还需要你补充一个核心技术要素。")

    created = service.create_session_from_analysis("guest_ai_search", analysis_task.id)
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert snapshot.phase == PHASE_AWAITING_USER_ANSWER
    assert snapshot.pendingQuestion is not None
    assert snapshot.pendingQuestion["question_id"] == "q-seed-1"
    assert snapshot.searchElements is not None
    assert snapshot.searchElements["status"] == "needs_answer"
