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
stub_ai_search_agents.build_main_agent = lambda storage, task_id: None
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
from backend.ai_search.analysis_seed import seed_search_elements_from_analysis
from backend.ai_search.models import (
    PENDING_QUESTION_EXISTS_CODE,
    RESUME_NOT_AVAILABLE_CODE,
    SEARCH_IN_PROGRESS_CODE,
    STALE_PLAN_CONFIRMATION_CODE,
)
import agents.ai_search.src.context as ai_search_context_module
from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.state import (
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_CLOSE_READ,
    PHASE_DRAFTING_PLAN,
    PHASE_EXECUTE_SEARCH,
    PHASE_GENERATE_FEATURE_TABLE,
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


def _parse_data_events(items: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in items:
        if not item.startswith("data: "):
            continue
        events.append(json.loads(item[6:]))
    return events


def _set_phase(storage: SQLiteTaskStorage, task_id: str, phase: str, **meta_updates):
    task = storage.get_task(task_id)
    assert task is not None
    storage.update_task(
        task_id,
        status=TaskStatus.PAUSED.value if phase in {PHASE_AWAITING_USER_ANSWER, PHASE_AWAITING_PLAN_CONFIRMATION} else TaskStatus.PROCESSING.value,
        metadata=merge_ai_search_meta(task, current_phase=phase, **meta_updates),
    )


def _plan_record(task_id: str, *, plan_version: int = 1, status: str = "draft", title: str = "检索计划") -> dict:
    return {
        "task_id": task_id,
        "plan_version": plan_version,
        "status": status,
        "review_markdown": f"# {title}\n\n## 检索目标\n测试目标\n\n## 检索边界\n无\n\n## 检索要素\n- 要素A\n\n## 分步检索方案\n1. 步骤一\n\n## 调整策略\n- 若结果过少则扩词\n\n## 待确认\n确认后实施",
        "execution_spec_json": {
            "search_scope": {
                "objective": "测试目标",
                "applicants": [],
                "filing_date": None,
                "priority_date": None,
                "languages": ["zh", "en"],
                "databases": ["zhihuiya"],
                "excluded_items": [],
            },
            "constraints": {},
            "execution_policy": {"dynamic_replanning": True, "planner_visibility": "summary_only", "max_rounds": 3},
            "sub_plans": [
                {
                    "sub_plan_id": "sub_plan_1",
                    "title": "子计划 1",
                    "goal": "测试目标",
                    "semantic_query_text": "",
                    "search_elements": [{"element_name": "要素A", "keywords_zh": ["要素A"], "keywords_en": ["feature a"], "block_id": "B1", "notes": ""}],
                    "retrieval_steps": [
                        {
                            "step_id": "step_1",
                            "title": "子计划 1 / 首轮宽召回",
                            "purpose": "验证首轮召回质量",
                            "feature_combination": "A+B1",
                            "language_strategy": "中文优先，补英文",
                            "ipc_cpc_mode": "按需补 IPC/CPC",
                            "ipc_cpc_codes": [],
                            "expected_recall": "获取首轮候选池",
                            "fallback_action": "结果异常时调整同义词和分类号",
                            "query_blueprint_refs": ["b1"],
                            "phase_key": "execute_search",
                        }
                    ],
                    "query_blueprints": [{"batch_id": "b1", "goal": "测试目标", "sub_plan_id": "sub_plan_1"}],
                    "classification_hints": [],
                }
            ],
        },
    }


def test_create_session_and_snapshot(monkeypatch, tmp_path):
    service, _storage = _mount_service(monkeypatch, tmp_path)

    created = service.create_session("guest_ai_search")
    listed = service.list_sessions("guest_ai_search")
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert listed.total == 1
    assert snapshot.phase == "collecting_requirements"
    assert snapshot.session.taskId == created.taskId
    assert snapshot.messages[0]["content"] == "请描述检索目标、核心技术方案、关注特征，并尽量提供申请人、申请日或优先权日等约束条件。"
    assert snapshot.session.pinned is False


def test_create_session_uses_unified_flow_metadata(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)

    created = service.create_session("guest_ai_search")
    task = storage.get_task(created.sessionId)

    assert (task.metadata.get("ai_search") or {}).get("current_phase") == "collecting_requirements"


def test_update_session_supports_rename_and_pin(monkeypatch, tmp_path):
    service, _storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")

    renamed = service.update_session(created.sessionId, "guest_ai_search", title="新的检索标题")
    pinned = service.update_session(created.sessionId, "guest_ai_search", pinned=True)
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    listed = service.list_sessions("guest_ai_search")

    assert renamed.title == "新的检索标题"
    assert pinned.pinned is True
    assert snapshot.session.title == "新的检索标题"
    assert snapshot.session.pinned is True
    assert listed.items[0].title == "新的检索标题"
    assert listed.items[0].pinned is True


def test_delete_session_soft_deletes_ai_search_task(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")

    result = service.delete_session(created.sessionId, "guest_ai_search")
    deleted_task = storage.get_task(created.sessionId)

    assert result == {"deleted": True}
    assert deleted_task is not None
    assert deleted_task.deleted_at is not None
    assert service.list_sessions("guest_ai_search").total == 0


def test_delete_session_rejects_execute_search_phase(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_phase(storage, created.sessionId, PHASE_EXECUTE_SEARCH)

    with pytest.raises(HTTPException) as exc_info:
        service.delete_session(created.sessionId, "guest_ai_search")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "检索执行中，请稍后再删除会话。"


def test_stream_message_rejects_when_search_is_running(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_phase(storage, created.sessionId, PHASE_EXECUTE_SEARCH)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "继续调整计划")))

    assert exc_info.value.detail["code"] == SEARCH_IN_PROGRESS_CODE


def test_stream_message_keeps_unified_flow_without_structured_claim_source(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    monkeypatch.setattr(
        service,
        "_run_main_agent",
        lambda task_id, thread_id, payload, **kwargs: {"interrupted": False, "values": {"messages": []}},
    )

    asyncio.run(
        _collect_stream(
            service.stream_message(
                created.sessionId,
                "guest_ai_search",
                "请围绕权利要求1的处理器和存储器限定来规划检索。",
            )
        )
    )

    task = storage.get_task(created.sessionId)

    assert (task.metadata.get("ai_search") or {}).get("current_phase") == PHASE_DRAFTING_PLAN


def test_stream_message_rejects_when_execution_todo_failed(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_phase(
        storage,
        created.sessionId,
        PHASE_EXECUTE_SEARCH,
        current_task="plan_1:sub_plan_1:step_1",
        todos=[{"todo_id": "plan_1:sub_plan_1:step_1", "sub_plan_id": "sub_plan_1", "step_id": "step_1", "phase_key": "execute_search", "title": "执行步骤 1", "description": "目的：验证首轮召回", "status": "failed", "resume_from": "run_execution_step"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "继续上次失败的执行")))

    assert exc_info.value.detail["code"] == SEARCH_IN_PROGRESS_CODE


def test_stream_resume_continues_failed_execution_todo(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_phase(
        storage,
        created.sessionId,
        PHASE_EXECUTE_SEARCH,
        current_task="plan_1:sub_plan_1:step_1",
        todos=[{"todo_id": "plan_1:sub_plan_1:step_1", "sub_plan_id": "sub_plan_1", "step_id": "step_1", "phase_key": "execute_search", "title": "执行步骤 1", "description": "目的：验证首轮召回", "status": "failed", "resume_from": "run_execution_step", "last_error": "timeout"}],
    )

    monkeypatch.setattr(
        service,
        "_run_main_agent",
        lambda task_id, thread_id, payload, **kwargs: (
            {"interrupted": False, "values": {"messages": [{"role": "assistant", "content": "继续恢复检索。"}]}}
            if "继续当前失败的 AI 检索执行" in payload["messages"][0]["content"]
            else (_ for _ in ()).throw(AssertionError("unexpected resume payload"))
        ),
    )
    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage_arg, task_id_arg: None)
    monkeypatch.setattr(
        ai_search_service_module,
        "extract_latest_ai_message",
        lambda values: values["messages"][-1]["content"],
    )

    events = asyncio.run(_collect_stream(service.stream_resume(created.sessionId, "guest_ai_search")))
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert snapshot.phase == PHASE_EXECUTE_SEARCH
    assert snapshot.resumeAction is not None
    assert snapshot.resumeAction["lastError"] == "timeout"
    assert any("assistant.message.completed" in item for item in events)


def test_stream_resume_rejects_when_no_failed_execution_todo(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_phase(
        storage,
        created.sessionId,
        PHASE_EXECUTE_SEARCH,
        current_task="plan_1:sub_plan_1:step_1",
        todos=[{"todo_id": "plan_1:sub_plan_1:step_1", "sub_plan_id": "sub_plan_1", "step_id": "step_1", "phase_key": "execute_search", "title": "执行步骤 1", "description": "目的：验证首轮召回", "status": "in_progress", "resume_from": "run_execution_step"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_collect_stream(service.stream_resume(created.sessionId, "guest_ai_search")))

    assert exc_info.value.detail["code"] == RESUME_NOT_AVAILABLE_CODE


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
        _plan_record(created.sessionId, plan_version=1, status="superseded", title="旧计划")
    )
    storage.create_ai_search_plan(
        _plan_record(created.sessionId, plan_version=2, status="awaiting_confirmation", title="新计划")
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


def test_stream_plan_confirmation_emits_run_error_when_resume_does_not_confirm_plan(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    storage.create_ai_search_plan(
        _plan_record(created.sessionId, plan_version=1, status="awaiting_confirmation", title="待确认计划")
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
        "_run_main_agent",
        lambda task_id, thread_id, payload, **kwargs: {"interrupted": False, "values": {"messages": []}},
    )
    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage_arg, task_id_arg: None)

    events = asyncio.run(_collect_stream(service.stream_plan_confirmation(created.sessionId, "guest_ai_search", 1)))

    assert any("run.error" in item for item in events)
    assert any(ai_search_service_module.PLAN_CONFIRMATION_REQUIRED_CODE in item for item in events)
    assert not any("run.completed" in item for item in events)


def test_patch_selected_documents_reopens_feature_table_and_hides_stale_table(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    storage.create_ai_search_plan(
        _plan_record(created.sessionId, plan_version=1, status="confirmed", title="测试目标")
    )
    storage.upsert_ai_search_documents(
        [
            {
                "document_id": "doc-1",
                "task_id": created.sessionId,
                "plan_version": 1,
                "pn": "CN1",
                "title": "文献1",
                "abstract": "",
                "stage": "selected",
                "user_pinned": False,
                "user_removed": False,
                "coarse_status": "kept",
                "close_read_status": "selected",
            }
        ]
    )
    storage.create_ai_search_feature_table(
        {
            "feature_table_id": "ft-1",
            "task_id": created.sessionId,
            "plan_version": 1,
            "status": "completed",
            "table_json": [{"feature": "A"}],
            "summary_markdown": "旧特征表",
        }
    )
    _set_phase(
        storage,
        created.sessionId,
        "completed",
        active_plan_version=1,
        current_feature_table_id="ft-1",
    )

    snapshot = service.patch_selected_documents(created.sessionId, "guest_ai_search", 1, ["doc-1"], [])

    assert snapshot.phase == PHASE_GENERATE_FEATURE_TABLE
    assert snapshot.featureTable is None


def test_patch_selected_documents_returns_to_close_read_when_selection_becomes_empty(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    storage.create_ai_search_plan(
        _plan_record(created.sessionId, plan_version=1, status="confirmed", title="测试目标")
    )
    storage.upsert_ai_search_documents(
        [
            {
                "document_id": "doc-1",
                "task_id": created.sessionId,
                "plan_version": 1,
                "pn": "CN1",
                "title": "文献1",
                "abstract": "",
                "stage": "selected",
                "user_pinned": False,
                "user_removed": False,
                "coarse_status": "kept",
                "close_read_status": "selected",
            }
        ]
    )
    _set_phase(
        storage,
        created.sessionId,
        PHASE_GENERATE_FEATURE_TABLE,
        active_plan_version=1,
        current_feature_table_id=None,
    )

    snapshot = service.patch_selected_documents(created.sessionId, "guest_ai_search", 1, [], ["doc-1"])

    assert snapshot.phase == PHASE_CLOSE_READ
    assert snapshot.selectedDocuments == []


def test_stream_message_supersedes_waiting_plan(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    storage.create_ai_search_plan(
        _plan_record(created.sessionId, plan_version=1, status="awaiting_confirmation", title="原计划")
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
        "_run_main_agent",
        lambda task_id, thread_id, payload, **kwargs: {"interrupted": False, "values": {"messages": []}},
    )
    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage_arg, task_id_arg: None)

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "把日期范围缩窄到最近五年")))

    updated_plan = storage.get_ai_search_plan(created.sessionId, 1)
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert updated_plan is not None
    assert updated_plan["status"] == "superseded"
    assert snapshot.phase == PHASE_DRAFTING_PLAN
    assert any("run.completed" in item for item in events)


def test_run_main_agent_reads_state_with_explicit_checkpointer(monkeypatch, tmp_path):
    service, _storage = _mount_service(monkeypatch, tmp_path)

    class _FakeState:
        values = {"messages": [{"role": "assistant", "content": "ok"}]}

    class _FakeAgent:
        def __init__(self):
            self.checkpointer = object()
            self.state_config = None

        def stream(self, payload, config):
            assert payload == {"messages": [{"role": "user", "content": "测试"}]}
            assert config["configurable"]["thread_id"] == "ai-search-task-1"
            assert config["configurable"]["checkpoint_ns"] == ai_search_service_module.MAIN_AGENT_CHECKPOINT_NS
            yield {"messages": []}

        def get_state(self, config):
            self.state_config = config
            assert config["configurable"]["thread_id"] == "ai-search-task-1"
            assert config["configurable"]["checkpoint_ns"] == ai_search_service_module.MAIN_AGENT_CHECKPOINT_NS
            assert config["configurable"]["__pregel_checkpointer"] is self.checkpointer
            return _FakeState()

    fake_agent = _FakeAgent()
    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: fake_agent)

    result = service._run_main_agent("task-1", "ai-search-task-1", {"messages": [{"role": "user", "content": "测试"}]})

    assert result == {"interrupted": False, "values": {"messages": [{"role": "assistant", "content": "ok"}]}}
    assert fake_agent.state_config is not None


def test_run_main_agent_reuses_existing_empty_checkpoint_namespace(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    assert storage.put_ai_search_checkpoint(
        {
            "thread_id": "ai-search-task-2",
            "checkpoint_ns": "",
            "checkpoint_id": "0001",
            "checkpoint_json": json.dumps({"id": "0001"}),
            "metadata_json": json.dumps({"source": "main"}),
        }
    )

    class _FakeState:
        values = {"messages": [{"role": "assistant", "content": "ok"}]}

    class _FakeAgent:
        def __init__(self):
            self.checkpointer = object()

        def stream(self, payload, config):
            assert payload == {"messages": [{"role": "user", "content": "测试恢复"}]}
            assert config["configurable"]["thread_id"] == "ai-search-task-2"
            assert config["configurable"]["checkpoint_ns"] == ""
            yield {"messages": []}

        def get_state(self, config):
            assert config["configurable"]["thread_id"] == "ai-search-task-2"
            assert config["configurable"]["checkpoint_ns"] == ""
            assert config["configurable"]["__pregel_checkpointer"] is self.checkpointer
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage_arg, task_id_arg: _FakeAgent())

    result = service._run_main_agent("task-2", "ai-search-task-2", {"messages": [{"role": "user", "content": "测试恢复"}]})

    assert result == {"interrupted": False, "values": {"messages": [{"role": "assistant", "content": "ok"}]}}


def test_main_agent_config_for_resume_targets_latest_interrupt_checkpoint(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    assert storage.put_ai_search_checkpoint(
        {
            "thread_id": "ai-search-task-3",
            "checkpoint_ns": "",
            "checkpoint_id": "0001",
            "checkpoint_json": json.dumps({"id": "0001"}),
            "metadata_json": json.dumps({"source": "main"}),
        }
    )
    assert storage.put_ai_search_checkpoint(
        {
            "thread_id": "ai-search-task-3",
            "checkpoint_ns": "",
            "checkpoint_id": "0002",
            "checkpoint_json": json.dumps({"id": "0002"}),
            "metadata_json": json.dumps({"source": "main"}),
        }
    )
    assert storage.put_ai_search_checkpoint_writes(
        [
            {
                "thread_id": "ai-search-task-3",
                "checkpoint_ns": "",
                "checkpoint_id": "0001",
                "task_id": "writer-1",
                "write_idx": -3,
                "channel": "__interrupt__",
                "typed_value_json": json.dumps({"type": "msgpack", "data": ""}),
            },
            {
                "thread_id": "ai-search-task-3",
                "checkpoint_ns": "",
                "checkpoint_id": "0002",
                "task_id": "writer-2",
                "write_idx": -4,
                "channel": "__resume__",
                "typed_value_json": json.dumps({"type": "msgpack", "data": ""}),
            },
        ]
    )

    config = service._main_agent_config("ai-search-task-3", for_resume=True)

    assert config["configurable"]["checkpoint_ns"] == ""
    assert config["configurable"]["checkpoint_id"] == "0001"


def test_stream_message_emits_run_started_keepalive_and_assistant_completion(monkeypatch, tmp_path):
    service, _storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    monkeypatch.setattr(ai_search_service_module, "MAIN_AGENT_PROGRESS_POLL_SECONDS", 0.01)

    class _FakeChunk:
        def __init__(self, content: str):
            self.content = content

    class _FakeState:
        values = {"messages": [{"role": "assistant", "content": "已生成计划。"}]}
        interrupts = ()

    class _FakeAgent:
        def __init__(self):
            self.checkpointer = object()

        async def astream(self, payload, config=None, **kwargs):
            assert payload == {"messages": [{"role": "user", "content": "请开始规划"}]}
            assert config["configurable"]["thread_id"].startswith("ai-search-")
            assert kwargs["stream_mode"] == ["messages", "custom"]
            await asyncio.sleep(0.03)
            yield ((), "messages", (_FakeChunk("已生成计划。"), {}))

        def get_state(self, config):
            assert config["configurable"]["__pregel_checkpointer"] is self.checkpointer
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: _FakeAgent())

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "请开始规划")))
    parsed = _parse_data_events(events)

    assert events[0].startswith("data: ")
    assert parsed[0]["type"] == "run.started"
    assert any(item.startswith(": keepalive") for item in events)
    assert any(event["type"] == "assistant.message.delta" for event in parsed)
    assert any(event["type"] == "assistant.message.completed" for event in parsed)
    assert events[-1].startswith("data: ")
    assert "run.completed" in events[-1]


def test_stream_message_dedupes_phase_markers_and_maps_subagent_lifecycle(monkeypatch, tmp_path):
    service, _storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")

    class _FakeState:
        values = {"messages": []}
        interrupts = ()

    class _FakeAgent:
        def __init__(self):
            self.checkpointer = object()

        async def astream(self, payload, config=None, **kwargs):
            assert payload == {"messages": [{"role": "user", "content": "开始处理"}]}
            yield ((), "custom", {"type": "phase.changed", "payload": {"phase": PHASE_DRAFTING_PLAN}})
            yield ((), "custom", {"type": "phase.changed", "payload": {"phase": PHASE_DRAFTING_PLAN}})
            yield ((), "custom", {"type": "subagent.started", "payload": {"name": "search-elements"}})
            yield ((), "custom", {"type": "subagent.completed", "payload": {"name": "search-elements"}})

        def get_state(self, config):
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: _FakeAgent())

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "开始处理")))
    parsed = _parse_data_events(events)

    assert [event["type"] for event in parsed].count("phase.changed") == 1
    assert any(event["type"] == "subagent.started" and event["payload"]["label"] == "检索要素整理" for event in parsed)
    assert any(event["type"] == "subagent.completed" and event["payload"]["label"] == "检索要素整理" for event in parsed)


def test_stream_feature_table_uses_bound_feature_agent_and_persists_outputs(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    storage.create_ai_search_plan(_plan_record(created.sessionId, plan_version=1, status="confirmed", title="测试目标"))
    storage.upsert_ai_search_documents(
        [
            {
                "document_id": "doc-1",
                "task_id": created.sessionId,
                "plan_version": 1,
                "pn": "CN1",
                "title": "文献1",
                "abstract": "",
                "stage": "selected",
                "user_pinned": False,
                "user_removed": False,
                "coarse_status": "kept",
                "close_read_status": "selected",
                "key_passages_json": [{"passage": "证据"}],
            }
        ]
    )
    _set_phase(
        storage,
        created.sessionId,
        PHASE_GENERATE_FEATURE_TABLE,
        active_plan_version=1,
        current_feature_table_id=None,
    )

    class _FakeFeatureAgent:
        def invoke(self, payload):
            assert "生成特征对比表" in payload["messages"][0]["content"]
            storage.create_ai_search_feature_table(
                {
                    "feature_table_id": "ft-new",
                    "task_id": created.sessionId,
                    "plan_version": 1,
                    "status": "completed",
                    "table_json": [{"feature": "A"}],
                    "summary_markdown": "新特征表",
                }
            )
            storage.create_ai_search_message(
                {
                    "message_id": "msg-feature-result",
                    "task_id": created.sessionId,
                    "plan_version": 1,
                    "role": "assistant",
                    "kind": "feature_compare_result",
                    "content": "可以结束",
                    "metadata": {
                        "table_rows": [{"feature": "A"}],
                        "summary_markdown": "新特征表",
                        "overall_findings": "可以结束",
                        "coverage_gaps": [],
                        "follow_up_search_hints": [],
                        "creativity_readiness": "ready",
                        "readiness_rationale": "证据充分",
                    },
                }
            )
            storage.update_task(
                created.sessionId,
                metadata=merge_ai_search_meta(
                    storage.get_task(created.sessionId),
                    current_phase=PHASE_GENERATE_FEATURE_TABLE,
                    active_plan_version=1,
                    current_feature_table_id="ft-new",
                    current_task="generate_feature_table",
                ),
            )
            return {"messages": [{"role": "assistant", "content": "done"}]}

    monkeypatch.setattr(
        ai_search_service_module,
        "build_feature_comparer_agent",
        lambda storage_arg, task_id_arg: (
            _FakeFeatureAgent()
            if storage_arg is storage and task_id_arg == created.sessionId
            else (_ for _ in ()).throw(AssertionError("unexpected feature agent binding"))
        ),
    )

    events = asyncio.run(_collect_stream(service.stream_feature_table(created.sessionId, "guest_ai_search", 1)))
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert snapshot.phase == "completed"
    assert snapshot.featureTable is not None
    assert snapshot.featureTable["feature_table_id"] == "ft-new"
    assert any("feature_table.updated" in item for item in events)


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

    context = AiSearchAgentContext(storage, created.sessionId)

    assert context.current_search_elements()["applicants"] == ["杭州海康威视数字技术股份有限公司"]
    assert context.current_search_elements()["filing_date"] == "2024-03-01"
    assert context.current_search_elements()["priority_date"] == "2023-10-15"


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
    payload = seed_search_elements_from_analysis(
        _build_analysis_payload(),
        _build_patent_payload(),
    )

    assert payload["status"] == "complete"
    assert payload["applicants"] == ["杭州海康威视数字技术股份有限公司"]
    assert payload["filing_date"] == "2024-03-01"
    assert payload["priority_date"] == "2023-10-15"
    assert payload["search_elements"][0]["element_name"] == "异常检测"
    assert payload["search_elements"][0]["notes"] == "核心算法要素"


def test_seed_search_elements_from_analysis_keeps_optional_fields_missing():
    payload = seed_search_elements_from_analysis(
        _build_analysis_payload(include_semantic=False),
        _build_patent_payload(with_applicants=False, with_dates=False),
    )

    assert payload["status"] == "complete"
    assert payload["applicants"] == []
    assert payload["filing_date"] is None
    assert payload["priority_date"] is None
    assert "申请日或优先权日" in payload["missing_items"]
    assert "未提供申请人" in payload["clarification_summary"]


def test_load_source_patent_data_falls_back_to_r2_when_local_patent_json_missing(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    analysis_task = _create_completed_analysis_task(storage, owner_id="guest_ai_search", tmp_path=tmp_path)
    patent_path = Path(str(analysis_task.output_dir)) / "patent.json"
    patent_path.unlink()
    patent_payload = {
        **_build_patent_payload(),
        "claims": [
            {
                "claim_id": "1",
                "claim_type": "independent",
                "claim_text": "一种异常检测系统，包括处理器和存储器。",
                "parent_claim_ids": [],
            }
        ],
    }
    storage.update_task(
        analysis_task.id,
        metadata={
            "output_files": {
                "json": str(Path(analysis_task.output_dir) / "analysis.json"),
                "pn": "CN123456A",
                "patent_r2_key": "patent/CN123456A/patent.json",
            }
        },
    )

    class _FakeR2Storage:
        def get_bytes(self, key: str):
            assert key == "patent/CN123456A/patent.json"
            return json.dumps(patent_payload, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr(ai_search_context_module, "_build_r2_storage", lambda: _FakeR2Storage())

    created = service.create_session("guest_ai_search")
    storage.update_task(
        created.sessionId,
        metadata=merge_ai_search_meta(storage.get_task(created.sessionId), source_task_id=analysis_task.id),
    )

    payload = AiSearchAgentContext(storage, created.sessionId).load_source_patent_data()

    assert payload["bibliographic_data"]["publication_number"] == "CN123456A"
    assert payload["claims"][0]["claim_id"] == "1"


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
        patent_payload={
            **_build_patent_payload(),
            "claims": [
                {
                    "claim_id": "1",
                    "claim_type": "independent",
                    "claim_text": "一种异常检测系统，包括处理器和存储器。",
                    "parent_claim_ids": [],
                }
            ],
        },
    )

    def _fake_planning(task_id, thread_id, payload):
        assert thread_id == f"ai-search-{task_id}"
        assert "AI 分析结果" in payload["messages"][0]["content"]
        storage.create_ai_search_plan(
            _plan_record(task_id, plan_version=1, status="awaiting_confirmation", title="基于分析结果的检索计划")
        )
        storage.create_ai_search_message(
            {
                "message_id": "msg-plan-confirmation",
                "task_id": task_id,
                "plan_version": 1,
                "role": "assistant",
                "kind": "plan_confirmation",
                "content": _plan_record(task_id, plan_version=1, status="awaiting_confirmation", title="基于分析结果的检索计划")["review_markdown"],
                "stream_status": "completed",
                "metadata": {
                    "plan_version": 1,
                    "confirmation_label": "实施此计划",
                },
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

    monkeypatch.setattr(service, "_run_main_agent", _fake_planning)
    monkeypatch.setattr(ai_search_service_module, "extract_latest_ai_message", lambda values: "检索草稿已生成，请确认计划。")

    created = service.create_session_from_analysis("guest_ai_search", analysis_task.id)
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    task = storage.get_task(created.sessionId)
    visible_kinds = [message["kind"] for message in snapshot.messages]

    assert snapshot.phase == PHASE_AWAITING_PLAN_CONFIRMATION
    assert snapshot.pendingConfirmation is not None
    assert snapshot.currentPlan is not None
    assert snapshot.currentPlan["reviewMarkdown"].startswith("# 基于分析结果的检索计划")
    assert snapshot.messages[0]["role"] == "user"
    assert "请基于以上信息生成一份可审核的检索计划。" in snapshot.messages[0]["content"]
    assert visible_kinds == ["chat", "plan_confirmation"]
    assert (task.metadata.get("ai_search") or {}).get("seed_mode") == "analysis"


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

    monkeypatch.setattr(service, "_run_main_agent", _fake_planning)
    monkeypatch.setattr(ai_search_service_module, "extract_latest_ai_message", lambda values: "还需要你补充一个核心技术要素。")

    created = service.create_session_from_analysis("guest_ai_search", analysis_task.id)
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    task = storage.get_task(created.sessionId)
    visible_kinds = [message["kind"] for message in snapshot.messages]

    assert snapshot.phase == PHASE_AWAITING_USER_ANSWER
    assert snapshot.pendingQuestion is not None
    assert snapshot.pendingQuestion["question_id"] == "q-seed-1"
    assert AiSearchAgentContext(storage, created.sessionId).current_search_elements()["status"] == "needs_answer"
    assert visible_kinds == ["chat", "question"]
    assert (task.metadata.get("ai_search") or {}).get("seed_mode") == "analysis"
