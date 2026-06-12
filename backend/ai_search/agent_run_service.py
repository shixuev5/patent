"""Streaming service for the conversational AI search agent."""

from __future__ import annotations

import asyncio
import json
import contextlib
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from backend.storage import TaskStatus
from backend.time_utils import parse_storage_ts, utc_now, utc_now_z
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

STOP_SATISFIED_COMPLETION_GRACE_SECONDS = 12.0
STOP_SATISFIED_SUBSCRIBE_STALE_SECONDS = 30.0
SNAPSHOT_STALE_RUNNING_REPAIR_GRACE_SECONDS = 60.0
STOP_SATISFIED_MESSAGE = "停止条件已满足，本轮已停止继续检索。可查看当前候选/已选证据，或发送新指令调整范围。"
STALE_RUNNING_REPAIR_MESSAGE = "会话恢复时发现上一轮长时间无进展，已自动结束本轮。可查看当前候选/已选证据，或发送新指令继续检索。"


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

    def _truthy_value(self, value: Any) -> bool:
        return value is True or str(value or "").strip().lower() == "true"

    def _payload_indicates_stop_satisfied(self, payload: Dict[str, Any]) -> bool:
        label = str(payload.get("label") or "").strip()
        if "停止条件已满足" in label:
            return True
        for key in ("output", "result"):
            value = payload.get(key)
            if not isinstance(value, dict):
                continue
            if self._truthy_value(value.get("blocked")):
                return True
        return False

    def _payload_indicates_stop_condition_met(self, payload: Dict[str, Any]) -> bool:
        for key in ("output", "result"):
            value = payload.get(key)
            if not isinstance(value, dict):
                continue
            stop = value.get("stop")
            if isinstance(stop, dict) and self._truthy_value(stop.get("should_stop")):
                return True
        return False

    def _event_indicates_report_saved(self, event: Dict[str, Any]) -> bool:
        if str(event.get("event_type") or "").strip() != "trace.completed":
            return False
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if str(payload.get("toolName") or "").strip() != "save_search_report":
            return False
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        return self._truthy_value(output.get("saved")) or "检索报告已保存" in str(payload.get("label") or "")

    def _event_indicates_stop_satisfied(self, event: Dict[str, Any]) -> bool:
        if str(event.get("event_type") or "").strip() != "trace.completed":
            return False
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        return self._payload_indicates_stop_satisfied(payload)

    def _event_indicates_stop_condition_met(self, event: Dict[str, Any]) -> bool:
        if str(event.get("event_type") or "").strip() != "trace.completed":
            return False
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        return self._payload_indicates_stop_condition_met(payload)

    def _latest_stop_satisfied_event(self, task_id: str) -> Optional[Dict[str, Any]]:
        latest: Optional[Dict[str, Any]] = None
        stop_condition_event: Optional[Dict[str, Any]] = None
        report_saved_event: Optional[Dict[str, Any]] = None
        for event in self.storage.list_ai_search_stream_events(task_id, after_seq=0):
            if self._event_indicates_stop_satisfied(event):
                latest = event
            if self._event_indicates_stop_condition_met(event):
                stop_condition_event = event
                if report_saved_event:
                    latest = event
            if self._event_indicates_report_saved(event):
                report_saved_event = event
                if stop_condition_event:
                    latest = event
        return latest

    def _event_age_seconds(self, event: Dict[str, Any]) -> float:
        created_at = parse_storage_ts(str(event.get("created_at") or ""), naive_strategy="utc")
        if created_at is None:
            return 0.0
        return max(0.0, (utc_now() - created_at).total_seconds())

    def _timestamp_age_seconds(self, value: Any) -> float:
        created_at = parse_storage_ts(str(value or ""), naive_strategy="utc")
        if created_at is None:
            return 0.0
        return max(0.0, (utc_now() - created_at).total_seconds())

    def _active_run_id(self, task_id: str) -> str:
        task = self.storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 1)
        run = self.storage.get_ai_search_run(task_id, plan_version=plan_version) or self.storage.get_ai_search_run(task_id)
        return str((run or {}).get("run_id") or meta.get("current_run_id") or "").strip()

    def _latest_terminal_run_event(self, task_id: str, run_id: str = "") -> Optional[Dict[str, Any]]:
        latest: Optional[Dict[str, Any]] = None
        terminal_types = {"run.completed", "run.cancelled", "run.failed"}
        for event in self.storage.list_ai_search_stream_events(task_id, after_seq=0):
            if str(event.get("event_type") or "").strip() not in terminal_types:
                continue
            if run_id and str(event.get("run_id") or "").strip() != run_id:
                continue
            latest = event
        return latest

    def _mark_task_idle_preserving_run(self, task_id: str) -> None:
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

    def _mark_task_failed_preserving_run(self, task_id: str, message: str = "") -> None:
        set_task_phase(self.storage, task_id, "failed", error_message=message)

    def _finish_stop_satisfied_run(
        self,
        task_id: str,
        run_id: str,
        *,
        runtime: Optional[AiSearchRuntimeContext] = None,
        assistant_message_id: str = "",
        assistant_parts: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        if not run_id or self._current_phase_value(task_id) != PHASE_RUNNING:
            return []
        events: List[Dict[str, Any]] = []
        content = "".join(assistant_parts or []).strip() or STOP_SATISFIED_MESSAGE
        if assistant_message_id:
            assistant_message = self._update_message_content(
                assistant_message_id,
                content,
                stream_status="completed",
            )
            if assistant_message:
                events.append(
                    self._append_event(
                        task_id,
                        "message.completed",
                        assistant_message,
                        run_id=run_id,
                        entity_id=assistant_message_id,
                    )
                )
        else:
            assistant_message = self._append_message(
                task_id,
                "assistant",
                content,
                metadata={"render_mode": "markdown"},
            )
            if assistant_message:
                events.append(
                    self._append_event(
                        task_id,
                        "message.created",
                        assistant_message,
                        run_id=run_id,
                        entity_id=str(assistant_message.get("message_id") or ""),
                    )
                )
        task = self.storage.get_task(task_id)
        plan_version = int(get_ai_search_meta(task).get("active_plan_version") or 1)
        resolved_runtime = runtime or AiSearchRuntimeContext(self.storage, task_id, run_id, plan_version)
        events.append(self._append_event(task_id, "documents.updated", documents_payload(resolved_runtime), run_id=run_id))
        self._mark_idle(task_id, run_id)
        events.append(
            self._append_event(
                task_id,
                "run.completed",
                {
                    "phase": PHASE_IDLE,
                    "completionReason": "stop_satisfied",
                    "awaitingUserAction": False,
                    "message": STOP_SATISFIED_MESSAGE,
                },
                run_id=run_id,
            )
        )
        return events

    def _finish_stale_running_run(self, task_id: str, run_id: str) -> List[Dict[str, Any]]:
        if not run_id or self._current_phase_value(task_id) != PHASE_RUNNING:
            return []
        events: List[Dict[str, Any]] = []
        assistant_message = self._append_message(
            task_id,
            "assistant",
            STALE_RUNNING_REPAIR_MESSAGE,
            metadata={"render_mode": "markdown", "message_variant": "run_repair_notice"},
        )
        if assistant_message:
            events.append(
                self._append_event(
                    task_id,
                    "message.created",
                    assistant_message,
                    run_id=run_id,
                    entity_id=str(assistant_message.get("message_id") or ""),
                )
            )
        task = self.storage.get_task(task_id)
        plan_version = int(get_ai_search_meta(task).get("active_plan_version") or 1)
        runtime = AiSearchRuntimeContext(self.storage, task_id, run_id, plan_version)
        events.append(self._append_event(task_id, "documents.updated", documents_payload(runtime), run_id=run_id))
        self._mark_idle(task_id, run_id)
        events.append(
            self._append_event(
                task_id,
                "run.completed",
                {
                    "phase": PHASE_IDLE,
                    "completionReason": "stale_running_repaired",
                    "awaitingUserAction": False,
                    "message": STALE_RUNNING_REPAIR_MESSAGE,
                },
                run_id=run_id,
            )
        )
        return events

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
        run = self.storage.get_ai_search_run(task_id, run_id) if run_id else None
        if str((run or {}).get("status") or "").strip() == TaskStatus.CANCELLED.value:
            return True
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

    def _cancel_running_run(
        self,
        task_id: str,
        run_id: str,
        *,
        completion_reason: str,
        message: str,
    ) -> Optional[Dict[str, Any]]:
        if not run_id:
            return None
        self._mark_idle(task_id, run_id)
        self.storage.update_ai_search_run(
            task_id,
            run_id,
            phase=PHASE_IDLE,
            status=TaskStatus.CANCELLED.value,
            completed_at=utc_now_z(),
        )
        task = self.storage.get_task(task_id)
        self.storage.update_task(
            task_id,
            metadata=merge_ai_search_meta(
                task,
                cancel_requested=False,
                cancel_requested_run_id="",
                cancel_reason=completion_reason,
            ),
        )
        return self._append_event(
            task_id,
            "run.cancelled",
            {
                "phase": PHASE_IDLE,
                "completionReason": completion_reason,
                "message": message,
            },
            run_id=run_id,
        )

    def _run_deadline_seconds(self, runtime: AiSearchRuntimeContext) -> int:
        try:
            value = int((runtime.stop_policy() or {}).get("deadline_seconds") or DEFAULT_STOP_POLICY["deadline_seconds"])
        except Exception:
            value = int(DEFAULT_STOP_POLICY["deadline_seconds"])
        return max(30, value)

    def repair_stale_running_state(self, session_id: str, owner_id: str) -> Dict[str, Any]:
        task = self._owned_task(session_id, owner_id)
        if self._current_phase_value(task.id) != PHASE_RUNNING:
            return {"repaired": False, "reason": "not_running"}

        run_id = self._active_run_id(task.id)
        run = self.storage.get_ai_search_run(task.id, run_id) if run_id else None
        terminal_event = self._latest_terminal_run_event(task.id, run_id)
        if terminal_event:
            event_type = str(terminal_event.get("event_type") or "").strip()
            payload = terminal_event.get("payload") if isinstance(terminal_event.get("payload"), dict) else {}
            if event_type == "run.failed":
                self._mark_task_failed_preserving_run(task.id, str(payload.get("message") or "当前流式轮次执行失败。"))
                return {"repaired": True, "reason": "terminal_failed_event"}
            self._mark_task_idle_preserving_run(task.id)
            return {"repaired": True, "reason": f"terminal_{event_type}"}

        if run:
            run_phase = str(run.get("phase") or "").strip()
            run_status = str(run.get("status") or "").strip()
            if run_phase and run_phase != PHASE_RUNNING:
                if run_phase == "failed" or run_status == TaskStatus.FAILED.value:
                    self._mark_task_failed_preserving_run(task.id, "当前流式轮次执行失败。")
                    return {"repaired": True, "reason": "run_failed_phase"}
                self._mark_task_idle_preserving_run(task.id)
                return {"repaired": True, "reason": "run_not_running_phase"}
            if run_status in {TaskStatus.CANCELLED.value, TaskStatus.COMPLETED.value}:
                self._mark_task_idle_preserving_run(task.id)
                return {"repaired": True, "reason": f"run_{run_status}_status"}
            if run_status == TaskStatus.FAILED.value:
                self._mark_task_failed_preserving_run(task.id, "当前流式轮次执行失败。")
                return {"repaired": True, "reason": "run_failed_status"}

        stop_event = self._latest_stop_satisfied_event(task.id)
        if stop_event and self._event_age_seconds(stop_event) >= STOP_SATISFIED_SUBSCRIBE_STALE_SECONDS:
            for _event in self._finish_stop_satisfied_run(task.id, run_id):
                pass
            return {"repaired": True, "reason": "stale_stop_satisfied"}

        if run_id:
            task = self.storage.get_task(task.id)
            meta = get_ai_search_meta(task)
            plan_version = int(meta.get("active_plan_version") or (run.get("plan_version") if run else 1) or 1)
            runtime = AiSearchRuntimeContext(self.storage, task.id, run_id, plan_version)
            deadline_seconds = self._run_deadline_seconds(runtime)
            latest_event = self.storage.get_latest_ai_search_stream_event(task.id)
            age_seconds = (
                self._event_age_seconds(latest_event)
                if isinstance(latest_event, dict)
                else self._timestamp_age_seconds((run or {}).get("created_at"))
            )
            if age_seconds >= deadline_seconds + SNAPSHOT_STALE_RUNNING_REPAIR_GRACE_SECONDS:
                for _event in self._finish_stale_running_run(task.id, run_id):
                    pass
                return {"repaired": True, "reason": "deadline_stale_running"}

        return {"repaired": False, "reason": "running_recent"}

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
            cancelled = self._cancel_running_run(
                task.id,
                run_id,
                completion_reason="interrupted",
                message="已根据新指令停止当前检索轮次。",
            )
            if isinstance(cancelled, dict):
                yield self._format_event(cancelled)
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
        agent_task: Optional[asyncio.Task[str]] = None
        terminal_event_written = False
        try:
            runtime = AiSearchRuntimeContext(self.storage, task.id, run_id, int(run.get("plan_version") or 1))
            delta_queue: asyncio.Queue[str] = asyncio.Queue()
            assistant_message_id = ""
            assistant_parts: List[str] = []
            latest_seq = start_seq
            stop_satisfied_since: Optional[float] = None
            stop_condition_met_seen = False
            report_saved_seen = False

            async def on_delta(delta: str) -> None:
                if delta:
                    await delta_queue.put(delta)

            def flush_runtime_events() -> List[str]:
                nonlocal latest_seq, stop_satisfied_since, stop_condition_met_seen, report_saved_seen
                chunks: List[str] = []
                for event in self.storage.list_ai_search_stream_events(task.id, after_seq=latest_seq, limit=500):
                    seq = int(event.get("seq") or 0)
                    if seq <= latest_seq:
                        continue
                    if self._event_indicates_stop_satisfied(event) and stop_satisfied_since is None:
                        stop_satisfied_since = time.monotonic()
                    if self._event_indicates_stop_condition_met(event):
                        stop_condition_met_seen = True
                    if self._event_indicates_report_saved(event):
                        report_saved_seen = True
                    if stop_condition_met_seen and report_saved_seen and stop_satisfied_since is None:
                        stop_satisfied_since = time.monotonic()
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
            cancel_requested = False
            stop_satisfied_timeout = False
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
                if self._run_cancel_requested(task.id, run_id):
                    cancel_requested = True
                    agent_task.cancel()
                    break
                if (
                    stop_satisfied_since is not None
                    and time.monotonic() - stop_satisfied_since >= STOP_SATISFIED_COMPLETION_GRACE_SECONDS
                ):
                    stop_satisfied_timeout = True
                    agent_task.cancel()
                    break
                await asyncio.sleep(0.1)

            if timed_out or cancel_requested or stop_satisfied_timeout:
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
                    terminal_event_written = True
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
                terminal_event_written = True
                return
            if stop_satisfied_timeout:
                self._clear_run_cancel_request(task.id)
                for event in self._finish_stop_satisfied_run(
                    task.id,
                    run_id,
                    runtime=runtime,
                    assistant_message_id=assistant_message_id,
                    assistant_parts=assistant_parts,
                ):
                    yield self._format_event(event)
                terminal_event_written = True
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
                terminal_event_written = True
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
            terminal_event_written = True
        except asyncio.CancelledError:
            if agent_task and not agent_task.done():
                agent_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await agent_task
            if not terminal_event_written:
                self._cancel_running_run(
                    task.id,
                    run_id,
                    completion_reason="stream_disconnected",
                    message="流式连接已断开，本轮检索已停止。",
                )
            raise
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
            stop_event = self._latest_stop_satisfied_event(session_id)
            if stop_event and self._event_age_seconds(stop_event) >= STOP_SATISFIED_SUBSCRIBE_STALE_SECONDS:
                run_id = self._active_run_id(session_id)
                for event in self._finish_stop_satisfied_run(session_id, run_id):
                    yield self._format_event(event)
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
        event = self._cancel_running_run(
            task.id,
            run_id,
            completion_reason="cancelled",
            message="本轮检索已取消。",
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
