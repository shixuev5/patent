from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

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
from backend.ai_search import agent_run_service as ai_search_agent_run_service_module
from backend.ai_search import analysis_seed_service as ai_search_analysis_seed_service_module
from backend.ai_search.analysis_seed import (
    build_analysis_seed_user_message,
    build_analysis_sub_plans,
    seed_search_elements_from_analysis,
)
from backend import task_usage_tracking
from backend.ai_search.models import (
    INVALID_SESSION_PHASE_CODE,
    PENDING_QUESTION_EXISTS_CODE,
    PLAN_CONFIRMATION_REQUIRED_CODE,
    RESUME_NOT_AVAILABLE_CODE,
    SEARCH_IN_PROGRESS_CODE,
    STALE_PLAN_CONFIRMATION_CODE,
)
import agents.ai_search.src.context as ai_search_context_module
from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.exceptions import ExecutionQueueTakeoverRequested
from agents.ai_search.src.runtime_context import build_runtime_context
from agents.ai_search.src.subagents.query_executor.tools import build_query_executor_tools
from agents.ai_search.src.state import (
    PHASE_AWAITING_HUMAN_DECISION,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_CLOSE_READ,
    PHASE_COMPLETED,
    PHASE_DRAFTING_PLAN,
    PHASE_EXECUTE_SEARCH,
    PHASE_FEATURE_COMPARISON,
    merge_ai_search_meta,
)
from backend.storage import TaskStatus, TaskType
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage import SQLiteTaskStorage


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
    active_plan_version = int(meta_updates.get("active_plan_version") or ((task.metadata.get("ai_search") or {}).get("active_plan_version") if isinstance(task.metadata, dict) else 0) or 0)
    storage.update_task(
        task_id,
        status=TaskStatus.PAUSED.value if phase in {PHASE_AWAITING_USER_ANSWER, PHASE_AWAITING_PLAN_CONFIRMATION, PHASE_AWAITING_HUMAN_DECISION} else TaskStatus.PROCESSING.value,
        metadata=merge_ai_search_meta(task, current_phase=phase, **meta_updates),
    )
    if active_plan_version > 0:
        run = storage.get_ai_search_run(task_id, plan_version=active_plan_version)
        if run:
            run_updates = {
                "phase": phase,
                "status": TaskStatus.PAUSED.value if phase in {PHASE_AWAITING_USER_ANSWER, PHASE_AWAITING_PLAN_CONFIRMATION, PHASE_AWAITING_HUMAN_DECISION} else TaskStatus.PROCESSING.value,
            }
            if "current_task" in meta_updates:
                run_updates["active_retrieval_todo_id"] = meta_updates.get("current_task")
            if "active_batch_id" in meta_updates:
                run_updates["active_batch_id"] = meta_updates.get("active_batch_id")
            if "selected_document_count" in meta_updates:
                run_updates["selected_document_count"] = int(meta_updates.get("selected_document_count") or 0)
            state_keys = {
                "execution_round_count",
                "no_progress_round_count",
                "last_selected_count",
                "last_readiness",
                "last_gap_signature",
                "processed_execution_summary_count",
                "human_decision_reason",
                "human_decision_summary",
            }
            if any(key in meta_updates for key in state_keys):
                human_decision_state = dict(run.get("human_decision_state") or {})
                for key in state_keys:
                    if key in meta_updates:
                        human_decision_state[key] = meta_updates.get(key)
                run_updates["human_decision_state"] = human_decision_state
            storage.update_ai_search_run(task_id, str(run.get("run_id") or ""), **run_updates)


def _set_planner_draft(storage: SQLiteTaskStorage, task_id: str, *, review_markdown: str = "# 计划") -> None:
    task = storage.get_task(task_id)
    assert task is not None
    storage.update_task(
        task_id,
        metadata=merge_ai_search_meta(
            task,
            planner_draft={
                "draft_id": "draft-1",
                "draft_version": 1,
                "phase": PHASE_DRAFTING_PLAN,
                "review_markdown": review_markdown,
                "execution_spec": _plan_record(task_id)["execution_spec_json"],
            },
        ),
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


def _create_run(storage: SQLiteTaskStorage, task_id: str, *, plan_version: int = 1, phase: str = PHASE_DRAFTING_PLAN) -> str:
    run_id = f"{task_id}-run-{plan_version}"
    storage.create_ai_search_run(
        {
            "run_id": run_id,
            "task_id": task_id,
            "plan_version": plan_version,
            "phase": phase,
            "status": TaskStatus.PROCESSING.value,
        }
    )
    return run_id


def _create_batch(
    storage: SQLiteTaskStorage,
    task_id: str,
    run_id: str,
    *,
    plan_version: int = 1,
    batch_type: str = "feature_comparison",
    batch_id: str | None = None,
) -> str:
    resolved_batch_id = batch_id or f"{run_id}-{batch_type}-batch"
    storage.create_ai_search_batch(
        {
            "batch_id": resolved_batch_id,
            "run_id": run_id,
            "task_id": task_id,
            "plan_version": plan_version,
            "batch_type": batch_type,
            "status": "loaded",
        }
    )
    return resolved_batch_id


def _seed_run_todos(
    storage: SQLiteTaskStorage,
    task_id: str,
    *,
    plan_version: int = 1,
    phase: str = PHASE_EXECUTE_SEARCH,
    active_todo_id: str | None = None,
    todos: list[dict[str, Any]] | None = None,
    run_updates: dict[str, Any] | None = None,
) -> str:
    run_id = _create_run(storage, task_id, plan_version=plan_version, phase=phase)
    if todos:
        storage.replace_ai_search_retrieval_todos(run_id, task_id, plan_version, todos)
        active_todo = next((item for item in todos if str(item.get("todo_id") or "").strip() == str(active_todo_id or "").strip()), None)
        if isinstance(active_todo, dict) and str(active_todo.get("status") or "").strip() == "failed":
            _create_pending_action(
                storage,
                task_id,
                "resume",
                run_id=run_id,
                payload={
                    "todo_id": str(active_todo.get("todo_id") or "").strip(),
                    "resume_from": str(active_todo.get("resume_from") or "").strip(),
                    "last_error": str(active_todo.get("last_error") or "").strip(),
                    "attempt_count": int(active_todo.get("attempt_count") or 0),
                    "checkpoint_ref": {"thread_id": f"ai-search-{task_id}"},
                },
            )
    if active_todo_id:
        storage.update_ai_search_run(task_id, run_id, active_retrieval_todo_id=active_todo_id, **(run_updates or {}))
    elif run_updates:
        storage.update_ai_search_run(task_id, run_id, **run_updates)
    _set_phase(storage, task_id, phase, active_plan_version=plan_version)
    return run_id


def _seed_execution_queue_message(
    storage: SQLiteTaskStorage,
    task_id: str,
    run_id: str,
    *,
    content: str,
    ordinal: int,
    status: str = "pending",
) -> str:
    queue_message_id = f"queue-{ordinal}"
    storage.create_ai_search_execution_queue_message(
        {
            "queue_message_id": queue_message_id,
            "task_id": task_id,
            "run_id": run_id,
            "content": content,
            "ordinal": ordinal,
            "status": status,
        }
    )
    return queue_message_id


def _create_pending_action(
    storage: SQLiteTaskStorage,
    task_id: str,
    action_type: str,
    *,
    run_id: str | None = None,
    plan_version: int | None = None,
    source: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    action_id = f"{task_id}-{action_type}-action"
    storage.create_ai_search_pending_action(
        {
            "action_id": action_id,
            "task_id": task_id,
            "run_id": run_id,
            "plan_version": plan_version,
            "action_type": action_type,
            "status": "pending",
            "source": source,
            "payload": payload or {},
        }
    )
    return action_id


def test_create_session_and_snapshot(monkeypatch, tmp_path):
    service, _storage = _mount_service(monkeypatch, tmp_path)

    created = service.create_session("guest_ai_search")
    listed = service.list_sessions("guest_ai_search")
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert listed.total == 1
    assert snapshot.run["phase"] == "collecting_requirements"
    assert snapshot.session.taskId == created.taskId
    assert snapshot.conversation["messages"][0]["content"] == "请描述检索目标、核心技术方案、关注特征，并尽量提供申请人、申请日或优先权日等约束条件。"
    assert snapshot.session.pinned is False


def test_create_session_uses_unified_flow_metadata(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)

    created = service.create_session("guest_ai_search")
    task = storage.get_task(created.sessionId)

    assert (task.metadata.get("ai_search") or {}).get("current_phase") == "collecting_requirements"


def test_ai_search_usage_accumulates_across_multiple_stream_calls(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")

    async def _fake_stream_message(session_id: str, owner_id: str, content: str):
        assert session_id == created.sessionId
        assert owner_id == "guest_ai_search"
        assert content == "你好"
        task_usage_tracking.record_llm_usage(
            model="qwen3.5-flash",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            reasoning_tokens=2,
        )
        yield 'data: {"type":"run.completed","payload":{"awaitingUserAction":false,"completionReason":"completed"}}\n\n'

    async def _fake_stream_plan_confirmation(session_id: str, owner_id: str, plan_version: int):
        assert session_id == created.sessionId
        assert owner_id == "guest_ai_search"
        assert plan_version == 1
        task_usage_tracking.record_llm_usage(
            model="qwen3.5-plus",
            prompt_tokens=20,
            completion_tokens=10,
            total_tokens=30,
            reasoning_tokens=4,
        )
        yield 'data: {"type":"run.completed","payload":{"awaitingUserAction":false,"completionReason":"completed"}}\n\n'

    monkeypatch.setattr(service.agent_runs, "stream_message", _fake_stream_message)
    monkeypatch.setattr(service.agent_runs, "stream_plan_confirmation", _fake_stream_plan_confirmation)

    message_events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "你好")))
    confirm_events = asyncio.run(_collect_stream(service.stream_plan_confirmation(created.sessionId, "guest_ai_search", 1)))

    assert any("run.completed" in item for item in message_events)
    assert any("run.completed" in item for item in confirm_events)

    usage_row = storage.get_task_llm_usage(created.sessionId)
    assert usage_row is not None
    assert usage_row["task_type"] == TaskType.AI_SEARCH.value
    assert usage_row["prompt_tokens"] == 30
    assert usage_row["completion_tokens"] == 15
    assert usage_row["total_tokens"] == 45
    assert usage_row["reasoning_tokens"] == 6
    assert usage_row["llm_call_count"] == 2
    assert set((usage_row["model_breakdown_json"] or {}).keys()) == {"qwen3.5-flash", "qwen3.5-plus"}


def test_ai_search_usage_skips_empty_stream_without_existing_record(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")

    async def _fake_stream_complete(session_id: str, owner_id: str):
        assert session_id == created.sessionId
        assert owner_id == "guest_ai_search"
        yield 'data: {"type":"run.completed","payload":{"awaitingUserAction":false,"completionReason":"completed"}}\n\n'

    monkeypatch.setattr(service.agent_runs, "stream_decision_complete", _fake_stream_complete)

    events = asyncio.run(_collect_stream(service.stream_decision_complete(created.sessionId, "guest_ai_search")))

    assert any("run.completed" in item for item in events)
    assert storage.get_task_llm_usage(created.sessionId) is None


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
    assert exc_info.value.detail["code"] == "SESSION_DELETE_BLOCKED"
    assert exc_info.value.detail["message"] == "这个检索还在执行中。"


def test_stream_message_rejects_when_search_is_running(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_phase(storage, created.sessionId, PHASE_EXECUTE_SEARCH)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "继续调整计划")))

    assert exc_info.value.detail["code"] == SEARCH_IN_PROGRESS_CODE


def test_append_execution_queue_message_requires_execution_phase(monkeypatch, tmp_path):
    service, _storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")

    with pytest.raises(HTTPException) as exc_info:
        service.append_execution_queue_message(created.sessionId, "guest_ai_search", "请把日期范围缩窄到最近五年")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == INVALID_SESSION_PHASE_CODE


def test_append_and_delete_execution_queue_message_roundtrip(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    run_id = _create_run(storage, created.sessionId, plan_version=1, phase=PHASE_EXECUTE_SEARCH)
    _set_phase(storage, created.sessionId, PHASE_EXECUTE_SEARCH, active_plan_version=1)

    appended = service.append_execution_queue_message(created.sessionId, "guest_ai_search", "补充申请人限制")

    assert [item.content for item in appended.items] == ["补充申请人限制"]
    assert appended.items[0].ordinal == 1

    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    assert snapshot.executionMessageQueue["items"][0]["runId"] == run_id

    queue_message_id = appended.items[0].queueMessageId
    deleted = service.delete_execution_queue_message(created.sessionId, "guest_ai_search", queue_message_id)

    assert deleted.items == []
    stored = storage.get_ai_search_execution_queue_message(queue_message_id)
    assert stored is not None
    assert stored["status"] == "deleted"


def test_delete_execution_queue_message_rejects_consumed_item(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    run_id = _create_run(storage, created.sessionId, plan_version=1, phase=PHASE_EXECUTE_SEARCH)
    _set_phase(storage, created.sessionId, PHASE_EXECUTE_SEARCH, active_plan_version=1)
    queue_message_id = _seed_execution_queue_message(
        storage,
        created.sessionId,
        run_id,
        content="补充排除项",
        ordinal=1,
        status="consumed",
    )

    with pytest.raises(HTTPException) as exc_info:
        service.delete_execution_queue_message(created.sessionId, "guest_ai_search", queue_message_id)

    assert exc_info.value.status_code == 409


def test_stream_message_keeps_unified_flow_without_structured_claim_source(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        service,
        "_run_main_agent",
        lambda task_id, thread_id, payload, **kwargs: {"awaiting_user_action": False, "completion_reason": "completed", "values": {"messages": []}},
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
    _seed_run_todos(
        storage,
        created.sessionId,
        phase=PHASE_EXECUTE_SEARCH,
        active_todo_id="plan_1:sub_plan_1:step_1",
        todos=[{"todo_id": "plan_1:sub_plan_1:step_1", "sub_plan_id": "sub_plan_1", "step_id": "step_1", "title": "执行步骤 1", "description": "目的：验证首轮召回", "status": "failed", "resume_from": "run_execution_step"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "继续上次失败的执行")))

    assert exc_info.value.detail["code"] == SEARCH_IN_PROGRESS_CODE


def test_stream_resume_continues_failed_execution_todo(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _seed_run_todos(
        storage,
        created.sessionId,
        phase=PHASE_EXECUTE_SEARCH,
        active_todo_id="plan_1:sub_plan_1:step_1",
        todos=[{"todo_id": "plan_1:sub_plan_1:step_1", "sub_plan_id": "sub_plan_1", "step_id": "step_1", "title": "执行步骤 1", "description": "目的：验证首轮召回", "status": "failed", "resume_from": "run_execution_step", "last_error": "timeout"}],
    )

    monkeypatch.setattr(
        service,
        "_run_main_agent",
        lambda task_id, thread_id, payload, **kwargs: (
            {"awaiting_user_action": False, "completion_reason": "completed", "values": {"messages": [{"role": "assistant", "content": "继续恢复检索。"}]}}
            if "继续当前失败的 AI 检索执行" in payload["messages"][0]["content"]
            else (_ for _ in ()).throw(AssertionError("unexpected resume payload"))
        ),
    )
    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage_arg, task_id_arg: None)
    monkeypatch.setattr(
        ai_search_agent_run_service_module,
        "extract_latest_ai_message",
        lambda values: values["messages"][-1]["content"],
    )
    notify_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        service,
        "notify_task_terminal_status",
        lambda task_id, terminal_status, **kwargs: notify_calls.append(
            {"task_id": task_id, "terminal_status": terminal_status, **kwargs}
        ),
    )

    events = asyncio.run(_collect_stream(service.stream_resume(created.sessionId, "guest_ai_search")))
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert snapshot.run["phase"] == PHASE_EXECUTE_SEARCH


def test_run_execution_step_commit_consumes_queued_messages_and_requests_takeover(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    storage.create_ai_search_plan(_plan_record(created.sessionId, plan_version=1, status="confirmed", title="测试计划"))
    _seed_run_todos(
        storage,
        created.sessionId,
        plan_version=1,
        phase=PHASE_EXECUTE_SEARCH,
        active_todo_id="plan_1:sub_plan_1:step_1",
        todos=[
            {
                "todo_id": "plan_1:sub_plan_1:step_1",
                "sub_plan_id": "sub_plan_1",
                "step_id": "step_1",
                "title": "执行步骤 1",
                "description": "目的：验证首轮召回",
                "status": "in_progress",
            }
        ],
    )
    _set_phase(storage, created.sessionId, PHASE_EXECUTE_SEARCH, active_plan_version=1, current_task="plan_1:sub_plan_1:step_1")
    run = storage.get_ai_search_run(created.sessionId, plan_version=1)
    assert run is not None
    _seed_execution_queue_message(storage, created.sessionId, str(run.get("run_id") or ""), content="缩窄时间范围到最近五年", ordinal=1)
    _seed_execution_queue_message(storage, created.sessionId, str(run.get("run_id") or ""), content="优先关注申请人为华为", ordinal=2)

    context = AiSearchAgentContext(storage, created.sessionId)
    runtime = SimpleNamespace(context=build_runtime_context(context.storage, context.task_id))

    with pytest.raises(ExecutionQueueTakeoverRequested) as exc_info:
        context.persist_execution_step_summary(
            {
                "candidate_pool_size": 12,
                "new_unique_candidates": 4,
            },
            plan_version=1,
            runtime=runtime.context,
        )

    exc = exc_info.value
    assert "缩窄时间范围到最近五年" in exc.takeover_prompt
    assert "优先关注申请人为华为" in exc.takeover_prompt

    queue_rows = storage.list_ai_search_execution_queue_messages(created.sessionId, str(run.get("run_id") or ""))
    assert [item["status"] for item in queue_rows] == ["consumed", "consumed"]

    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    assert snapshot.session.phase == PHASE_DRAFTING_PLAN
    assert snapshot.executionMessageQueue["items"] == []
    queued_messages = [
        item
        for item in snapshot.conversation["messages"]
        if item["role"] == "user" and item.get("metadata", {}).get("queuedDuringExecution")
    ]
    assert [item["content"] for item in queued_messages] == ["缩窄时间范围到最近五年", "优先关注申请人为华为"]
    assert snapshot.retrieval["activeTodo"] is None


def test_stream_resume_rejects_when_no_failed_execution_todo(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _seed_run_todos(
        storage,
        created.sessionId,
        phase=PHASE_EXECUTE_SEARCH,
        active_todo_id="plan_1:sub_plan_1:step_1",
        todos=[{"todo_id": "plan_1:sub_plan_1:step_1", "sub_plan_id": "sub_plan_1", "step_id": "step_1", "title": "执行步骤 1", "description": "目的：验证首轮召回", "status": "in_progress", "resume_from": "run_execution_step"}],
    )

    events = asyncio.run(_collect_stream(service.stream_resume(created.sessionId, "guest_ai_search")))
    assert events == []


def test_stream_message_rejects_when_question_pending(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_phase(storage, created.sessionId, PHASE_AWAITING_USER_ANSWER)
    _create_pending_action(
        storage,
        created.sessionId,
        "question",
        payload={"question_id": "q-1", "prompt": "请补充一个核心特征"},
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
    _set_phase(storage, created.sessionId, PHASE_AWAITING_PLAN_CONFIRMATION, active_plan_version=2)
    _create_pending_action(
        storage,
        created.sessionId,
        "plan_confirmation",
        payload={"plan_version": 2, "confirmationLabel": "实施此计划"},
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_collect_stream(service.stream_plan_confirmation(created.sessionId, "guest_ai_search", 1)))

    assert exc_info.value.detail["code"] == STALE_PLAN_CONFIRMATION_CODE


def test_stream_plan_confirmation_emits_run_failed_when_resume_does_not_confirm_plan(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    storage.create_ai_search_plan(
        _plan_record(created.sessionId, plan_version=1, status="awaiting_confirmation", title="待确认计划")
    )
    _set_phase(storage, created.sessionId, PHASE_AWAITING_PLAN_CONFIRMATION, active_plan_version=1)
    _create_pending_action(
        storage,
        created.sessionId,
        "plan_confirmation",
        payload={"plan_version": 1, "confirmationLabel": "实施此计划"},
    )

    monkeypatch.setattr(
        service,
        "_run_main_agent",
        lambda task_id, thread_id, payload, **kwargs: {"awaiting_user_action": False, "completion_reason": "completed", "values": {"messages": []}},
    )
    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage_arg, task_id_arg: None)

    events = asyncio.run(_collect_stream(service.stream_plan_confirmation(created.sessionId, "guest_ai_search", 1)))

    assert any("run.failed" in item for item in events)
    assert any(PLAN_CONFIRMATION_REQUIRED_CODE in item for item in events)
    assert not any("run.completed" in item for item in events)


def test_stream_document_review_replaces_selected_set_via_manual_review(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    storage.create_ai_search_plan(
        _plan_record(created.sessionId, plan_version=1, status="confirmed", title="测试目标")
    )
    run_id = _create_run(storage, created.sessionId, plan_version=1, phase=PHASE_AWAITING_HUMAN_DECISION)
    storage.upsert_ai_search_documents(
        [
            {
                "run_id": run_id,
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
            },
            {
                "run_id": run_id,
                "document_id": "doc-2",
                "task_id": created.sessionId,
                "plan_version": 1,
                "pn": "CN2",
                "title": "文献2",
                "abstract": "",
                "stage": "shortlisted",
                "user_pinned": False,
                "user_removed": False,
                "coarse_status": "kept",
                "close_read_status": "rejected",
            },
        ]
    )
    batch_id = _create_batch(storage, created.sessionId, run_id, plan_version=1, batch_type="feature_comparison")
    storage.create_ai_search_feature_comparison(
        {
            "feature_comparison_id": "ft-1",
            "run_id": run_id,
            "batch_id": batch_id,
            "task_id": created.sessionId,
            "plan_version": 1,
            "table_json": [{"feature": "A"}],
        }
    )
    _set_phase(
        storage,
        created.sessionId,
        PHASE_AWAITING_HUMAN_DECISION,
        active_plan_version=1,
        current_feature_comparison_id="ft-1",
        active_batch_id=batch_id,
        human_decision_reason="no_progress_limit_reached",
        human_decision_summary="需要人工决策",
    )
    _create_pending_action(
        storage,
        created.sessionId,
        "human_decision",
        run_id=run_id,
        plan_version=1,
        payload={
            "available": True,
            "reason": "no_progress_limit_reached",
            "summary": "需要人工决策",
            "roundCount": 2,
            "noProgressRoundCount": 1,
            "selectedCount": 1,
            "recommendedActions": ["continue_search", "complete_current_results"],
        },
    )

    def _fake_run_main_agent(task_id: str, thread_id: str, payload: Any, *, for_resume: bool = False):
        assert task_id == created.sessionId
        assert for_resume is False
        context = AiSearchAgentContext(storage, task_id)
        assert "人工送审复核后的继续执行" in payload["messages"][0]["content"]
        batch_id_value = str(context.active_batch_id(1) or "")
        storage.update_ai_search_document(
            created.sessionId,
            1,
            "doc-2",
            stage="selected",
            user_pinned=True,
            user_removed=False,
            close_read_status="selected",
            close_read_reason="人工送审复核通过",
            key_passages_json=[{"passage": "证据段"}],
        )
        storage.update_ai_search_batch(batch_id_value, status="committed")
        feature_batch_id = _create_batch(storage, created.sessionId, run_id, plan_version=1, batch_type="feature_comparison", batch_id="ft-new-batch")
        storage.create_ai_search_feature_comparison(
            {
                "feature_comparison_id": "ft-new",
                "run_id": run_id,
                "batch_id": feature_batch_id,
                "task_id": created.sessionId,
                "plan_version": 1,
                "table_json": [{"feature": "B"}],
            }
        )
        context.create_pending_action(
            "human_decision",
            {
                "available": True,
                "reason": "manual_document_review",
                "summary": "人工文献复核已完成，请决定继续检索或按当前结果完成。",
                "roundCount": 2,
                "noProgressRoundCount": 1,
                "selectedCount": 1,
                "recommendedActions": ["continue_search", "complete_current_results"],
            },
            run_id=run_id,
            plan_version=1,
            source="human_decision_gate",
        )
        context.update_task_phase(
            PHASE_AWAITING_HUMAN_DECISION,
            active_plan_version=1,
            run_id=run_id,
            selected_document_count=1,
            current_task=None,
        )
        return {
            "awaiting_user_action": True,
            "completion_reason": "awaiting_human_decision",
            "values": {"messages": []},
        }

    monkeypatch.setattr(service, "_run_main_agent", _fake_run_main_agent)
    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda *_args, **_kwargs: None)

    events = asyncio.run(_collect_stream(service.stream_document_review(created.sessionId, "guest_ai_search", 1, ["doc-2"], ["doc-1"])))
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    removed_doc = next(item for item in snapshot.retrieval["documents"]["candidates"] if item["document_id"] == "doc-1")
    selected_doc = next(item for item in snapshot.retrieval["documents"]["selected"] if item["document_id"] == "doc-2")

    assert snapshot.run["phase"] == PHASE_AWAITING_HUMAN_DECISION
    assert removed_doc["stage"] == "shortlisted"
    assert removed_doc["user_removed"] is True
    assert selected_doc["stage"] == "selected"
    assert snapshot.analysis["latestFeatureCompareResult"]["feature_comparison_id"] == "ft-new"
    assert any("run.completed" in item for item in events)


def test_stream_document_review_keeps_human_decision_when_selection_becomes_empty(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    storage.create_ai_search_plan(
        _plan_record(created.sessionId, plan_version=1, status="confirmed", title="测试目标")
    )
    run_id = _create_run(storage, created.sessionId, plan_version=1, phase=PHASE_AWAITING_HUMAN_DECISION)
    storage.upsert_ai_search_documents(
        [
            {
                "run_id": run_id,
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
        PHASE_AWAITING_HUMAN_DECISION,
        active_plan_version=1,
        current_feature_comparison_id=None,
        human_decision_reason="no_progress_limit_reached",
        human_decision_summary="需要人工决策",
    )
    _create_pending_action(
        storage,
        created.sessionId,
        "human_decision",
        run_id=run_id,
        plan_version=1,
        payload={
            "available": True,
            "reason": "no_progress_limit_reached",
            "summary": "需要人工决策",
            "roundCount": 1,
            "noProgressRoundCount": 1,
            "selectedCount": 1,
            "recommendedActions": ["continue_search", "complete_current_results"],
        },
    )

    def _fake_run_main_agent(task_id: str, thread_id: str, payload: Any, *, for_resume: bool = False):
        assert task_id == created.sessionId
        assert for_resume is False
        context = AiSearchAgentContext(storage, task_id)
        context.create_pending_action(
            "human_decision",
            {
                "available": True,
                "reason": "manual_document_review",
                "summary": "当前无已选对比文献，请送审候选文献或继续检索。",
                "roundCount": 1,
                "noProgressRoundCount": 1,
                "selectedCount": 0,
                "recommendedActions": ["continue_search", "complete_current_results"],
            },
            run_id=run_id,
            plan_version=1,
            source="human_decision_gate",
        )
        context.update_task_phase(
            PHASE_AWAITING_HUMAN_DECISION,
            active_plan_version=1,
            run_id=run_id,
            selected_document_count=0,
            current_task=None,
        )
        return {
            "awaiting_user_action": True,
            "completion_reason": "awaiting_human_decision",
            "values": {"messages": []},
        }

    monkeypatch.setattr(service, "_run_main_agent", _fake_run_main_agent)

    events = asyncio.run(_collect_stream(service.stream_document_review(created.sessionId, "guest_ai_search", 1, [], ["doc-1"])))
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert snapshot.run["phase"] == PHASE_AWAITING_HUMAN_DECISION
    assert snapshot.retrieval["documents"]["selected"] == []
    assert snapshot.retrieval["documents"]["candidates"][0]["stage"] == "shortlisted"
    assert any("run.completed" in item for item in events)


def test_stream_message_supersedes_waiting_plan(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_planner_draft(storage, created.sessionId)
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
        lambda task_id, thread_id, payload, **kwargs: {"awaiting_user_action": False, "completion_reason": "completed", "values": {"messages": []}},
    )
    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage_arg, task_id_arg: None)

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "把日期范围缩窄到最近五年")))

    updated_plan = storage.get_ai_search_plan(created.sessionId, 1)
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert updated_plan is not None
    assert updated_plan["status"] == "superseded"
    assert snapshot.run["phase"] == PHASE_DRAFTING_PLAN
    assert any("run.failed" in item for item in events)


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

    assert result == {
        "values": {"messages": [{"role": "assistant", "content": "ok"}]},
        "awaiting_user_action": False,
        "completion_reason": "completed",
    }
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

    assert result == {
        "values": {"messages": [{"role": "assistant", "content": "ok"}]},
        "awaiting_user_action": False,
        "completion_reason": "completed",
    }


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

    config = service.agent_runs._main_agent_config("ai-search-task-3", for_resume=True)

    assert config["configurable"]["checkpoint_ns"] == ""
    assert config["configurable"]["checkpoint_id"] == "0001"


def test_stream_message_persists_main_agent_direct_reply(monkeypatch, tmp_path):
    service, _storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_planner_draft(_storage, created.sessionId)
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
            assert kwargs["stream_mode"] == ["updates", "messages", "custom"]
            assert kwargs["version"] == "v2"
            await asyncio.sleep(0.03)
            yield ((), "messages", (_FakeChunk("已生成计划。"), {}))

        def get_state(self, config):
            assert config["configurable"]["__pregel_checkpointer"] is self.checkpointer
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: _FakeAgent())
    monkeypatch.setattr(
        ai_search_agent_run_service_module,
        "extract_latest_ai_message",
        lambda values: values["messages"][-1]["content"],
    )

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "请开始规划")))
    parsed = _parse_data_events(events)

    assert events[0].startswith("data: ")
    assert parsed[0]["type"] == "run.started"
    assert any(item.startswith(": keepalive") for item in events)
    assert any(event["type"] == "message.segment.delta" for event in parsed)
    assert any(
        event["type"] == "message.segment.completed"
        and event["payload"].get("sourceAgent") == "main-agent"
        for event in parsed
    )
    assert events[-1].startswith("data: ")
    assert "run.completed" in events[-1]


def test_stream_message_emits_planner_message_segments_and_persists_review_markdown(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    monkeypatch.setattr(ai_search_service_module, "MAIN_AGENT_PROGRESS_POLL_SECONDS", 0.01)

    class _FakeChunk:
        def __init__(self, content: str):
            self.content = content

    class _FakeState:
        values = {"messages": [{"role": "assistant", "content": "计划已起草完成。"}]}
        interrupts = ()

    class _FakeAgent:
        def __init__(self):
            self.checkpointer = object()

        async def astream(self, payload, config=None, **kwargs):
            planner_ns = ("planner:task-1",)
            planner_meta = {"lc_agent_name": "planner", "langgraph_node": "model"}
            yield (
                (),
                "updates",
                {
                    "agent": {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "task",
                                        "args": {"subagent_type": "planner"},
                                        "id": "call-planner-1",
                                        "type": "tool_call",
                                    }
                                ],
                            )
                        ]
                    }
                },
            )
            yield (planner_ns, "messages", (_FakeChunk("# 检索计划\n\n"), planner_meta))
            yield (planner_ns, "messages", (_FakeChunk("## 检索目标\n测试目标"), planner_meta))
            yield ((), "updates", {"tools": {"messages": [ToolMessage(content="done", tool_call_id="call-planner-1")]}})

        def get_state(self, config):
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: _FakeAgent())
    monkeypatch.setattr(
        ai_search_agent_run_service_module,
        "extract_latest_ai_message",
        lambda values: values["messages"][-1]["content"],
    )

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "请开始规划")))
    parsed = _parse_data_events(events)
    planner_draft = AiSearchAgentContext(storage, created.sessionId).current_planner_draft()

    assert any(
        event["type"] == "message.segment.started"
        and event["payload"].get("sourceAgent") == "planner"
        for event in parsed
    )
    assert any(
        event["type"] == "message.segment.delta"
        and event["payload"].get("sourceAgent") == "planner"
        for event in parsed
    )
    assert any(
        event["type"] == "message.segment.completed"
        and event["payload"].get("sourceAgent") == "planner"
        for event in parsed
    )
    assert planner_draft["review_markdown"] == "# 检索计划\n\n## 检索目标\n测试目标"


def test_stream_message_does_not_persist_message_segment_deltas(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    monkeypatch.setattr(ai_search_service_module, "MAIN_AGENT_PROGRESS_POLL_SECONDS", 0.01)

    class _FakeChunk:
        def __init__(self, content: str):
            self.content = content

    class _FakeState:
        values = {"messages": [{"role": "assistant", "content": "计划已起草完成。"}]}
        interrupts = ()

    class _FakeAgent:
        def __init__(self):
            self.checkpointer = object()

        async def astream(self, payload, config=None, **kwargs):
            planner_ns = ("tools:planner-call-1",)
            planner_meta = {"lc_agent_name": "planner", "langgraph_node": "model"}
            yield (planner_ns, "messages", (_FakeChunk("# 检索计划\n\n"), planner_meta))
            yield (planner_ns, "messages", (_FakeChunk("## 检索目标\n测试目标"), planner_meta))

        def get_state(self, config):
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: _FakeAgent())
    monkeypatch.setattr(
        ai_search_agent_run_service_module,
        "extract_latest_ai_message",
        lambda values: values["messages"][-1]["content"],
    )

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "请开始规划")))
    parsed = _parse_data_events(events)
    stored_events = storage.list_ai_search_stream_events(created.sessionId, after_seq=0)
    stored_types = [str(item.get("event_type") or "") for item in stored_events]

    assert any(event["type"] == "message.segment.delta" for event in parsed)
    assert "message.segment.delta" not in stored_types
    assert "message.segment.started" in stored_types
    assert "message.segment.completed" in stored_types


def test_stream_message_ignores_root_tool_messages_and_only_streams_model_text(monkeypatch, tmp_path):
    service, _storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")

    class ToolMessage:
        def __init__(self, content: str):
            self.content = content

    class _FakeChunk:
        def __init__(self, content: str):
            self.content = content

    class _FakeState:
        values = {"messages": [{"role": "assistant", "content": "最终答复"}]}
        interrupts = ()

    class _FakeAgent:
        def __init__(self):
            self.checkpointer = object()

        async def astream(self, payload, config=None, **kwargs):
            assert payload == {"messages": [{"role": "user", "content": "请开始规划"}]}
            yield ((), "messages", (ToolMessage('{"phase":"drafting_plan"}'), {"lc_agent_name": "ai-search-main-agent-test", "langgraph_node": "tools"}))
            yield ((), "messages", (_FakeChunk("最终答复"), {"lc_agent_name": "ai-search-main-agent-test", "langgraph_node": "model"}))

        def get_state(self, config):
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: _FakeAgent())
    monkeypatch.setattr(
        ai_search_agent_run_service_module,
        "extract_latest_ai_message",
        lambda values: values["messages"][-1]["content"],
    )

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "请开始规划")))
    parsed = _parse_data_events(events)
    deltas = [
        event["payload"]["delta"]
        for event in parsed
        if event["type"] == "message.segment.delta" and event["payload"].get("sourceAgent") == "main-agent"
    ]

    assert deltas == ["最终答复"]


def test_stream_message_emits_run_failed_when_drafting_completes_without_draft(monkeypatch, tmp_path):
    service, _storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")

    class _FakeState:
        values = {"messages": []}
        interrupts = ()

    class _FakeAgent:
        def __init__(self):
            self.checkpointer = object()

        async def astream(self, payload, config=None, **kwargs):
            assert payload == {"messages": [{"role": "user", "content": "请开始规划"}]}
            if False:
                yield None

        def get_state(self, config):
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: _FakeAgent())

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "请开始规划")))

    assert any("run.failed" in item for item in events)
    assert not any("run.completed" in item for item in events)


def test_stream_message_dedupes_phase_markers_and_maps_subagent_lifecycle(monkeypatch, tmp_path):
    service, _storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_planner_draft(_storage, created.sessionId)

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
            yield (
                (),
                "updates",
                {
                    "agent": {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "task",
                                        "args": {"subagent_type": "planner"},
                                        "id": "call-planner-2",
                                        "type": "tool_call",
                                    }
                                ],
                            )
                        ]
                    }
                },
            )
            yield ((), "updates", {"tools": {"messages": [ToolMessage(content="done", tool_call_id="call-planner-2")]}})

        def get_state(self, config):
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: _FakeAgent())

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "开始处理")))
    parsed = _parse_data_events(events)

    assert [event["type"] for event in parsed].count("phase.changed") == 0
    assert parsed[0]["type"] == "run.started"
    subagent_started_index = next(index for index, event in enumerate(parsed) if event["type"] == "process.started")
    subagent_completed_index = next(index for index, event in enumerate(parsed) if event["type"] == "process.completed")
    assert subagent_started_index < subagent_completed_index
    assert any(
        event["type"] == "process.started"
        and event["payload"]["processType"] == "subagent"
        and event["payload"]["label"] == "检索规划"
        for event in parsed
    )
    assert any(
        event["type"] == "process.completed"
        and event["payload"]["processType"] == "subagent"
        and event["payload"]["label"] == "检索规划"
        for event in parsed
    )


def test_stream_message_persists_stage_messages_and_process_events_from_updates(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_planner_draft(storage, created.sessionId)

    class _FakeState:
        values = {"messages": []}
        interrupts = ()

    class _FakeAgent:
        def __init__(self):
            self.checkpointer = object()

        async def astream(self, payload, config=None, **kwargs):
            assert payload == {"messages": [{"role": "user", "content": "开始处理"}]}
            yield (
                ("planner:task-1",),
                "updates",
                {
                    "agent": {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "get_planning_context",
                                        "args": {},
                                        "id": "call-tool-1",
                                        "type": "tool_call",
                                    }
                                ],
                            )
                        ]
                    },
                },
            )
            yield (
                ("planner:task-1",),
                "updates",
                {
                    "tools": {
                        "messages": [
                            ToolMessage(
                                content="ok",
                                tool_call_id="call-tool-1",
                            )
                        ]
                    },
                },
            )

        def get_state(self, config):
            return _FakeState()

    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda storage, task_id: _FakeAgent())

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "开始处理")))
    parsed = _parse_data_events(events)
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert any(event["type"] == "process.started" and event["payload"]["processType"] == "tool" for event in parsed)
    assert any(
        event["type"] == "process.completed"
        and event["payload"]["processType"] == "tool"
        and event["payload"]["summary"] == "读取规划上下文"
        for event in parsed
    )
    assert not any(message["kind"] == "assistant_stage_message" for message in snapshot.conversation["messages"])
    assert snapshot.conversation["processEvents"]
    assert snapshot.conversation["processEvents"][0]["type"] == "process.started"
    assert snapshot.stream["lastEventSeq"] > 0


def test_subscribe_stream_replays_events_after_seq(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    first = storage.append_ai_search_stream_event(
        {
            "event_id": "evt-replay-1",
            "session_id": created.sessionId,
            "task_id": created.sessionId,
            "run_id": None,
            "event_type": "process.started",
            "entity_id": "stage-1",
            "payload": {
                "type": "process.started",
                "sessionId": created.sessionId,
                "taskId": created.sessionId,
                "phase": PHASE_DRAFTING_PLAN,
                "payload": {"eventId": "planner:started", "processType": "subagent", "name": "planner", "label": "检索规划"},
            },
        }
    )
    second = storage.append_ai_search_stream_event(
        {
            "event_id": "evt-replay-2",
            "session_id": created.sessionId,
            "task_id": created.sessionId,
            "run_id": None,
            "event_type": "process.completed",
            "entity_id": "stage-1",
            "payload": {
                "type": "process.completed",
                "sessionId": created.sessionId,
                "taskId": created.sessionId,
                "phase": PHASE_DRAFTING_PLAN,
                "payload": {"eventId": "planner:completed", "processType": "subagent", "name": "planner", "label": "检索规划"},
            },
        }
    )

    events = asyncio.run(_collect_stream(service.subscribe_stream(created.sessionId, "guest_ai_search", after_seq=int(first["seq"]))))
    parsed = _parse_data_events(events)

    assert len(parsed) == 1
    assert parsed[0]["type"] == "process.completed"
    assert parsed[0]["seq"] == int(second["seq"])


def test_stream_feature_comparison_runs_via_main_agent_and_persists_outputs(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    output_dir = Path(storage.get_task(created.sessionId).output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / "ai_search_result_bundle.zip"
    bundle_path.write_bytes(b"PK\x03\x04")
    pdf_path = output_dir / "ai_search_report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    storage.create_ai_search_plan(_plan_record(created.sessionId, plan_version=1, status="confirmed", title="测试目标"))
    run_id = _create_run(storage, created.sessionId, plan_version=1, phase=PHASE_FEATURE_COMPARISON)
    storage.upsert_ai_search_documents(
        [
            {
                "run_id": run_id,
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
    monkeypatch.setattr(
        ai_search_service_module,
        "build_ai_search_terminal_artifacts",
        lambda **kwargs: {
            "pdf": str(pdf_path),
            "bundle_zip": str(bundle_path),
            "feature_comparison_csv": None,
            "classified_documents": [
                {
                    "document_id": "doc-1",
                    "document_type": "X",
                    "report_row_order": 1,
                    "stage": "selected",
                    "pn": "CN1",
                }
            ],
        },
    )
    _set_phase(
        storage,
        created.sessionId,
        PHASE_FEATURE_COMPARISON,
        active_plan_version=1,
        current_feature_comparison_id=None,
    )

    def _fake_run_main_agent(task_id: str, thread_id: str, payload: Any, *, for_resume: bool = False):
        assert task_id == created.sessionId
        assert for_resume is False
        assert "feature comparison" in payload["messages"][0]["content"] or "特征对比" in payload["messages"][0]["content"]
        batch_id = _create_batch(storage, created.sessionId, run_id, plan_version=1, batch_type="feature_comparison", batch_id="ft-new-batch")
        storage.create_ai_search_feature_comparison(
            {
                "feature_comparison_id": "ft-new",
                "run_id": run_id,
                "batch_id": batch_id,
                "task_id": created.sessionId,
                "plan_version": 1,
                "table_json": [{"feature": "A"}],
                "coverage_gaps": [],
                "follow_up_search_hints": [],
                "creativity_readiness": "ready",
            }
        )
        context = AiSearchAgentContext(storage, task_id)
        context.update_task_phase(PHASE_COMPLETED, active_plan_version=1, run_id=run_id, selected_document_count=1, current_task=None)
        return {
            "awaiting_user_action": False,
            "completion_reason": "completed",
            "values": {"messages": []},
        }

    monkeypatch.setattr(service, "_run_main_agent", _fake_run_main_agent)
    notify_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        service,
        "notify_task_terminal_status",
        lambda task_id, terminal_status, **kwargs: notify_calls.append(
            {"task_id": task_id, "terminal_status": terminal_status, **kwargs}
        ),
    )

    events = asyncio.run(_collect_stream(service.stream_feature_comparison(created.sessionId, "guest_ai_search", 1)))
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    documents = storage.list_ai_search_documents(created.sessionId, 1)
    task = storage.get_task(created.sessionId)

    assert snapshot.run["phase"] == "completed"
    assert snapshot.analysis["latestFeatureCompareResult"] is not None
    assert snapshot.analysis["latestFeatureCompareResult"]["feature_comparison_id"] == "ft-new"
    assert [item.attachmentId for item in snapshot.artifacts.attachments] == ["result_bundle", "report_pdf"]
    assert snapshot.artifacts.attachments[0].downloadUrl == f"/api/ai-search/sessions/{created.sessionId}/attachments/result_bundle/download"
    assert snapshot.artifacts.attachments[1].attachmentId == "report_pdf"
    assert snapshot.session.activityState == "none"
    assert documents[0]["document_type"] == "X"
    assert documents[0]["report_row_order"] == 1
    assert task.metadata["output_files"]["bundle_zip"] == str(bundle_path)
    assert any("run.updated" in item for item in events)
    assert notify_calls == [
        {
            "task_id": created.sessionId,
            "terminal_status": PHASE_COMPLETED,
        }
    ]


def test_stream_feature_comparison_enters_human_decision_when_no_progress_limit_hits(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    plan = _plan_record(created.sessionId, plan_version=1, status="confirmed", title="测试目标")
    plan["execution_spec_json"]["execution_policy"].update(
        {
            "max_rounds": 5,
            "max_no_progress_rounds": 1,
            "max_selected_documents": 5,
            "decision_on_exhaustion": True,
        }
    )
    storage.create_ai_search_plan(plan)
    run_id = _create_run(storage, created.sessionId, plan_version=1, phase=PHASE_FEATURE_COMPARISON)
    storage.upsert_ai_search_documents(
        [
            {
                "run_id": run_id,
                "document_id": "doc-1",
                "task_id": created.sessionId,
                "plan_version": 1,
                "pn": "CN1",
                "title": "文献1",
                "abstract": "",
                "stage": "selected",
                "key_passages_json": [{"passage": "证据"}],
            }
        ]
    )
    storage.create_ai_search_execution_summary(
        {
            "summary_id": f"{run_id}-summary-1",
            "run_id": run_id,
            "task_id": created.sessionId,
            "plan_version": 1,
            "todo_id": "plan_1:sub_plan_1:step_1",
            "sub_plan_id": "sub_plan_1",
            "step_id": "step_1",
            "new_unique_candidates": 0,
            "candidate_pool_size": 1,
        }
    )
    _set_phase(
        storage,
        created.sessionId,
        PHASE_FEATURE_COMPARISON,
        active_plan_version=1,
        current_feature_comparison_id=None,
        current_task="feature_comparison",
        execution_round_count=0,
        no_progress_round_count=0,
        last_selected_count=1,
        last_readiness="needs_more_evidence",
        last_gap_signature={"limitation_gap_count": 0, "coverage_gap_count": 1, "follow_up_hint_count": 0, "weak_evidence_count": 0},
        processed_execution_summary_count=0,
    )

    def _fake_run_main_agent(task_id: str, thread_id: str, payload: Any, *, for_resume: bool = False):
        assert task_id == created.sessionId
        assert for_resume is False
        assert "feature comparison" in payload["messages"][0]["content"] or "特征对比" in payload["messages"][0]["content"]
        batch_id = _create_batch(storage, created.sessionId, run_id, plan_version=1, batch_type="feature_comparison", batch_id="ft-handoff-batch")
        storage.create_ai_search_feature_comparison(
            {
                "feature_comparison_id": "ft-handoff",
                "run_id": run_id,
                "batch_id": batch_id,
                "task_id": created.sessionId,
                "plan_version": 1,
                "table_json": [{"feature": "A"}],
                "coverage_gaps": [{"claim_id": "1", "limitation_id": "1-L3", "gap_type": "combination_gap"}],
                "follow_up_search_hints": ["补搜实现方式B"],
                "creativity_readiness": "needs_more_evidence",
            }
        )
        context = AiSearchAgentContext(storage, task_id)
        context.create_pending_action(
            "human_decision",
            {
                "available": True,
                "reason": "no_progress_limit_reached",
                "summary": "## 对比结论\n仍需继续检索。",
                "roundCount": 1,
                "noProgressRoundCount": 1,
                "selectedCount": 1,
                "recommendedActions": ["continue_search", "complete_current_results"],
            },
            run_id=run_id,
            plan_version=1,
            source="human_decision_gate",
        )
        context.update_task_phase(
            PHASE_AWAITING_HUMAN_DECISION,
            active_plan_version=1,
            run_id=run_id,
            selected_document_count=1,
            current_task=None,
        )
        return {
            "awaiting_user_action": True,
            "completion_reason": "awaiting_human_decision",
            "values": {"messages": []},
        }

    monkeypatch.setattr(service, "_run_main_agent", _fake_run_main_agent)

    events = asyncio.run(_collect_stream(service.stream_feature_comparison(created.sessionId, "guest_ai_search", 1)))
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert snapshot.run["phase"] == PHASE_AWAITING_HUMAN_DECISION
    assert snapshot.artifacts.attachments == []
    assert snapshot.session.activityState == "paused"
    assert snapshot.conversation["pendingAction"] is not None
    assert snapshot.conversation["pendingAction"]["actionType"] == "human_decision"
    assert snapshot.conversation["pendingAction"]["selectedCount"] == 1
    assert any("pending_action.updated" in item for item in events)


def test_stream_message_rejects_when_human_decision_pending(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_phase(
        storage,
        created.sessionId,
        PHASE_AWAITING_HUMAN_DECISION,
        active_plan_version=1,
        human_decision_reason="no_progress_limit_reached",
        human_decision_summary="需要人工决策",
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "继续跑下去")))

    assert exc_info.value.status_code == 409


def test_stream_decision_continue_resets_counters_and_restarts_planning(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    _set_planner_draft(storage, created.sessionId)
    storage.create_ai_search_plan(_plan_record(created.sessionId, plan_version=1, status="confirmed", title="测试目标"))
    run_id = _create_run(storage, created.sessionId, plan_version=1, phase=PHASE_AWAITING_HUMAN_DECISION)
    _create_pending_action(
        storage,
        created.sessionId,
        "human_decision",
        run_id=run_id,
        payload={
            "available": True,
            "reason": "no_progress_limit_reached",
            "summary": "需要人工决策",
            "roundCount": 3,
            "noProgressRoundCount": 2,
            "selectedCount": 0,
            "recommendedActions": ["continue_search", "complete_current_results"],
        },
    )
    _set_phase(
        storage,
        created.sessionId,
        PHASE_AWAITING_HUMAN_DECISION,
        active_plan_version=1,
        execution_round_count=3,
        no_progress_round_count=2,
        human_decision_reason="no_progress_limit_reached",
        human_decision_summary="需要人工决策",
    )
    def _fake_continue_run_main_agent(task_id: str, thread_id: str, payload: Any, *, for_resume: bool = False):
        assert isinstance(payload, Command)
        assert getattr(payload, "resume", None) == {"decision": "continue_search"}
        assert for_resume is True
        context = AiSearchAgentContext(storage, task_id)
        context.resolve_pending_action("human_decision", resolution={"decision": "continue_search"})
        context.reset_execution_control(1, clear_human_decision=True)
        context.update_task_phase(
            PHASE_DRAFTING_PLAN,
            active_plan_version=1,
            run_id=run_id,
            current_task=None,
        )
        return {
            "awaiting_user_action": False,
            "completion_reason": "completed",
            "values": {"messages": [{"role": "assistant", "content": "我会重新起草计划。"}]},
        }

    monkeypatch.setattr(service, "_run_main_agent", _fake_continue_run_main_agent)
    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai_search_agent_run_service_module, "extract_latest_ai_message", lambda values: values["messages"][-1]["content"])

    events = asyncio.run(_collect_stream(service.stream_decision_continue(created.sessionId, "guest_ai_search")))
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    run = storage.get_ai_search_run(created.sessionId, plan_version=1)
    run_state = dict(run.get("human_decision_state") or {}) if run else {}

    assert snapshot.run["phase"] == PHASE_DRAFTING_PLAN
    assert snapshot.conversation["pendingAction"] is None or snapshot.conversation["pendingAction"]["actionType"] != "human_decision"
    assert int(run_state.get("execution_round_count") or 0) == 0
    assert any("run.completed" in item for item in events)


def test_stream_decision_complete_finalizes_existing_feature_comparison(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    created = service.create_session("guest_ai_search")
    output_dir = Path(storage.get_task(created.sessionId).output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / "ai_search_result_bundle.zip"
    bundle_path.write_bytes(b"PK\x03\x04")
    pdf_path = output_dir / "ai_search_report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    storage.create_ai_search_plan(_plan_record(created.sessionId, plan_version=1, status="confirmed", title="测试目标"))
    run_id = _create_run(storage, created.sessionId, plan_version=1, phase=PHASE_AWAITING_HUMAN_DECISION)
    storage.upsert_ai_search_documents(
        [
            {
                "run_id": run_id,
                "document_id": "doc-1",
                "task_id": created.sessionId,
                "plan_version": 1,
                "pn": "CN1",
                "title": "文献1",
                "abstract": "",
                "stage": "selected",
            }
        ]
    )
    batch_id = _create_batch(storage, created.sessionId, run_id, plan_version=1, batch_type="feature_comparison")
    storage.create_ai_search_feature_comparison(
        {
            "feature_comparison_id": "ft-1",
            "run_id": run_id,
            "batch_id": batch_id,
            "task_id": created.sessionId,
            "plan_version": 1,
            "table_json": [{"feature": "A"}],
        }
    )
    _set_phase(
        storage,
        created.sessionId,
        PHASE_AWAITING_HUMAN_DECISION,
        active_plan_version=1,
        current_feature_comparison_id="ft-1",
        active_batch_id=batch_id,
        human_decision_reason="no_progress_limit_reached",
        human_decision_summary="需要人工决策",
    )
    _create_pending_action(
        storage,
        created.sessionId,
        "human_decision",
        run_id=run_id,
        payload={
            "available": True,
            "reason": "no_progress_limit_reached",
            "summary": "需要人工决策",
            "roundCount": 0,
            "noProgressRoundCount": 0,
            "selectedCount": 1,
            "recommendedActions": ["continue_search", "complete_current_results"],
        },
    )
    monkeypatch.setattr(
        ai_search_service_module,
        "build_ai_search_terminal_artifacts",
        lambda **kwargs: {
            "pdf": str(pdf_path),
            "bundle_zip": str(bundle_path),
            "feature_comparison_csv": None,
            "classified_documents": [
                {
                    "document_id": "doc-1",
                    "document_type": "Y",
                    "report_row_order": 1,
                    "stage": "selected",
                    "pn": "CN1",
                }
            ],
        },
    )
    def _fake_complete_run_main_agent(task_id: str, thread_id: str, payload: Any, *, for_resume: bool = False):
        assert isinstance(payload, Command)
        assert getattr(payload, "resume", None) == {"decision": "complete_current_results"}
        assert for_resume is True
        context = AiSearchAgentContext(storage, task_id)
        context.resolve_pending_action("human_decision", resolution={"decision": "complete_current_results"})
        context.update_task_phase(
            PHASE_COMPLETED,
            active_plan_version=1,
            run_id=run_id,
            selected_document_count=1,
            current_task=None,
        )
        return {
            "awaiting_user_action": False,
            "completion_reason": "completed",
            "values": {"messages": [{"role": "assistant", "content": "按当前结果结束。"}]},
        }

    monkeypatch.setattr(service, "_run_main_agent", _fake_complete_run_main_agent)
    monkeypatch.setattr(ai_search_service_module, "build_main_agent", lambda *_args, **_kwargs: None)

    events = asyncio.run(_collect_stream(service.stream_decision_complete(created.sessionId, "guest_ai_search")))
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert snapshot.run["phase"] == PHASE_COMPLETED
    assert [item.attachmentId for item in snapshot.artifacts.attachments] == ["result_bundle", "report_pdf"]
    assert snapshot.artifacts.attachments[0].isPrimary is True
    assert snapshot.artifacts.attachments[1].attachmentId == "report_pdf"
    assert snapshot.session.activityState == "none"
    assert snapshot.conversation["pendingAction"] is None or snapshot.conversation["pendingAction"]["actionType"] != "human_decision"
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


def _build_analysis_payload_with_follow_up() -> dict:
    return {
        "metadata": {
            "task_id": "analysis-task-1",
            "resolved_pn": "CN123456A",
        },
        "report_core": {
            "ai_title": "一种异常检测系统",
            "technical_problem": "降低漏报率",
            "technical_means": "结合时序特征和阈值校正",
            "technical_effects": [
                {
                    "effect": "降低漏报率",
                    "tcs_score": 5,
                    "contributing_features": ["异常检测", "阈值校正"],
                    "dependent_on": [],
                },
                {
                    "effect": "提升复杂噪声场景下的检测稳定性",
                    "tcs_score": 4,
                    "contributing_features": ["时序特征融合"],
                    "dependent_on": ["异常检测"],
                    "rationale": "通过时序特征融合补强主检索命中的边缘场景。",
                },
            ],
        },
        "search_strategy": {
            "search_matrix": [
                {
                    "element_name": "摄像机系统",
                    "keywords_zh": ["摄像机系统"],
                    "keywords_en": ["camera system"],
                    "ipc_cpc_ref": ["H04N 7/18"],
                    "block_id": "A",
                    "effect_cluster_ids": [],
                },
                {
                    "element_name": "异常检测",
                    "keywords_zh": ["异常检测"],
                    "keywords_en": ["anomaly detection"],
                    "ipc_cpc_ref": ["G06V 10/44"],
                    "block_id": "B1",
                    "effect_cluster_ids": ["E1"],
                },
                {
                    "element_name": "阈值校正",
                    "keywords_zh": ["阈值校正"],
                    "keywords_en": ["threshold calibration"],
                    "ipc_cpc_ref": ["G06V 10/56"],
                    "block_id": "B1",
                    "effect_cluster_ids": ["E1"],
                },
                {
                    "element_name": "时序特征融合",
                    "keywords_zh": ["时序特征融合"],
                    "keywords_en": ["temporal feature fusion"],
                    "ipc_cpc_ref": ["G06N 3/08"],
                    "block_id": "C",
                    "effect_cluster_ids": ["E1"],
                },
                {
                    "element_name": "降低漏报率",
                    "keywords_zh": ["降低漏报率"],
                    "keywords_en": ["reduce missed detection"],
                    "ipc_cpc_ref": ["G06V 10/82"],
                    "block_id": "E",
                    "effect_cluster_ids": ["E1"],
                },
            ],
            "semantic_strategy": {
                "queries": [
                    {
                        "block_id": "B1",
                        "effect_cluster_ids": ["E1"],
                        "effect": "降低漏报率",
                        "content": "围绕异常检测与阈值校正降低漏报率",
                    }
                ],
            },
        },
        "report": {},
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
    assert "clarification_summary" not in payload


def test_build_analysis_seed_user_message_renders_structured_effect_groups():
    analysis_payload = _build_analysis_payload_with_follow_up()
    patent_payload = _build_patent_payload()
    seeded = seed_search_elements_from_analysis(analysis_payload, patent_payload)

    message = build_analysis_seed_user_message(analysis_payload, patent_payload, seeded)

    assert "### 核心效果1：降低漏报率" in message
    assert "#### 语义检索文本" in message
    assert "#### 5分效果检索要素表" in message
    assert "#### Block C 条件分支要素表" in message
    assert "| Block B1 | 异常检测 |" in message
    assert "| Block C | 时序特征融合 |" in message
    assert "G06V 10/44" in message
    assert "Step 2（条件触发）" in message
    assert "命中 Block B 或结果过宽时" in message
    assert "进一步检索" not in message
    assert "4分效果" not in message
    assert "效果锚点" not in message
    assert "{'effect':" not in message


def test_build_analysis_sub_plans_adds_block_c_conditional_step():
    sub_plans = build_analysis_sub_plans(_build_analysis_payload_with_follow_up())

    assert len(sub_plans) == 1
    sub_plan = sub_plans[0]
    assert sub_plan["title"] == "降低漏报率"
    assert len(sub_plan["retrieval_steps"]) == 2
    assert len(sub_plan["query_blueprints"]) == 2
    assert sub_plan["retrieval_steps"][0]["title"] == "降低漏报率 / 核心特征击穿"
    assert sub_plan["retrieval_steps"][0]["activation_mode"] == "immediate"
    assert sub_plan["retrieval_steps"][1]["activation_mode"] == "conditional"
    assert sub_plan["retrieval_steps"][1]["depends_on_step_ids"] == ["sub_plan_1_step_1"]
    assert sub_plan["retrieval_steps"][1]["activation_conditions"]["any_of"][0]["signal"] == "primary_goal_reached"
    assert sub_plan["retrieval_steps"][1]["activation_summary"] == "命中 Block B 或结果过宽时，激活 Block C 条件分支做从权防守检索或降噪。"
    assert sub_plan["query_blueprints"][0]["batch_id"] == "sub_plan_1_batch_1"
    assert sub_plan["query_blueprints"][0]["goal"] == "降低漏报率"
    assert sub_plan["query_blueprints"][1]["batch_id"] == "sub_plan_1_batch_2"
    assert "时序特征融合" in sub_plan["query_blueprints"][1]["must_terms_zh"]


def test_create_session_from_analysis_seed_embeds_conditional_block_c_strategy(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    analysis_task = _create_completed_analysis_task(
        storage,
        owner_id="guest_ai_search",
        tmp_path=tmp_path,
        analysis_payload=_build_analysis_payload_with_follow_up(),
        patent_payload=_build_patent_payload(),
    )

    created = service.create_session_from_analysis_seed("guest_ai_search", analysis_task.id)
    messages = storage.list_ai_search_messages(created.sessionId)
    seed_message = next(item for item in messages if item["kind"] == "search_elements_update")
    execution_spec_seed = seed_message["metadata"]["execution_spec_seed"]
    retrieval_steps = execution_spec_seed["sub_plans"][0]["retrieval_steps"]

    assert retrieval_steps[0]["activation_mode"] == "immediate"
    assert retrieval_steps[1]["activation_mode"] == "conditional"
    assert retrieval_steps[1]["activation_summary"].startswith("命中 Block B 或结果过宽时")


def test_create_session_from_analysis_seed_marks_seed_context_as_markdown(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    analysis_task = _create_completed_analysis_task(storage, owner_id="guest_ai_search", tmp_path=tmp_path)

    created = service.create_session_from_analysis_seed("guest_ai_search", analysis_task.id)
    messages = storage.list_ai_search_messages(created.sessionId)
    seed_context = next(
        item for item in messages
        if item["role"] == "user" and item["kind"] == "chat"
    )

    assert seed_context["metadata"]["message_variant"] == "analysis_seed_context"
    assert seed_context["metadata"]["render_mode"] == "markdown"
    assert "## 来源" in seed_context["content"]


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

    def _fake_planning(task_id, thread_id, payload, *, for_resume=False):
        assert thread_id == f"ai-search-{task_id}"
        assert for_resume is False
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
        return {"awaiting_user_action": True, "completion_reason": "awaiting_plan_confirmation", "values": {"messages": []}}

    monkeypatch.setattr(service, "_run_main_agent", _fake_planning)
    monkeypatch.setattr(ai_search_analysis_seed_service_module, "extract_latest_ai_message", lambda values: "检索计划已生成，请确认计划。")

    created = service.create_session_from_analysis("guest_ai_search", analysis_task.id)
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    task = storage.get_task(created.sessionId)
    visible_kinds = [message["kind"] for message in snapshot.conversation["messages"]]

    assert snapshot.run["phase"] == PHASE_AWAITING_PLAN_CONFIRMATION
    assert snapshot.plan["currentPlan"] is not None
    assert snapshot.plan["currentPlan"]["reviewMarkdown"].startswith("# 基于分析结果的检索计划")
    assert snapshot.conversation["messages"][0]["role"] == "user"
    assert "请基于以上信息生成一份可审核的检索计划。" in snapshot.conversation["messages"][0]["content"]
    assert visible_kinds == ["chat", "plan_confirmation"]
    assert (task.metadata.get("ai_search") or {}).get("seed_mode") == "analysis"


def test_create_session_from_analysis_seed_reuses_existing_session(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    analysis_task = _create_completed_analysis_task(
        storage,
        owner_id="guest_ai_search",
        tmp_path=tmp_path,
    )

    first = service.create_session_from_analysis_seed("guest_ai_search", analysis_task.id)
    second = service.create_session_from_analysis_seed("guest_ai_search", analysis_task.id)
    sessions = service.list_sessions("guest_ai_search")
    linked_sessions = [
        item for item in sessions.items
        if item.sourceTaskId == analysis_task.id
    ]

    assert first.reused is False
    assert second.reused is True
    assert second.sessionId == first.sessionId
    assert second.sourceTaskId == analysis_task.id
    assert len(linked_sessions) == 1


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
        return {"awaiting_user_action": True, "completion_reason": "awaiting_user_answer", "values": {"messages": []}}

    monkeypatch.setattr(service, "_run_main_agent", _fake_planning)
    monkeypatch.setattr(ai_search_analysis_seed_service_module, "extract_latest_ai_message", lambda values: "还需要你补充一个核心技术要素。")

    created = service.create_session_from_analysis("guest_ai_search", analysis_task.id)
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    task = storage.get_task(created.sessionId)
    visible_kinds = [message["kind"] for message in snapshot.conversation["messages"]]

    assert snapshot.run["phase"] == PHASE_AWAITING_USER_ANSWER
    assert AiSearchAgentContext(storage, created.sessionId).current_search_elements()["status"] == "needs_answer"
    assert visible_kinds == ["chat", "question"]
    assert (task.metadata.get("ai_search") or {}).get("seed_mode") == "analysis"


def test_stream_analysis_seed_advances_seeded_session(monkeypatch, tmp_path):
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
    created = service.create_session_from_analysis_seed("guest_ai_search", analysis_task.id)

    def _fake_planning(task_id, thread_id, payload, *, for_resume=False):
        assert thread_id == f"ai-search-{task_id}"
        assert for_resume is False
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
                analysis_seed_status="completed",
            ),
            status=TaskStatus.PAUSED.value,
        )
        _create_pending_action(
            storage,
            task_id,
            "plan_confirmation",
            payload={
                "plan_version": 1,
                "plan_summary": _plan_record(task_id, plan_version=1, status="awaiting_confirmation", title="基于分析结果的检索计划")["review_markdown"],
                "confirmation_label": "实施此计划",
            },
        )
        return {"awaiting_user_action": True, "completion_reason": "awaiting_plan_confirmation", "values": {"messages": []}}

    monkeypatch.setattr(service, "_run_main_agent", _fake_planning)
    monkeypatch.setattr(ai_search_analysis_seed_service_module, "extract_latest_ai_message", lambda values: "检索计划已生成，请确认计划。")

    events = asyncio.run(_collect_stream(service.stream_analysis_seed(created.sessionId, "guest_ai_search")))
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert any("run.completed" in item for item in events)
    assert snapshot.conversation["pendingAction"] is not None
    assert snapshot.conversation["pendingAction"]["actionType"] == "plan_confirmation"
    assert snapshot.analysisSeed is not None
    assert snapshot.analysisSeed["status"] == "completed"


def test_stream_analysis_seed_failure_notifies_terminal_failure(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    analysis_task = _create_completed_analysis_task(storage, owner_id="guest_ai_search", tmp_path=tmp_path)
    created = service.create_session_from_analysis_seed("guest_ai_search", analysis_task.id)

    async def _fake_stream_main_agent_execution(*_args, **_kwargs):
        yield 'data: {"type":"run.failed","payload":{"message":"seed boom"}}'

    notify_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(service.agent_runs, "_stream_main_agent_execution", _fake_stream_main_agent_execution)
    monkeypatch.setattr(
        service,
        "notify_task_terminal_status",
        lambda task_id, terminal_status, **kwargs: notify_calls.append(
            {"task_id": task_id, "terminal_status": terminal_status, **kwargs}
        ),
    )

    asyncio.run(_collect_stream(service.stream_analysis_seed(created.sessionId, "guest_ai_search")))
    task = storage.get_task(created.sessionId)

    assert task is not None
    assert task.status.value == "failed"
    assert notify_calls == [
        {
            "task_id": created.sessionId,
            "terminal_status": "failed",
            "error_message": "生成 AI 检索计划失败：seed boom",
        }
    ]


def test_stream_analysis_seed_cancelled_after_planner_draft_recovers_plan_confirmation(monkeypatch, tmp_path):
    service, storage = _mount_service(monkeypatch, tmp_path)
    analysis_task = _create_completed_analysis_task(
        storage,
        owner_id="guest_ai_search",
        tmp_path=tmp_path,
        analysis_payload=_build_analysis_payload(include_semantic=False),
        patent_payload=_build_patent_payload(),
    )
    created = service.create_session_from_analysis_seed("guest_ai_search", analysis_task.id)
    plan_record = _plan_record(created.sessionId, plan_version=1, title="基于分析结果的检索计划")

    class _CancelledPlannerAgent:
        def __init__(self, task_id: str) -> None:
            self.task_id = task_id
            self.checkpointer = object()

        async def astream(self, payload, config=None, **kwargs):
            assert payload["messages"][0]["role"] == "user"
            assert config["configurable"]["thread_id"] == f"ai-search-{self.task_id}"
            assert kwargs["stream_mode"] == ["updates", "messages", "custom"]
            assert kwargs["version"] == "v2"
            task = storage.get_task(self.task_id)
            storage.update_task(
                self.task_id,
                metadata=merge_ai_search_meta(
                    task,
                    planner_draft={
                        "draft_id": "draft-1",
                        "draft_version": 1,
                        "phase": PHASE_DRAFTING_PLAN,
                        "review_markdown": plan_record["review_markdown"],
                        "execution_spec": plan_record["execution_spec_json"],
                    },
                ),
            )
            yield (
                (),
                "updates",
                {
                    "agent": {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "task",
                                        "args": {"subagent_type": "planner"},
                                        "id": "call-planner-seed",
                                        "type": "tool_call",
                                    }
                                ],
                            )
                        ]
                    }
                },
            )
            raise asyncio.CancelledError()

        def get_state(self, config):
            raise AssertionError("cancelled run should not read final state")

    monkeypatch.setattr(
        ai_search_service_module,
        "build_main_agent",
        lambda _storage_arg, task_id_arg: _CancelledPlannerAgent(task_id_arg),
    )

    events = asyncio.run(_collect_stream(service.stream_analysis_seed(created.sessionId, "guest_ai_search")))
    parsed = _parse_data_events(events)

    task = storage.get_task(created.sessionId)
    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    plan = storage.get_ai_search_plan(created.sessionId, 1)
    ai_meta = (task.metadata or {}).get("ai_search") if task else {}

    assert task is not None
    assert plan is not None
    assert ai_meta is not None
    assert ai_meta.get("analysis_seed_status") == "completed"
    assert ai_meta.get("planner_draft") is None
    assert ai_meta.get("active_plan_version") == 1
    assert any(event["type"] == "process.started" for event in parsed)
    assert snapshot.conversation["pendingAction"] is not None
    assert snapshot.conversation["pendingAction"]["actionType"] == "plan_confirmation"
    assert snapshot.analysisSeed is not None
    assert snapshot.analysisSeed["status"] == "completed"
    assert [message["kind"] for message in snapshot.conversation["messages"]] == [
        "chat",
        "plan_confirmation",
    ]
