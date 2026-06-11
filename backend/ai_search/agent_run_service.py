"""Streaming service for the conversational AI search agent."""

from __future__ import annotations

import asyncio
import json
import contextlib
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from backend.storage import TaskStatus
from backend.time_utils import utc_now_z
from patent_agents.ai_search.src.state import (
    PHASE_IDLE,
    PHASE_RUNNING,
    get_ai_search_meta,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
)

from patent_agents.ai_search.src.runtime import (
    AiSearchRuntimeContext,
    DEFAULT_STOP_POLICY,
    documents_payload,
    run_search_agent_stream,
    set_task_phase,
)


class AiSearchAgentRunService:
    def __init__(self, facade: Any) -> None:
        self.facade = facade

    @property
    def storage(self):
        return self.facade.storage

    def _current_phase_value(self, task_id: str, default: str = PHASE_IDLE) -> str:
        task = self.storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        return str(meta.get("current_phase") or default).strip() or default

    def _format_event(self, event: Dict[str, Any]) -> str:
        message = {
            "type": str(event.get("event_type") or ""),
            "sessionId": str(event.get("session_id") or event.get("task_id") or ""),
            "taskId": str(event.get("task_id") or event.get("session_id") or ""),
            "runId": event.get("run_id"),
            "entityId": event.get("entity_id"),
            "phase": str((event.get("payload") or {}).get("phase") or ""),
            "seq": int(event.get("seq") or 0),
            "timestamp": event.get("created_at"),
            "payload": event.get("payload") or {},
        }
        if not message["phase"]:
            task = self.storage.get_task(message["taskId"])
            message["phase"] = str(get_ai_search_meta(task).get("current_phase") or PHASE_IDLE)
        return f"data: {json.dumps(message, ensure_ascii=False)}\n\n"

    def _append_event(
        self,
        task_id: str,
        event_type: str,
        payload: Dict[str, Any],
        *,
        run_id: str = "",
        entity_id: str = "",
    ) -> Dict[str, Any]:
        event = self.storage.append_ai_search_stream_event(
            {
                "event_id": uuid.uuid4().hex,
                "session_id": task_id,
                "task_id": task_id,
                "run_id": run_id or None,
                "event_type": event_type,
                "entity_id": entity_id or None,
                "payload": payload,
            }
        )
        return event or {
            "event_type": event_type,
            "session_id": task_id,
            "task_id": task_id,
            "run_id": run_id,
            "entity_id": entity_id,
            "payload": payload,
            "created_at": utc_now_z(),
            "seq": 0,
        }

    def _append_message(
        self,
        task_id: str,
        role: str,
        content: str,
        *,
        kind: str = "chat",
        stream_status: str = "completed",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        message_id = uuid.uuid4().hex
        created = self.storage.create_ai_search_message(
            {
                "message_id": message_id,
                "task_id": task_id,
                "role": role,
                "kind": kind,
                "content": content,
                "stream_status": stream_status,
                "metadata": metadata or {},
            }
        )
        return self.storage.get_ai_search_message(message_id) if created else None

    def _update_message_content(self, message_id: str, content: str, *, stream_status: str) -> Optional[Dict[str, Any]]:
        if not message_id:
            return None
        self.storage.update_ai_search_message(message_id, content=content, stream_status=stream_status)
        return self.storage.get_ai_search_message(message_id)

    def _ensure_run(self, task_id: str) -> Dict[str, Any]:
        task = self.storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 1)
        run = self.storage.get_ai_search_run(task_id, plan_version=plan_version)
        if run:
            self.storage.update_ai_search_run(
                task_id,
                str(run.get("run_id") or ""),
                phase=PHASE_RUNNING,
                status=TaskStatus.PROCESSING.value,
            )
            return self.storage.get_ai_search_run(task_id, str(run.get("run_id") or "")) or run
        run_id = uuid.uuid4().hex
        self.storage.create_ai_search_run(
            {
                "run_id": run_id,
                "task_id": task_id,
                "plan_version": plan_version,
                "phase": PHASE_RUNNING,
                "status": TaskStatus.PROCESSING.value,
                "selected_document_count": int(meta.get("selected_document_count") or 0),
            }
        )
        self.storage.update_task(
            task_id,
            metadata=merge_ai_search_meta(task, active_plan_version=plan_version, current_run_id=run_id),
        )
        return self.storage.get_ai_search_run(task_id, run_id) or {"run_id": run_id, "plan_version": plan_version}

    def _mark_running(self, task_id: str) -> None:
        task = self.storage.get_task(task_id)
        self.storage.update_task(
            task_id,
            metadata=merge_ai_search_meta(task, current_phase=PHASE_RUNNING),
            status=TaskStatus.PROCESSING.value,
            progress=phase_progress(PHASE_RUNNING),
            current_step=phase_step(PHASE_RUNNING),
        )

    def _mark_idle(self, task_id: str, run_id: str = "") -> None:
        task = self.storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 1)
        selected_count = len(self.storage.list_ai_search_documents(task_id, plan_version, stages=["selected"]))
        self.storage.update_task(
            task_id,
            metadata=merge_ai_search_meta(task, current_phase=PHASE_IDLE, selected_document_count=selected_count),
            status=TaskStatus.PROCESSING.value,
            progress=phase_progress(PHASE_IDLE),
            current_step=phase_step(PHASE_IDLE),
        )
        if run_id:
            self.storage.update_ai_search_run(
                task_id,
                run_id,
                phase=PHASE_IDLE,
                status=TaskStatus.PROCESSING.value,
                selected_document_count=selected_count,
                completed_at=utc_now_z(),
            )

    def _owned_task(self, session_id: str, owner_id: str) -> Any:
        return self.facade.sessions._get_owned_session_task(session_id, owner_id)

    def _run_cancel_requested(self, task_id: str, run_id: str) -> bool:
        meta = get_ai_search_meta(self.storage.get_task(task_id))
        return (
            bool(meta.get("cancel_requested"))
            and str(meta.get("cancel_requested_run_id") or "").strip() == str(run_id or "").strip()
        )

    def _clear_run_cancel_request(self, task_id: str) -> None:
        task = self.storage.get_task(task_id)
        self.storage.update_task(
            task_id,
            metadata=merge_ai_search_meta(task, cancel_requested=False, cancel_requested_run_id=""),
        )

    def _request_interrupt_current_run(self, task_id: str, run_id: str, *, reason: str = "") -> Dict[str, Any]:
        task = self.storage.get_task(task_id)
        if not run_id:
            return {"cancelled": False, "reason": "not_running"}
        self.storage.update_task(
            task_id,
            metadata=merge_ai_search_meta(
                task,
                cancel_requested=True,
                cancel_requested_run_id=run_id,
                cancel_reason=reason or "user_message",
            ),
        )
        event = self._append_event(
            task_id,
            "run.interrupt_requested",
            {
                "phase": PHASE_RUNNING,
                "message": "已收到新指令，当前检索轮次将尽快停止。",
                "reason": reason or "user_message",
            },
            run_id=run_id,
        )
        return {"cancelled": True, "event": event}

    def _run_deadline_seconds(self, runtime: AiSearchRuntimeContext) -> int:
        try:
            value = int((runtime.stop_policy() or {}).get("deadline_seconds") or DEFAULT_STOP_POLICY["deadline_seconds"])
        except Exception:
            value = int(DEFAULT_STOP_POLICY["deadline_seconds"])
        return max(30, value)

    async def _yield_events_after(self, session_id: str, after_seq: int) -> AsyncIterator[str]:
        for event in self.storage.list_ai_search_stream_events(session_id, after_seq=after_seq, limit=500):
            yield self._format_event(event)

    async def stream_message(self, session_id: str, owner_id: str, content: str) -> AsyncIterator[str]:
        task = self._owned_task(session_id, owner_id)
        text = str(content or "").strip()
        if not text:
            return
        if self._current_phase_value(task.id) == PHASE_RUNNING:
            user_message = self._append_message(
                task.id,
                "user",
                text,
                metadata={"message_variant": "mid_run_instruction"},
            )
            run = self.storage.get_ai_search_run(
                task.id,
                plan_version=int(get_ai_search_meta(task).get("active_plan_version") or 1),
            )
            run_id = str((run or {}).get("run_id") or get_ai_search_meta(task).get("current_run_id") or "").strip()
            start_seq = 0
            if user_message:
                msg_event = self._append_event(
                    task.id,
                    "message.created",
                    user_message,
                    run_id=run_id,
                    entity_id=str(user_message.get("message_id") or ""),
                )
                start_seq = int(msg_event.get("seq") or 0)
                yield self._format_event(msg_event)
            interrupted = self._request_interrupt_current_run(task.id, run_id, reason="user_message")
            event = interrupted.get("event")
            if isinstance(event, dict):
                yield self._format_event(event)
                start_seq = max(start_seq, int(event.get("seq") or 0))
            async for chunk in self._yield_events_after(task.id, start_seq):
                yield chunk
            return
        user_message = self._append_message(task.id, "user", text)
        run = self._ensure_run(task.id)
        run_id = str(run.get("run_id") or "")
        self._mark_running(task.id)
        started = self._append_event(
            task.id,
            "run.started",
            {
                "phase": PHASE_RUNNING,
                "run": {
                    "runId": run_id,
                    "phase": PHASE_RUNNING,
                    "status": TaskStatus.PROCESSING.value,
                    "planVersion": int(run.get("plan_version") or 1),
                },
            },
            run_id=run_id,
        )
        yield self._format_event(started)
        start_seq = int(started.get("seq") or 0)
        if user_message:
            msg_event = self._append_event(task.id, "message.created", user_message, run_id=run_id, entity_id=str(user_message.get("message_id") or ""))
            yield self._format_event(msg_event)
            start_seq = int(msg_event.get("seq") or start_seq)
        try:
            runtime = AiSearchRuntimeContext(self.storage, task.id, run_id, int(run.get("plan_version") or 1))
            delta_queue: asyncio.Queue[str] = asyncio.Queue()
            assistant_message_id = ""
            assistant_parts: List[str] = []
            latest_seq = start_seq

            async def on_delta(delta: str) -> None:
                if delta:
                    await delta_queue.put(delta)

            def flush_runtime_events() -> List[str]:
                nonlocal latest_seq
                chunks: List[str] = []
                for event in self.storage.list_ai_search_stream_events(task.id, after_seq=latest_seq, limit=500):
                    seq = int(event.get("seq") or 0)
                    if seq <= latest_seq:
                        continue
                    chunks.append(self._format_event(event))
                    latest_seq = seq
                return chunks

            def ensure_assistant_message() -> List[str]:
                nonlocal assistant_message_id, latest_seq
                if assistant_message_id:
                    return []
                message = self._append_message(
                    task.id,
                    "assistant",
                    "",
                    stream_status="streaming",
                    metadata={"render_mode": "markdown"},
                )
                if not message:
                    return []
                assistant_message_id = str(message.get("message_id") or "")
                event = self._append_event(
                    task.id,
                    "message.created",
                    message,
                    run_id=run_id,
                    entity_id=assistant_message_id,
                )
                latest_seq = max(latest_seq, int(event.get("seq") or 0))
                return [self._format_event(event)]

            def drain_delta_text() -> str:
                parts: List[str] = []
                while not delta_queue.empty():
                    try:
                        parts.append(delta_queue.get_nowait())
                        delta_queue.task_done()
                    except asyncio.QueueEmpty:
                        break
                return "".join(parts)

            def append_delta_event(delta_text: str) -> List[str]:
                nonlocal latest_seq
                if not delta_text:
                    return []
                chunks = ensure_assistant_message()
                assistant_parts.append(delta_text)
                current_content = "".join(assistant_parts)
                message = self._update_message_content(assistant_message_id, current_content, stream_status="streaming")
                payload = {
                    **(message or {}),
                    "message_id": assistant_message_id,
                    "role": "assistant",
                    "kind": "chat",
                    "delta": delta_text,
                    "content": current_content,
                    "stream_status": "streaming",
                    "metadata": {"render_mode": "markdown"},
                }
                event = self._append_event(
                    task.id,
                    "message.delta",
                    payload,
                    run_id=run_id,
                    entity_id=assistant_message_id,
                )
                latest_seq = max(latest_seq, int(event.get("seq") or 0))
                chunks.append(self._format_event(event))
                return chunks

            agent_task = asyncio.create_task(
                run_search_agent_stream(
                    runtime,
                    text,
                    on_delta=on_delta,
                    should_cancel=lambda: self._run_cancel_requested(task.id, run_id),
                )
            )
            deadline_seconds = self._run_deadline_seconds(runtime)
            started_monotonic = time.monotonic()
            timed_out = False
            while not agent_task.done():
                for chunk in flush_runtime_events():
                    yield chunk
                delta_text = drain_delta_text()
                if delta_text:
                    for chunk in flush_runtime_events():
                        yield chunk
                    for chunk in append_delta_event(delta_text):
                        yield chunk
                if time.monotonic() - started_monotonic >= deadline_seconds:
                    timed_out = True
                    agent_task.cancel()
                    break
                await asyncio.sleep(0.1)

            if timed_out:
                with contextlib.suppress(asyncio.CancelledError):
                    await agent_task
                assistant_text = ""
            else:
                assistant_text = await agent_task
            for chunk in flush_runtime_events():
                yield chunk
            delta_text = drain_delta_text()
            if delta_text:
                for chunk in flush_runtime_events():
                    yield chunk
                for chunk in append_delta_event(delta_text):
                    yield chunk
            if self._run_cancel_requested(task.id, run_id):
                run_state = self.storage.get_ai_search_run(task.id, run_id) or {}
                self._clear_run_cancel_request(task.id)
                if str(run_state.get("status") or "").strip() == TaskStatus.CANCELLED.value:
                    return
                self._mark_idle(task.id, run_id)
                cancelled = self._append_event(
                    task.id,
                    "run.cancelled",
                    {
                        "phase": PHASE_IDLE,
                        "completionReason": "interrupted",
                        "awaitingUserAction": False,
                        "message": "已根据新指令停止当前检索轮次。",
                    },
                    run_id=run_id,
                )
                yield self._format_event(cancelled)
                return
            if timed_out:
                self._clear_run_cancel_request(task.id)
                if assistant_message_id:
                    assistant_message = self._update_message_content(
                        assistant_message_id,
                        "".join(assistant_parts),
                        stream_status="completed",
                    )
                    if assistant_message:
                        event = self._append_event(
                            task.id,
                            "message.completed",
                            assistant_message,
                            run_id=run_id,
                            entity_id=assistant_message_id,
                        )
                        latest_seq = max(latest_seq, int(event.get("seq") or 0))
                        yield self._format_event(event)
                docs_event = self._append_event(task.id, "documents.updated", documents_payload(runtime), run_id=run_id)
                yield self._format_event(docs_event)
                self._mark_idle(task.id, run_id)
                completed = self._append_event(
                    task.id,
                    "run.completed",
                    {
                        "phase": PHASE_IDLE,
                        "completionReason": "deadline_seconds",
                        "awaitingUserAction": False,
                        "message": "本轮达到时间上限，已停止检索。",
                    },
                    run_id=run_id,
                )
                yield self._format_event(completed)
                return
            if assistant_text:
                if assistant_message_id:
                    assistant_message = self._update_message_content(
                        assistant_message_id,
                        assistant_text,
                        stream_status="completed",
                    )
                    if assistant_message:
                        event = self._append_event(
                            task.id,
                            "message.completed",
                            assistant_message,
                            run_id=run_id,
                            entity_id=assistant_message_id,
                        )
                        latest_seq = max(latest_seq, int(event.get("seq") or 0))
                        yield self._format_event(event)
                else:
                    assistant_message = self._append_message(
                        task.id,
                        "assistant",
                        assistant_text,
                        metadata={"render_mode": "markdown"},
                    )
                    if assistant_message:
                        event = self._append_event(
                            task.id,
                            "message.created",
                            assistant_message,
                            run_id=run_id,
                            entity_id=str(assistant_message.get("message_id") or ""),
                        )
                        latest_seq = max(latest_seq, int(event.get("seq") or 0))
                        yield self._format_event(event)
            elif assistant_message_id:
                assistant_message = self._update_message_content(
                    assistant_message_id,
                    "".join(assistant_parts),
                    stream_status="completed",
                )
                if assistant_message:
                    event = self._append_event(
                        task.id,
                        "message.completed",
                        assistant_message,
                        run_id=run_id,
                        entity_id=assistant_message_id,
                    )
                    yield self._format_event(event)
            docs_event = self._append_event(task.id, "documents.updated", documents_payload(runtime), run_id=run_id)
            yield self._format_event(docs_event)
            self._mark_idle(task.id, run_id)
            completed = self._append_event(
                task.id,
                "run.completed",
                {
                    "phase": PHASE_IDLE,
                    "completionReason": "completed",
                    "awaitingUserAction": False,
                },
                run_id=run_id,
            )
            yield self._format_event(completed)
        except Exception as exc:
            set_task_phase(self.storage, task.id, "failed", error_message=str(exc))
            self.storage.update_ai_search_run(
                task.id,
                run_id,
                phase="failed",
                status=TaskStatus.FAILED.value,
                completed_at=utc_now_z(),
            )
            failed = self._append_event(
                task.id,
                "run.failed",
                {"phase": "failed", "message": str(exc)},
                run_id=run_id,
            )
            yield self._format_event(failed)

    async def subscribe_stream(self, session_id: str, owner_id: str, *, after_seq: int = 0) -> AsyncIterator[str]:
        self._owned_task(session_id, owner_id)
        latest_seq = max(int(after_seq or 0), 0)
        idle_ticks = 0
        while True:
            events = self.storage.list_ai_search_stream_events(session_id, after_seq=latest_seq, limit=500)
            if events:
                idle_ticks = 0
                for event in events:
                    latest_seq = max(latest_seq, int(event.get("seq") or 0))
                    yield self._format_event(event)
                continue
            if self._current_phase_value(session_id) != PHASE_RUNNING:
                break
            idle_ticks += 1
            if idle_ticks % 10 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    async def stream_analysis_seed(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self._owned_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        seed_prompt = str(meta.get("analysis_seed_prompt") or "").strip()
        if not seed_prompt:
            return
        self.storage.update_task(
            task.id,
            metadata=merge_ai_search_meta(task, analysis_seed_status="running"),
        )
        async for event in self.stream_message(session_id, owner_id, seed_prompt):
            yield event
        self.storage.update_task(
            task.id,
            metadata=merge_ai_search_meta(self.storage.get_task(task.id), analysis_seed_status="completed"),
        )

    def cancel_current_run(self, session_id: str, owner_id: str) -> Dict[str, Any]:
        task = self._owned_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 1)
        run = self.storage.get_ai_search_run(task.id, plan_version=plan_version)
        run_id = str((run or {}).get("run_id") or meta.get("current_run_id") or "").strip()
        if str(meta.get("current_phase") or "").strip() != PHASE_RUNNING or not run_id:
            return {"cancelled": False, "reason": "not_running"}
        self.storage.update_task(
            task.id,
            metadata=merge_ai_search_meta(
                task,
                current_phase=PHASE_IDLE,
                cancel_requested=True,
                cancel_requested_run_id=run_id,
            ),
            status=TaskStatus.PROCESSING.value,
            progress=phase_progress(PHASE_IDLE),
            current_step=phase_step(PHASE_IDLE),
        )
        self.storage.update_ai_search_run(
            task.id,
            run_id,
            phase=PHASE_IDLE,
            status=TaskStatus.CANCELLED.value,
            completed_at=utc_now_z(),
        )
        event = self._append_event(
            task.id,
            "run.cancelled",
            {
                "phase": PHASE_IDLE,
                "completionReason": "cancelled",
                "message": "本轮检索已取消。",
            },
            run_id=run_id,
        )
        return {"cancelled": True, "event": event}

    async def stream_document_selection(
        self,
        session_id: str,
        owner_id: str,
        plan_version: int,
        review_document_ids: Optional[List[str]],
        remove_document_ids: Optional[List[str]],
    ) -> AsyncIterator[str]:
        task = self._owned_task(session_id, owner_id)
        version = int(plan_version or 1)
        for document_id in review_document_ids or []:
            self.storage.update_ai_search_document(task.id, version, document_id, stage="selected", user_pinned=True)
        for document_id in remove_document_ids or []:
            self.storage.update_ai_search_document(task.id, version, document_id, stage="candidate", user_pinned=False, user_removed=True)
        run = self.storage.get_ai_search_run(task.id, plan_version=version)
        runtime = AiSearchRuntimeContext(self.storage, task.id, str((run or {}).get("run_id") or ""), version)
        selected_count = len(runtime.selected_documents())
        if run:
            self.storage.update_ai_search_run(task.id, str(run.get("run_id") or ""), selected_document_count=selected_count)
        self.storage.update_task(
            task.id,
            metadata=merge_ai_search_meta(task, selected_document_count=selected_count),
        )
        event = self._append_event(task.id, "documents.updated", documents_payload(runtime), run_id=runtime.run_id)
        yield self._format_event(event)
