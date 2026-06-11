from __future__ import annotations

import asyncio
import json
import zipfile
from typing import Any, AsyncIterator

from agents import RunContextWrapper

from backend.ai_search import agent_run_service as agent_run_service_module
from patent_agents.ai_search.src.runtime import AiSearchRuntimeContext, search_patents
from backend.ai_search.service import AiSearchService
from backend.storage import PipelineTaskManager, SQLiteTaskStorage, TaskStatus
from patent_agents.ai_search.src.ids import stable_ai_search_document_id
from patent_agents.ai_search.src.state import PHASE_IDLE, PHASE_RUNNING, get_ai_search_meta, merge_ai_search_meta


async def _collect_stream(stream: AsyncIterator[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for chunk in stream:
        text = str(chunk or "").strip()
        if not text.startswith("data: "):
            continue
        events.append(json.loads(text.removeprefix("data: ")))
    return events


def _build_service(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_agent_service.db")
    service = AiSearchService()
    service.task_manager = PipelineTaskManager(storage)
    service._enforce_daily_quota = lambda *_args, **_kwargs: None
    return service, storage


def _create_session(service: AiSearchService, owner_id: str = "guest_ai_search"):
    created = service.create_session(owner_id)
    task = service.storage.get_task(created.sessionId)
    assert task is not None
    return created, task


def _document(task_id: str, run_id: str, canonical_id: str, *, stage: str = "candidate", title: str = "候选文献") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "task_id": task_id,
        "plan_version": 1,
        "document_id": stable_ai_search_document_id(task_id, 1, canonical_id),
        "source_type": "patent",
        "canonical_id": canonical_id,
        "external_id": canonical_id,
        "pn": canonical_id,
        "title": title,
        "abstract": "摘要",
        "stage": stage,
        "score": 0.91,
        "agent_reason": "命中核心特征",
    }


def test_stream_message_persists_agent_reply_and_documents(monkeypatch, tmp_path) -> None:
    service, storage = _build_service(tmp_path)
    created, _task = _create_session(service)

    async def fake_run_search_agent_stream(runtime, text: str, *, on_delta=None, should_cancel=None) -> str:
        assert text == "检索固态电池隔膜"
        storage.upsert_ai_search_documents([_document(runtime.task_id, runtime.run_id, "CN100001A")])
        trace = runtime.start_trace(
            tool_name="search_patents",
            label="检索智慧芽：固态电池隔膜",
            input={"query": "固态电池隔膜", "limit": 1},
        )
        runtime.finish_trace(
            trace,
            tool_name="search_patents",
            label="检索完成",
            output={"new_count": 1, "candidate_total": 1},
        )
        if on_delta:
            await on_delta("已找到 ")
            await on_delta("1 篇候选。")
        return "已找到 1 篇候选。"

    monkeypatch.setattr(agent_run_service_module, "run_search_agent_stream", fake_run_search_agent_stream)

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "检索固态电池隔膜")))
    event_types = [event["type"] for event in events]

    assert event_types.count("message.created") == 2
    assert event_types[0] == "run.started"
    assert "message.delta" in event_types
    assert "message.completed" in event_types
    assert "trace.completed" in event_types
    assert "documents.updated" in event_types
    assert event_types[-1] == "run.completed"
    assert "".join(event["payload"].get("delta", "") for event in events if event["type"] == "message.delta") == "已找到 1 篇候选。"
    trace_started = next(event for event in events if event["type"] == "trace.started" and event["payload"].get("toolName") == "search_patents")
    trace_completed = next(event for event in events if event["type"] == "trace.completed" and event["payload"].get("toolName") == "search_patents")
    assert trace_started["payload"]["input"]["query"] == "固态电池隔膜"
    assert trace_completed["payload"]["output"]["new_count"] == 1

    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    assert snapshot.session.phase == PHASE_IDLE
    assert snapshot.run["status"] == TaskStatus.PROCESSING.value
    assert snapshot.run["phase"] == PHASE_IDLE
    assert snapshot.retrieval["documents"]["candidates"][0]["pn"] == "CN100001A"
    assert any(item["role"] == "assistant" and "已找到" in item["content"] for item in snapshot.conversation["messages"])
    activity_traces = snapshot.stream["activityTraces"]
    assert activity_traces[0]["toolName"] == "search_patents"
    assert activity_traces[0]["arguments"]["query"] == "固态电池隔膜"
    assert activity_traces[0]["output"]["new_count"] == 1
    assert activity_traces[0]["status"] == "completed"


def test_cancel_current_run_stops_stream_from_writing_completion(monkeypatch, tmp_path) -> None:
    service, storage = _build_service(tmp_path)
    created, _task = _create_session(service)

    async def fake_run_search_agent_stream(runtime, _text: str, *, on_delta=None, should_cancel=None) -> str:
        service.agent_runs.cancel_current_run(runtime.task_id, "guest_ai_search")
        assert should_cancel is None or should_cancel()
        return "这段不应写入会话"

    monkeypatch.setattr(agent_run_service_module, "run_search_agent_stream", fake_run_search_agent_stream)

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "开始检索后取消")))
    event_types = [event["type"] for event in events]

    assert "run.cancelled" in event_types
    assert "run.completed" not in event_types
    assert all("这段不应写入会话" not in str(item.get("content") or "") for item in storage.list_ai_search_messages(created.sessionId))

    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    assert snapshot.session.phase == PHASE_IDLE
    assert snapshot.run["phase"] == PHASE_IDLE
    assert snapshot.run["status"] == TaskStatus.CANCELLED.value
    assert get_ai_search_meta(storage.get_task(created.sessionId)).get("cancel_requested") is False


def test_document_selection_updates_selected_and_candidate_sets(tmp_path) -> None:
    service, storage = _build_service(tmp_path)
    created, _task = _create_session(service)
    run = service.agent_runs._ensure_run(created.sessionId)
    run_id = str(run["run_id"])
    doc_review = _document(created.sessionId, run_id, "CN200001A", title="待选文献")
    doc_remove = _document(created.sessionId, run_id, "CN200002A", stage="selected", title="移回候选文献")
    storage.upsert_ai_search_documents([doc_review, doc_remove])

    asyncio.run(
        _collect_stream(
            service.stream_document_selection(
                created.sessionId,
                "guest_ai_search",
                1,
                [doc_review["document_id"]],
                [doc_remove["document_id"]],
            )
        )
    )

    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")
    selected_ids = {item["document_id"] for item in snapshot.retrieval["documents"]["selected"]}
    candidate_ids = {item["document_id"] for item in snapshot.retrieval["documents"]["candidates"]}

    assert doc_review["document_id"] in selected_ids
    assert doc_remove["document_id"] in candidate_ids
    assert snapshot.run["selectedDocumentCount"] == 1


def test_running_message_is_saved_and_interrupts_current_run(tmp_path) -> None:
    service, storage = _build_service(tmp_path)
    created, _task = _create_session(service)
    run = service.agent_runs._ensure_run(created.sessionId)
    run_id = str(run["run_id"])
    service.agent_runs._mark_running(created.sessionId)

    events = asyncio.run(_collect_stream(service.stream_message(created.sessionId, "guest_ai_search", "改查时间常数补偿")))
    event_types = [event["type"] for event in events]

    assert event_types[:2] == ["message.created", "run.interrupt_requested"]
    messages = storage.list_ai_search_messages(created.sessionId)
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "改查时间常数补偿"
    assert messages[-1]["metadata"]["message_variant"] == "mid_run_instruction"
    meta = get_ai_search_meta(storage.get_task(created.sessionId))
    assert meta["cancel_requested"] is True
    assert meta["cancel_requested_run_id"] == run_id


def test_subscribe_stream_waits_for_new_events_while_session_is_running(tmp_path) -> None:
    service, _storage = _build_service(tmp_path)
    created, _task = _create_session(service)
    run = service.agent_runs._ensure_run(created.sessionId)
    run_id = str(run["run_id"])
    service.agent_runs._mark_running(created.sessionId)

    async def collect_until_trace() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for chunk in service.subscribe_stream(created.sessionId, "guest_ai_search", after_seq=0):
            text = str(chunk or "").strip()
            if not text.startswith("data: "):
                continue
            event = json.loads(text.removeprefix("data: "))
            events.append(event)
            if event["type"] == "trace.completed":
                break
        return events

    async def run_scenario() -> list[dict[str, Any]]:
        pending = asyncio.create_task(collect_until_trace())
        await asyncio.sleep(0.15)
        service.agent_runs._append_event(
            created.sessionId,
            "trace.completed",
            {
                "traceId": "trace-refresh-progress",
                "traceType": "tool",
                "status": "completed",
                "label": "刷新后继续收到工具进展",
                "toolName": "search_patents",
                "phase": PHASE_RUNNING,
            },
            run_id=run_id,
            entity_id="trace-refresh-progress",
        )
        service.agent_runs._mark_idle(created.sessionId, run_id)
        return await asyncio.wait_for(pending, timeout=2)

    events = asyncio.run(run_scenario())

    assert any(event["type"] == "trace.completed" for event in events)


def test_search_patents_respects_remaining_candidate_capacity(monkeypatch, tmp_path) -> None:
    service, storage = _build_service(tmp_path)
    created, _task = _create_session(service)
    service.update_stop_policy(created.sessionId, "guest_ai_search", {"max_candidates": 2})
    run = service.agent_runs._ensure_run(created.sessionId)
    runtime = AiSearchRuntimeContext(storage, created.sessionId, str(run["run_id"]), 1)

    class FakeClient:
        def search(self, _query: str, limit: int = 20):
            assert limit == 2
            return {"results": [{"pn": f"CN{i}A", "title": f"文献 {i}"} for i in range(10)]}

    monkeypatch.setattr("patent_agents.ai_search.src.runtime.SearchClientFactory.get_client", lambda _name: FakeClient())

    result = asyncio.run(
        search_patents.on_invoke_tool(
            RunContextWrapper(runtime),
            json.dumps({"query": "固态电池隔膜", "limit": 20, "mode": "boolean", "to_date": ""}),
        )
    )
    parsed = json.loads(result) if isinstance(result, str) else result

    assert parsed["count"] == 2
    assert len(storage.list_ai_search_documents(created.sessionId, 1)) == 2
    assert "zhihuiya" in get_ai_search_meta(storage.get_task(created.sessionId))["stop_policy"]["databases"]


def test_search_patents_filters_target_patent(monkeypatch, tmp_path) -> None:
    service, storage = _build_service(tmp_path)
    created, task = _create_session(service)
    storage.update_task(
        created.sessionId,
        metadata=merge_ai_search_meta(task, source_pn="CN117545995A", source_title="CN117545995A"),
    )
    run = service.agent_runs._ensure_run(created.sessionId)
    runtime = AiSearchRuntimeContext(storage, created.sessionId, str(run["run_id"]), 1)

    class FakeClient:
        def search(self, _query: str, limit: int = 20):
            return {
                "results": [
                    {"pn": "CN117545995A", "title": "目标专利"},
                    {"pn": "US1234567B2", "title": "可用对比文件"},
                ]
            }

    monkeypatch.setattr("patent_agents.ai_search.src.runtime.SearchClientFactory.get_client", lambda _name: FakeClient())

    result = asyncio.run(
        search_patents.on_invoke_tool(
            RunContextWrapper(runtime),
            json.dumps({"query": "真空检漏 时间常数", "limit": 20, "mode": "boolean", "to_date": ""}),
        )
    )
    parsed = json.loads(result) if isinstance(result, str) else result
    docs = storage.list_ai_search_documents(created.sessionId, 1)

    assert parsed["skipped_target"] == 1
    assert [item["pn"] for item in docs] == ["US1234567B2"]


def test_snapshot_uses_idle_task_phase_when_run_was_left_running(tmp_path) -> None:
    service, storage = _build_service(tmp_path)
    created, task = _create_session(service)
    run = service.agent_runs._ensure_run(created.sessionId)
    storage.update_task(
        created.sessionId,
        metadata=merge_ai_search_meta(task, current_phase=PHASE_IDLE),
        status=TaskStatus.PROCESSING.value,
    )
    storage.update_ai_search_run(created.sessionId, str(run["run_id"]), phase=PHASE_RUNNING)

    snapshot = service.get_snapshot(created.sessionId, "guest_ai_search")

    assert snapshot.session.phase == PHASE_IDLE
    assert snapshot.run["phase"] == PHASE_IDLE


def test_export_report_writes_markdown_json_csv_and_bundle(tmp_path) -> None:
    service, storage = _build_service(tmp_path)
    created, _task = _create_session(service)
    run = service.agent_runs._ensure_run(created.sessionId)
    run_id = str(run["run_id"])
    storage.upsert_ai_search_documents(
        [
            _document(created.sessionId, run_id, "CN300001A", stage="selected", title="核心对比文件"),
            _document(created.sessionId, run_id, "CN300002A", title="候选文件"),
        ]
    )
    storage.create_ai_search_message(
        {
            "message_id": "report-msg-1",
            "task_id": created.sessionId,
            "role": "assistant",
            "kind": "chat",
            "content": "阶段性报告内容",
            "stream_status": "completed",
            "metadata": {"message_variant": "search_report"},
        }
    )

    snapshot = service.export_report(created.sessionId, "guest_ai_search")
    attachment_ids = {item.attachmentId for item in snapshot.artifacts.attachments}

    assert attachment_ids == {"result_bundle", "report_markdown", "selected_documents_csv", "report_json"}

    output_files = storage.get_task(created.sessionId).metadata["output_files"]
    assert "阶段性报告内容" in open(output_files["ai_search_report_markdown"], encoding="utf-8").read()
    assert "核心对比文件" in open(output_files["ai_search_selected_documents_csv"], encoding="utf-8-sig").read()
    with zipfile.ZipFile(output_files["ai_search_bundle"]) as archive:
        assert set(archive.namelist()) == {"ai_search_report.md", "ai_search_report.json", "selected_documents.csv"}
