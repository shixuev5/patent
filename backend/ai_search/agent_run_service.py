"""Agent invocation and streaming collaborator for AI Search."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from fastapi import HTTPException
from langgraph.types import Command

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime_context import build_runtime_context
from agents.ai_search.src.exceptions import ExecutionQueueTakeoverRequested
from agents.ai_search.src.orchestration.action_runtime import (
    build_pending_action_view,
    current_pending_action,
    resolve_pending_action,
)
from agents.ai_search.src.orchestration.execution_runtime import commit_round_evaluation
from agents.ai_search.src.runtime import (
    extract_latest_ai_message,
)
from agents.ai_search.src.state import (
    ACTIVE_EXECUTION_PHASES,
    PHASE_AWAITING_HUMAN_DECISION,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_CLOSE_READ,
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_COMPLETED,
    PHASE_COARSE_SCREEN,
    PHASE_DRAFTING_PLAN,
    PHASE_EXECUTE_SEARCH,
    PHASE_FAILED,
    PHASE_FEATURE_COMPARISON,
    get_ai_search_meta,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from backend.storage import TaskType
from backend.time_utils import utc_now_z

from .models import (
    ACTIVE_PLAN_REQUIRED_CODE,
    ANALYSIS_SEED_ALREADY_INITIALIZED_CODE,
    ANALYSIS_SEED_CONTEXT_MISSING_CODE,
    ANALYSIS_SEED_REQUIRED_CODE,
    DOCUMENT_REVIEW_CONFLICT_CODE,
    DOCUMENT_REVIEW_INVALID_SELECTED_CODE,
    DOCUMENT_REVIEW_INVALID_SHORTLISTED_CODE,
    DOCUMENT_REVIEW_SELECTION_REQUIRED_CODE,
    EXECUTION_QUEUE_APPEND_BLOCKED_CODE,
    EXECUTION_QUEUE_DELETE_BLOCKED_CODE,
    EXECUTION_QUEUE_DELETE_FAILED_CODE,
    EXECUTION_QUEUE_MESSAGE_NOT_FOUND_CODE,
    HUMAN_DECISION_REQUIRED_CODE,
    INVALID_SESSION_PHASE_CODE,
    MANUAL_REVIEW_RUN_REQUIRED_CODE,
    NO_SELECTED_DOCUMENTS_CODE,
    PENDING_QUESTION_EXISTS_CODE,
    PLAN_CONFIRMATION_REQUIRED_CODE,
    RESUME_NOT_AVAILABLE_CODE,
    SEARCH_IN_PROGRESS_CODE,
    STALE_PLAN_CONFIRMATION_CODE,
    AiSearchExecutionQueueResponse,
    AiSearchSnapshotResponse,
)


_AWAITING_USER_ACTION_PHASES = {
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_AWAITING_HUMAN_DECISION,
}

_TRACE_THINKING = "thinking"
_TRACE_TOOL = "tool"
_TRACE_AGENT = "agent"

_PHASE_ACTIVITY_LABELS = {
    PHASE_COLLECTING_REQUIREMENTS: "我先整理检索需求，再决定下一步。",
    PHASE_DRAFTING_PLAN: "我先起草检索计划，并检查是否还缺关键信息。",
    PHASE_EXECUTE_SEARCH: "我先推进当前检索步骤，再根据结果判断下一步。",
    PHASE_COARSE_SCREEN: "我先筛一轮候选文献，再决定哪些值得继续看。",
    PHASE_CLOSE_READ: "我先精读已选文献，再整理关键命中点。",
    PHASE_FEATURE_COMPARISON: "我先做特征对比，再判断是否继续检索。",
}

_SPECIALIST_TRACE_LABELS = {
    "query-executor": "调用检索执行 agent",
    "coarse-screener": "调用粗筛 agent",
    "close-reader": "调用精读 agent",
    "feature-comparer": "调用特征对比 agent",
}

_TOOL_TRACE_LABELS = {
    "get_workflow_context": "读取工作流上下文",
    "get_workflow_options": "读取可选动作",
    "start_plan_drafting": "进入起草计划",
    "request_user_question": "发起补充提问",
    "request_plan_confirmation": "发起计划确认",
    "probe_search_semantic": "执行语义预检",
    "probe_search_boolean": "执行布尔预检",
    "probe_count_boolean": "估算结果规模",
    "compile_confirmed_search_plan": "编译结构化计划",
    "advance_workflow": "推进工作流",
    "finalize_search_session": "完成当前结果",
}


class AiSearchAgentRunService:
    def __init__(self, facade: Any) -> None:
        self.facade = facade
        self.sessions = facade.sessions
        self.snapshots = facade.snapshots
        self.artifacts = facade.artifacts
        self.analysis_seeds = facade.analysis_seeds
        self._stream_subscribers: Dict[str, set[asyncio.Queue[Optional[Dict[str, Any]]]]] = {}
        self._stream_tasks: Dict[str, asyncio.Task[Any]] = {}
        self._stream_lock = asyncio.Lock()

    @property
    def storage(self):
        return self.facade.storage

    def _resolve_main_checkpoint_ns(self, thread_id: str) -> str:
        checkpoints = self.storage.list_ai_search_checkpoints(thread_id, limit=50)
        for item in checkpoints:
            checkpoint_ns = str(item.get("checkpoint_ns") or "")
            if not checkpoint_ns.startswith("tools:"):
                return checkpoint_ns
        return self.facade.MAIN_AGENT_CHECKPOINT_NS

    def _resolve_resume_checkpoint_id(self, thread_id: str, checkpoint_ns: str) -> Optional[str]:
        checkpoints = self.storage.list_ai_search_checkpoints(
            thread_id,
            checkpoint_ns=checkpoint_ns,
            limit=200,
        )
        for item in checkpoints:
            checkpoint_id = str(item.get("checkpoint_id") or "").strip()
            if not checkpoint_id:
                continue
            writes = self.storage.list_ai_search_checkpoint_writes(thread_id, checkpoint_ns, checkpoint_id)
            channels = {str(write.get("channel") or "").strip() for write in writes}
            if "__interrupt__" in channels:
                return checkpoint_id
        return None

    def _main_agent_config(self, thread_id: str, *, for_resume: bool = False) -> Dict[str, Any]:
        checkpoint_ns = self._resolve_main_checkpoint_ns(thread_id)
        config: Dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
            }
        }
        if for_resume:
            checkpoint_id = self._resolve_resume_checkpoint_id(thread_id, checkpoint_ns)
            if checkpoint_id:
                config["configurable"]["checkpoint_id"] = checkpoint_id
        return config

    def _main_agent_state_config(self, agent: Any, thread_id: str) -> Dict[str, Any]:
        config = self._main_agent_config(thread_id)
        config["configurable"]["__pregel_checkpointer"] = agent.checkpointer
        return config

    def _run_main_agent(self, task_id: str, thread_id: str, payload: Any, *, for_resume: bool = False) -> Dict[str, Any]:
        agent = self.facade._build_main_agent(self.storage, task_id)
        config = self._main_agent_config(thread_id, for_resume=for_resume)
        runtime_context = build_runtime_context(self.storage, task_id)
        try:
            iterator = agent.stream(payload, config, context=runtime_context)
        except TypeError as exc:
            if "context" not in str(exc):
                raise
            iterator = agent.stream(payload, config)
        for chunk in iterator:
            if "__interrupt__" in chunk:
                continue
        state = agent.get_state(self._main_agent_state_config(agent, thread_id))
        values = state.values if state else {}
        final_phase = str(get_ai_search_meta(self.storage.get_task(task_id)).get("current_phase") or "").strip()
        completion_payload = self._completion_payload_for_phase(final_phase)
        return {
            "values": values,
            "awaiting_user_action": bool(completion_payload.get("awaitingUserAction")),
            "completion_reason": str(completion_payload.get("completionReason") or "completed"),
        }

    def _format_event(self, event_type: str, session_id: str, phase: str, payload: Any) -> str:
        message = {
            "type": event_type,
            "sessionId": session_id,
            "taskId": session_id,
            "phase": phase,
            "payload": payload,
        }
        return f"data: {json.dumps(message, ensure_ascii=False)}\n\n"

    def _thinking_activity_label(self, phase: str) -> str:
        return _PHASE_ACTIVITY_LABELS.get(str(phase or "").strip(), "思考中")

    def _trace_event(
        self,
        event_type: str,
        session_id: str,
        phase: str,
        payload: Dict[str, Any],
    ) -> str:
        normalized = dict(payload or {})
        trace_id = str(normalized.get("traceId") or normalized.get("eventId") or "").strip()
        if trace_id:
            normalized.setdefault("eventId", trace_id)
        return self._format_event(event_type, session_id, phase, normalized)

    def _trace_state(self, stream_state: Dict[str, Any]) -> Dict[str, Any]:
        current = stream_state.get("trace_state")
        if isinstance(current, dict):
            return current
        current = {
            "thinking_trace_id": "",
            "traces_by_id": {},
            "tool_call_to_trace_id": {},
        }
        stream_state["trace_state"] = current
        return current

    def _start_trace(
        self,
        session_id: str,
        phase: str,
        stream_state: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Optional[str]:
        trace_id = str(payload.get("traceId") or payload.get("eventId") or "").strip()
        if not trace_id:
            return None
        trace_state = self._trace_state(stream_state)
        traces_by_id = trace_state["traces_by_id"]
        if trace_id in traces_by_id:
            return None
        normalized = {
            **payload,
            "traceId": trace_id,
            "eventId": trace_id,
            "status": str(payload.get("status") or "running").strip() or "running",
            "startedAt": str(payload.get("startedAt") or utc_now_z()),
            "endedAt": payload.get("endedAt"),
        }
        traces_by_id[trace_id] = normalized
        tool_call_id = str(normalized.get("toolCallId") or "").strip()
        if tool_call_id:
            trace_state["tool_call_to_trace_id"][tool_call_id] = trace_id
        if str(normalized.get("traceType") or "").strip() == _TRACE_THINKING:
            trace_state["thinking_trace_id"] = trace_id
        return self._trace_event("trace.started", session_id, phase, normalized)

    def _finish_trace(
        self,
        session_id: str,
        phase: str,
        stream_state: Dict[str, Any],
        trace_id: str,
        *,
        status: str,
    ) -> Optional[str]:
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            return None
        trace_state = self._trace_state(stream_state)
        traces_by_id = trace_state["traces_by_id"]
        current = traces_by_id.get(normalized_trace_id)
        if not isinstance(current, dict):
            return None
        if str(current.get("status") or "").strip() != "running":
            return None
        updated = {
            **current,
            "status": str(status or "completed").strip() or "completed",
            "endedAt": utc_now_z(),
        }
        traces_by_id[normalized_trace_id] = updated
        tool_call_id = str(updated.get("toolCallId") or "").strip()
        if tool_call_id:
            trace_state["tool_call_to_trace_id"].pop(tool_call_id, None)
        if trace_state.get("thinking_trace_id") == normalized_trace_id:
            trace_state["thinking_trace_id"] = ""
        return self._trace_event("trace.completed", session_id, phase, updated)

    def _start_thinking_trace(
        self,
        session_id: str,
        phase: str,
        stream_state: Dict[str, Any],
    ) -> Optional[str]:
        trace_state = self._trace_state(stream_state)
        existing = str(trace_state.get("thinking_trace_id") or "").strip()
        if existing:
            return None
        return self._start_trace(
            session_id,
            phase,
            stream_state,
            {
                "traceId": f"thinking-{uuid.uuid4().hex[:12]}",
                "traceType": _TRACE_THINKING,
                "label": self._thinking_activity_label(phase),
                "actorName": "main-agent",
                "status": "running",
                "startedAt": utc_now_z(),
            },
        )

    def _finish_thinking_trace(
        self,
        session_id: str,
        phase: str,
        stream_state: Dict[str, Any],
        *,
        status: str = "completed",
    ) -> Optional[str]:
        trace_state = self._trace_state(stream_state)
        thinking_trace_id = str(trace_state.get("thinking_trace_id") or "").strip()
        if not thinking_trace_id:
            return None
        return self._finish_trace(session_id, phase, stream_state, thinking_trace_id, status=status)

    def _specialist_actor_name(self, specialist_type: str) -> str:
        return str(specialist_type or "").strip() or "specialist"

    def _tool_trace_payload(self, tool_call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(tool_call, dict):
            return None
        tool_call_id = str(tool_call.get("id") or "").strip()
        tool_name = str(tool_call.get("name") or "").strip()
        if not tool_call_id or not tool_name:
            return None
        args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
        specialist_type = str(args.get("specialist_type") or args.get("subagent_type") or "").strip()
        trace_type = _TRACE_AGENT if tool_name in {"task", "run_search_specialist"} and specialist_type else _TRACE_TOOL
        label = (
            _SPECIALIST_TRACE_LABELS.get(specialist_type)
            if trace_type == _TRACE_AGENT
            else _TOOL_TRACE_LABELS.get(tool_name)
        ) or f"调用 {tool_name}"
        detail = ""
        if trace_type == _TRACE_AGENT:
            description = str(args.get("description") or "").strip()
            detail = description[:120] if description else ""
        elif tool_name == "advance_workflow":
            action = str(args.get("action") or "").strip()
            if action:
                detail = f"动作：{action}"
        elif tool_name in {"probe_search_semantic", "probe_search_boolean", "probe_count_boolean"}:
            query_text = str(args.get("query_text") or "").strip()
            if query_text:
                detail = query_text[:120]
        payload = {
            "traceId": f"trace-{tool_call_id}",
            "traceType": trace_type,
            "label": label,
            "actorName": self._specialist_actor_name(specialist_type) if trace_type == _TRACE_AGENT else "main-agent",
            "toolName": tool_name,
            "toolCallId": tool_call_id,
            "status": "running",
            "startedAt": utc_now_z(),
        }
        if specialist_type:
            payload["specialistType"] = specialist_type
        if detail:
            payload["detail"] = detail
        return payload

    def _iter_trace_message_objects(self, payload: Any) -> Any:
        if payload is None:
            return
        if isinstance(payload, tuple):
            if payload:
                yield from self._iter_trace_message_objects(payload[0])
            return
        if self._is_tool_message_chunk(payload) or hasattr(payload, "tool_calls"):
            yield payload
            return
        if isinstance(payload, dict):
            messages = payload.get("messages")
            if isinstance(messages, list):
                for item in messages:
                    yield from self._iter_trace_message_objects(item)
            for key, value in payload.items():
                if key == "messages":
                    continue
                if isinstance(value, (dict, list, tuple)):
                    yield from self._iter_trace_message_objects(value)
            return
        if isinstance(payload, list):
            for item in payload:
                yield from self._iter_trace_message_objects(item)

    def _message_tool_calls(self, message: Any) -> List[Dict[str, Any]]:
        raw = getattr(message, "tool_calls", None)
        if raw is None and isinstance(message, dict):
            raw = message.get("tool_calls")
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    def _message_tool_result(self, message: Any) -> Optional[Dict[str, Any]]:
        tool_call_id = str(getattr(message, "tool_call_id", "") or "").strip()
        if not tool_call_id and isinstance(message, dict):
            tool_call_id = str(message.get("tool_call_id") or "").strip()
        if not tool_call_id:
            return None
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        return {
            "toolCallId": tool_call_id,
            "content": self._content_to_text(content),
        }

    def _trace_events_from_payload(
        self,
        session_id: str,
        phase: str,
        stream_state: Dict[str, Any],
        payload: Any,
    ) -> List[str]:
        events: List[str] = []
        trace_state = self._trace_state(stream_state)
        for message in self._iter_trace_message_objects(payload):
            for tool_call in self._message_tool_calls(message):
                thinking_event = self._finish_thinking_trace(session_id, phase, stream_state)
                if thinking_event:
                    events.append(thinking_event)
                trace_payload = self._tool_trace_payload(tool_call)
                if not isinstance(trace_payload, dict):
                    continue
                event = self._start_trace(session_id, phase, stream_state, trace_payload)
                if event:
                    events.append(event)
            result = self._message_tool_result(message)
            if not isinstance(result, dict):
                continue
            trace_id = str(trace_state["tool_call_to_trace_id"].get(str(result.get("toolCallId") or "").strip()) or "").strip()
            if not trace_id:
                continue
            event = self._finish_trace(session_id, phase, stream_state, trace_id, status="completed")
            if event:
                events.append(event)
        return events

    def _finish_open_traces(
        self,
        session_id: str,
        phase: str,
        stream_state: Dict[str, Any],
        *,
        status: str,
    ) -> List[str]:
        trace_state = self._trace_state(stream_state)
        events: List[str] = []
        for trace_id, payload in list(trace_state["traces_by_id"].items()):
            if str((payload or {}).get("status") or "").strip() != "running":
                continue
            event = self._finish_trace(session_id, phase, stream_state, str(trace_id), status=status)
            if event:
                events.append(event)
        return events

    def _completion_payload_for_phase(self, phase: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        normalized_phase = str(phase or "").strip()
        if normalized_phase == PHASE_AWAITING_PLAN_CONFIRMATION:
            completion_reason = "awaiting_plan_confirmation"
        elif normalized_phase == PHASE_AWAITING_USER_ANSWER:
            completion_reason = "awaiting_user_answer"
        elif normalized_phase == PHASE_AWAITING_HUMAN_DECISION:
            completion_reason = "awaiting_human_decision"
        else:
            completion_reason = "completed"
        payload = {
            "awaitingUserAction": normalized_phase in _AWAITING_USER_ACTION_PHASES,
            "completionReason": completion_reason,
        }
        if isinstance(extra, dict):
            payload.update(extra)
        return payload

    def _current_run_id(self, task_id: str) -> Optional[str]:
        run = self.storage.get_ai_search_run(task_id)
        value = str(run.get("run_id") or "").strip() if isinstance(run, dict) else ""
        return value or None

    def _parse_sse_event(self, raw: str) -> Optional[Dict[str, Any]]:
        text = str(raw or "")
        if not text.strip() or text.lstrip().startswith(":"):
            return None
        lines = [line for line in text.splitlines() if line.startswith("data: ")]
        if not lines:
            return None
        payload = lines[-1][6:]
        try:
            data = json.loads(payload)
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _derive_event_entity_id(self, event: Dict[str, Any]) -> Optional[str]:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        for candidate in (
            payload.get("messageId"),
            payload.get("segmentId"),
            payload.get("stageInstanceId"),
            payload.get("eventId"),
            payload.get("actionId"),
            payload.get("queueMessageId"),
        ):
            value = str(candidate or "").strip()
            if value:
                return value
        event_type = str(event.get("type") or "").strip()
        if event_type == "run.updated":
            return str(((payload.get("run") or {}).get("runId")) or "").strip() or self._current_run_id(str(event.get("taskId") or "").strip())
        return None

    def _normalize_stream_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(event, dict):
            return {}
        return event

    def _should_persist_stream_event(self, event: Dict[str, Any]) -> bool:
        return bool(str(event.get("type") or "").strip())

    def _stream_event_to_record(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        normalized = self._normalize_stream_event(event)
        if not normalized:
            return None
        session_id = str(normalized.get("sessionId") or normalized.get("taskId") or "").strip()
        task_id = str(normalized.get("taskId") or session_id).strip()
        if not session_id or not task_id:
            return None
        payload = normalized.get("payload") if isinstance(normalized.get("payload"), dict) else {}
        run_id = str(
            normalized.get("runId")
            or payload.get("runId")
            or ((payload.get("run") or {}).get("runId") if isinstance(payload.get("run"), dict) else "")
            or self._current_run_id(task_id)
            or ""
        ).strip() or None
        return {
            "event_id": uuid.uuid4().hex,
            "session_id": session_id,
            "task_id": task_id,
            "run_id": run_id,
            "event_type": str(normalized.get("type") or "").strip(),
            "entity_id": self._derive_event_entity_id(normalized),
            "payload": normalized,
            "created_at": utc_now_z(),
        }

    def _format_persisted_stream_event(self, row: Dict[str, Any]) -> str:
        payload = dict(row.get("payload") or {})
        payload["seq"] = int(row.get("seq") or 0)
        payload["runId"] = str(row.get("run_id") or payload.get("runId") or "").strip() or None
        payload["entityId"] = str(row.get("entity_id") or payload.get("entityId") or "").strip() or None
        payload["timestamp"] = str(row.get("created_at") or payload.get("timestamp") or utc_now_z())
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def _format_live_stream_event(self, event: Dict[str, Any]) -> str:
        normalized = self._normalize_stream_event(event)
        if not normalized:
            return ""
        payload = dict(normalized)
        task_id = str(payload.get("taskId") or payload.get("sessionId") or "").strip()
        run_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        payload["taskId"] = task_id
        payload["runId"] = str(
            payload.get("runId")
            or run_payload.get("runId")
            or ((run_payload.get("run") or {}).get("runId") if isinstance(run_payload.get("run"), dict) else "")
            or self._current_run_id(task_id)
            or ""
        ).strip() or None
        payload["entityId"] = self._derive_event_entity_id(payload)
        payload["timestamp"] = str(payload.get("timestamp") or utc_now_z())
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def _broadcast_stream_event(self, session_id: str, item: Dict[str, Any]) -> None:
        queues = list(self._stream_subscribers.get(session_id, set()))
        for queue in queues:
            await queue.put(item)

    async def _run_background_stream(self, session_id: str, producer_factory: Callable[[], AsyncIterator[str]]) -> None:
        try:
            async for raw_event in producer_factory():
                parsed = self._parse_sse_event(raw_event)
                if not parsed:
                    continue
                normalized = self._normalize_stream_event(parsed)
                if not normalized:
                    continue
                if self._should_persist_stream_event(normalized):
                    record = self._stream_event_to_record(normalized)
                    if not record:
                        continue
                    row = self.storage.append_ai_search_stream_event(record)
                    if not isinstance(row, dict):
                        continue
                    await self._broadcast_stream_event(
                        session_id,
                        {
                            "formatted": self._format_persisted_stream_event(row),
                            "seq": int(row.get("seq") or 0),
                        },
                    )
                    continue
                formatted = self._format_live_stream_event(normalized)
                if not formatted:
                    continue
                await self._broadcast_stream_event(session_id, {"formatted": formatted, "seq": None})
        finally:
            async with self._stream_lock:
                existing = self._stream_tasks.get(session_id)
                if existing is asyncio.current_task():
                    self._stream_tasks.pop(session_id, None)
            queues = list(self._stream_subscribers.get(session_id, set()))
            for queue in queues:
                await queue.put(None)

    async def start_background_stream(
        self,
        session_id: str,
        producer_factory: Callable[[], AsyncIterator[str]],
    ) -> None:
        async with self._stream_lock:
            existing = self._stream_tasks.get(session_id)
            if existing and not existing.done():
                return
            self._stream_tasks[session_id] = asyncio.create_task(
                self._run_background_stream(session_id, producer_factory)
            )

    async def subscribe_stream(
        self,
        session_id: str,
        owner_id: str,
        *,
        after_seq: int = 0,
    ) -> AsyncIterator[str]:
        self.sessions._get_owned_session_task(session_id, owner_id)
        replayed_seq = max(int(after_seq or 0), 0)
        queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()
        self._stream_subscribers.setdefault(session_id, set()).add(queue)
        for row in self.storage.list_ai_search_stream_events(session_id, after_seq=replayed_seq):
            replayed_seq = max(replayed_seq, int(row.get("seq") or 0))
            formatted = self._format_persisted_stream_event(row)
            if formatted:
                yield formatted
        try:
            while True:
                task = self._stream_tasks.get(session_id)
                if (not task or task.done()) and queue.empty():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=self.facade._main_agent_progress_poll_seconds())
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    if not self._stream_tasks.get(session_id):
                        break
                    continue
                item_seq = item.get("seq")
                if item_seq is not None:
                    seq_value = int(item_seq or 0)
                    if seq_value <= replayed_seq:
                        continue
                    replayed_seq = seq_value
                formatted = str(item.get("formatted") or "")
                if not formatted:
                    continue
                yield formatted
        finally:
            subscribers = self._stream_subscribers.get(session_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._stream_subscribers.pop(session_id, None)

    def _execution_queue_response(self, task_id: str, run_id: str) -> AiSearchExecutionQueueResponse:
        items = self.storage.list_ai_search_execution_queue_messages(task_id, run_id, statuses=["pending"])
        return AiSearchExecutionQueueResponse(
            items=[
                {
                    "queueMessageId": str(item.get("queue_message_id") or "").strip(),
                    "runId": str(item.get("run_id") or "").strip(),
                    "content": str(item.get("content") or ""),
                    "ordinal": int(item.get("ordinal") or 0),
                    "createdAt": str(item.get("created_at") or ""),
                }
                for item in items
                if str(item.get("queue_message_id") or "").strip()
            ]
        )

    def append_execution_queue_message(self, session_id: str, owner_id: str, content: str) -> AiSearchExecutionQueueResponse:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS)
        if phase not in ACTIVE_EXECUTION_PHASES:
            raise HTTPException(
                status_code=409,
                detail={"code": INVALID_SESSION_PHASE_CODE, "message": "当前阶段不支持添加待执行用户消息。", "phase": phase},
            )
        context = AiSearchAgentContext(self.storage, task.id)
        created = context.append_execution_message_queue(content)
        if not created:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": EXECUTION_QUEUE_APPEND_BLOCKED_CODE,
                    "message": "当前执行轮次里不能再追加新消息。",
                    "suggestion": "你可以等当前步骤结束后再试。",
                },
            )
        return self._execution_queue_response(task.id, str(created.get("run_id") or ""))

    def delete_execution_queue_message(self, session_id: str, owner_id: str, queue_message_id: str) -> AiSearchExecutionQueueResponse:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        item = self.storage.get_ai_search_execution_queue_message(queue_message_id)
        if not item or str(item.get("task_id") or "").strip() != task.id:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": EXECUTION_QUEUE_MESSAGE_NOT_FOUND_CODE,
                    "message": "这条待执行消息不存在了。",
                    "suggestion": "你可以刷新后再试，或者直接重新发送。",
                },
            )
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS)
        if phase not in ACTIVE_EXECUTION_PHASES:
            raise HTTPException(
                status_code=409,
                detail={"code": INVALID_SESSION_PHASE_CODE, "message": "当前阶段不支持删除待执行用户消息。", "phase": phase},
            )
        if str(item.get("status") or "").strip() != "pending":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": EXECUTION_QUEUE_DELETE_BLOCKED_CODE,
                    "message": "这条消息已经进入处理流程了。",
                    "suggestion": "你可以等当前步骤结束后再看结果。",
                },
            )
        context = AiSearchAgentContext(self.storage, task.id)
        if not context.delete_execution_message_queue(queue_message_id):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": EXECUTION_QUEUE_DELETE_FAILED_CODE,
                    "message": "这条待执行消息删除失败了。",
                    "suggestion": "你可以稍后再试一次。",
                },
            )
        run_id = str(item.get("run_id") or "").strip()
        return self._execution_queue_response(task.id, run_id)

    def _fail_open_stage_events(
        self,
        task_id: str,
        phase: str,
        error_message: str,
    ) -> List[str]:
        return []

    def _stream_error_payload(self, exc: Exception) -> Dict[str, Any]:
        if isinstance(exc, HTTPException):
            detail = exc.detail
            if isinstance(detail, dict):
                return {
                    "code": str(detail.get("code") or "STREAM_ERROR"),
                    "message": str(detail.get("message") or "当前流式轮次执行失败。"),
                }
            return {"code": "STREAM_ERROR", "message": str(detail or "当前流式轮次执行失败。")}
        message = str(exc or "当前流式轮次执行失败。").strip()
        lowered = message.lower()
        if "failed to generate json schema" in lowered or "tool argument schemas must be json-serializable" in lowered:
            return {
                "code": "TOOL_SCHEMA_INIT_FAILED",
                "message": "AI 检索工具初始化失败，请稍后重试。",
            }
        return {"code": "STREAM_ERROR", "message": message or "当前流式轮次执行失败。"}

    def _current_phase_value(self, task_id: str, fallback: str = PHASE_COLLECTING_REQUIREMENTS) -> str:
        task = self.storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        return str(meta.get("current_phase") or fallback or PHASE_COLLECTING_REQUIREMENTS)

    def _current_active_plan_version(self, task_id: str) -> int:
        task = self.storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        return int(meta.get("active_plan_version") or 0)

    def _pending_action(self, task_id: str, expected_type: str = "") -> Optional[Dict[str, Any]]:
        pending = current_pending_action(self.storage, task_id=task_id)
        if not pending:
            return None
        if expected_type and str(pending.get("action_type") or "").strip() != str(expected_type or "").strip():
            return None
        return pending

    def _resume_action(self, task: Any) -> Optional[Dict[str, Any]]:
        phase = self._current_phase_value(task.id)
        pending = current_pending_action(self.storage, task_id=task.id)
        if phase not in ACTIVE_EXECUTION_PHASES or not isinstance(pending, dict):
            return None
        if str(pending.get("action_type") or "").strip() != "resume":
            return None
        payload = build_pending_action_view(pending, camel_case=True) or {}
        current_todo = self.snapshots._current_todo(task)
        if not isinstance(current_todo, dict):
            return None
        if str(current_todo.get("status") or "").strip() != "failed":
            return None
        if str(payload.get("todoId") or "").strip() and str(payload.get("todoId") or "").strip() != str(current_todo.get("todo_id") or "").strip():
            return None
        payload.update(
            {
                "available": True,
                "currentTask": str(current_todo.get("todo_id") or "").strip(),
                "taskTitle": str(current_todo.get("title") or "").strip(),
                "resumeFrom": str(current_todo.get("resume_from") or payload.get("resume_from") or "").strip(),
                "attemptCount": int(current_todo.get("attempt_count") or payload.get("attempt_count") or 0),
                "lastError": str(current_todo.get("last_error") or payload.get("last_error") or "").strip(),
            }
        )
        return payload

    def _require_resume_action(self, task: Any) -> Dict[str, Any]:
        resume_action = self._resume_action(task)
        if resume_action is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": RESUME_NOT_AVAILABLE_CODE,
                    "message": "现在没有需要恢复的步骤。",
                    "suggestion": "你可以继续补充要求，或者等我下一步提示。",
                },
            )
        return resume_action

    def _require_human_decision_action(self, task: Any) -> Dict[str, Any]:
        pending_action = self.snapshots._pending_action(task, "human_decision")
        if pending_action is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": HUMAN_DECISION_REQUIRED_CODE,
                    "message": "现在还不用你来决定是否继续。",
                    "suggestion": "想补充方向或条件的话，直接发给我就行。",
                },
            )
        return pending_action

    def _build_resume_prompt(self, resume_action: Dict[str, Any]) -> str:
        payload = {
            "current_task": resume_action.get("currentTask"),
            "task_title": resume_action.get("taskTitle"),
            "resume_from": resume_action.get("resumeFrom"),
            "attempt_count": resume_action.get("attemptCount"),
            "last_error": resume_action.get("lastError"),
        }
        return (
            "继续当前失败的 AI 检索执行。"
            "这不是新的用户需求，不要回到需求收集或计划确认。"
            "先读取当前 todo、execution state、documents 与 gap context，"
            "仅围绕当前失败步骤恢复并推进到下一个合法步骤或阶段。\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    def _build_human_decision_prompt(self, task_id: str, decision_action: Dict[str, Any]) -> str:
        context = AiSearchAgentContext(self.storage, task_id)
        payload = {
            "decision_reason": decision_action.get("reason"),
            "decision_summary": decision_action.get("summary"),
            "round_count": decision_action.get("roundCount"),
            "no_progress_round_count": decision_action.get("noProgressRoundCount"),
            "selected_count": decision_action.get("selectedCount"),
            "gap_context": context.latest_gap_context(),
        }
        return (
            "这不是新的用户需求。"
            "你现在必须立刻调用 `request_human_decision` 发起人工决策 interrupt，"
            "不要重新检索，不要自己替用户做决定。"
            "当 interrupt 恢复后：如果用户选择 `continue_search`，就回到 `drafting_plan` 并重新起草计划；"
            "如果用户选择 `complete_current_results`，就调用 `finalize_search_session(force_from_decision=true)` 结束当前结果。\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    def _build_resume_close_read_prompt(self, task_id: str) -> str:
        context = AiSearchAgentContext(self.storage, task_id)
        payload = {
            "phase": PHASE_CLOSE_READ,
            "active_plan_version": context.active_plan_version(),
            "active_batch_id": context.active_batch_id(),
            "gap_context": context.latest_gap_context(),
        }
        return (
            "这不是新的用户需求。"
            "当前是人工送审复核后的继续执行。"
            "你必须读取最新执行上下文，只处理当前 active close_read batch，完成精读后继续按主流程推进。"
            "不要重新起草计划，不要跳过 close_read，不要绕过主流程。\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    def _build_resume_feature_comparison_prompt(self, task_id: str) -> str:
        context = AiSearchAgentContext(self.storage, task_id)
        payload = {
            "phase": PHASE_FEATURE_COMPARISON,
            "active_plan_version": context.active_plan_version(),
            "active_batch_id": context.active_batch_id(),
            "gap_context": context.latest_gap_context(),
        }
        return (
            "这不是新的用户需求。"
            "当前是人工调整 selected 文献后的继续执行。"
            "你必须读取最新执行上下文，重新完成 feature comparison，并根据结果继续按主流程推进。"
            "不要绕过主流程，不要自己直接结束，除非流程判断应当完成。\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    def _decision_termination_reason(self, task: Any) -> str:
        decision = self._require_human_decision_action(task)
        reason = str(decision.get("reason") or "").strip()
        summary = str(decision.get("summary") or "").strip()
        parts = ["人工决策后按当前结果完成"]
        if reason:
            parts.append(f"原因：{reason}")
        if summary:
            parts.append(summary)
        return "；".join(parts)

    def _require_pending_action(
        self,
        task_id: str,
        *,
        expected_type: str,
        error_code: str,
        message: str,
    ) -> Dict[str, Any]:
        pending = self._pending_action(task_id, expected_type=expected_type)
        if pending is None:
            raise HTTPException(status_code=409, detail={"code": error_code, "message": message})
        return pending

    def _reconcile_drafting_outcome(self, task_id: str) -> None:
        if self._current_phase_value(task_id) != PHASE_DRAFTING_PLAN:
            return

        context = AiSearchAgentContext(self.storage, task_id)
        pending = context.current_pending_action()
        if isinstance(pending, dict):
            action_type = str(pending.get("action_type") or "").strip()
            active_plan_version = int(pending.get("plan_version") or 0) or None
            if not active_plan_version and action_type != "plan_confirmation":
                active_plan_version = int(context.active_plan_version() or 0) or None
            run_id = str(pending.get("run_id") or "").strip() or None
            if action_type == "question":
                context.update_task_phase(
                    PHASE_AWAITING_USER_ANSWER,
                    active_plan_version=active_plan_version,
                    run_id=run_id,
                )
                return
            if action_type == "plan_confirmation":
                context.update_task_phase(
                    PHASE_AWAITING_PLAN_CONFIRMATION,
                    active_plan_version=active_plan_version,
                    run_id=run_id,
                )
                return

    def _recover_cancelled_drafting_run(self, task_id: str) -> None:
        if self._current_phase_value(task_id) != PHASE_DRAFTING_PLAN:
            return

        self._reconcile_drafting_outcome(task_id)

    def _resolve_pending_action(
        self,
        task_id: str,
        *,
        expected_type: str,
        resolution: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        context = AiSearchAgentContext(self.storage, task_id)
        return resolve_pending_action(
            context,
            expected_action_type=expected_type,
            resolution=resolution,
        )

    def _init_stream_state(self, snapshot: AiSearchSnapshotResponse, previous_assistant: str) -> Dict[str, Any]:
        known_message_ids = {
            str(item.get("message_id") or "").strip()
            for item in self.snapshots._snapshot_messages(snapshot)
            if str(item.get("message_id") or "").strip()
        }
        phase = self.snapshots._snapshot_phase(snapshot)
        emitted_phases = {phase} if phase else set()
        return {
            "emitted_phases": emitted_phases,
            "final_values": {},
            "known_message_ids": known_message_ids,
            "last_snapshot": snapshot,
            "main_agent_message": {
                "buffer": "",
                "message_id": "",
                "created_at": "",
                "persisted": False,
            },
            "previous_assistant": str(previous_assistant or "").strip(),
            "last_snapshot_diff_monotonic": 0.0,
            "snapshot_diff_pending": False,
        }

    async def _iterate_stream_with_keepalive(self, iterator: AsyncIterator[Any]) -> AsyncIterator[Any]:
        pending = asyncio.create_task(iterator.__anext__())
        while True:
            try:
                item = await asyncio.wait_for(asyncio.shield(pending), timeout=self.facade._main_agent_progress_poll_seconds())
            except asyncio.TimeoutError:
                yield None
                continue
            except StopAsyncIteration:
                break
            yield item
            pending = asyncio.create_task(iterator.__anext__())

    def _normalize_stream_item(self, item: Any) -> tuple[str, Any]:
        mode = ""
        payload = item
        if isinstance(item, tuple):
            if len(item) == 3:
                _namespace, mode, payload = item
            elif len(item) == 2:
                first, second = item
                if isinstance(first, str) and first in {"updates", "messages", "custom"}:
                    mode, payload = first, second
        elif isinstance(item, dict):
            if "type" in item and "data" in item:
                mode = str(item.get("type") or "")
                payload = item.get("data")
            elif len(item) == 1:
                only_key = next(iter(item.keys()))
                if only_key in {"updates", "messages", "custom"}:
                    mode = str(only_key)
                    payload = item[only_key]
        return str(mode or ""), payload

    def _content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            if "text" in content:
                return str(content.get("text") or "")
            if "content" in content:
                return self._content_to_text(content.get("content"))
            return ""
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    item_type = str(item.get("type") or "").strip()
                    if item_type in {"", "text"} or "text" in item:
                        parts.append(str(item.get("text") or ""))
            return "".join(parts)
        return ""

    def _split_message_payload(self, payload: Any) -> tuple[Any, Dict[str, Any]]:
        chunk = payload
        metadata: Dict[str, Any] = {}
        if isinstance(payload, (tuple, list)) and payload:
            chunk = payload[0]
            if len(payload) > 1 and isinstance(payload[1], dict):
                metadata = dict(payload[1])
        return chunk, metadata

    def _message_chunk_type_name(self, chunk: Any) -> str:
        return type(chunk).__name__

    def _is_tool_message_chunk(self, chunk: Any) -> bool:
        return self._message_chunk_type_name(chunk) in {"ToolMessage", "ToolMessageChunk"}

    def _is_model_text_chunk(self, chunk: Any, metadata: Dict[str, Any]) -> bool:
        if self._is_tool_message_chunk(chunk):
            return False
        langgraph_node = str(metadata.get("langgraph_node") or "").strip().lower()
        if langgraph_node and langgraph_node != "model":
            return False
        if isinstance(chunk, (str, dict)):
            return True
        if hasattr(chunk, "content"):
            return True
        return False

    def _is_main_agent_message_stream(self, payload: Any) -> bool:
        chunk, metadata = self._split_message_payload(payload)
        if not self._is_model_text_chunk(chunk, metadata):
            return False
        return True

    def _extract_message_delta(self, payload: Any) -> str:
        chunk, _metadata = self._split_message_payload(payload)
        if isinstance(chunk, str):
            return chunk
        if isinstance(chunk, dict):
            if "content" in chunk:
                return self._content_to_text(chunk.get("content"))
            if "text" in chunk:
                return str(chunk.get("text") or "")
            return ""
        if hasattr(chunk, "content"):
            return self._content_to_text(getattr(chunk, "content"))
        return ""

    def _normalize_custom_event(self, payload: Any) -> tuple[str, Dict[str, Any]]:
        if not isinstance(payload, dict):
            return "", {}
        event_type = str(payload.get("type") or "").strip()
        event_payload = payload.get("payload")
        if isinstance(event_payload, dict):
            return event_type, event_payload
        if event_payload is None:
            return event_type, {}
        return event_type, {"value": event_payload}

    def _run_updated_payload(self, snapshot: AiSearchSnapshotResponse) -> Dict[str, Any]:
        return {
            "session": snapshot.session.model_dump(mode="python"),
            "run": snapshot.run if isinstance(snapshot.run, dict) else {},
            "plan": snapshot.plan.get("currentPlan") if isinstance(snapshot.plan, dict) else None,
            "artifacts": snapshot.artifacts.model_dump(mode="python"),
        }

    def _main_agent_message_state(self, stream_state: Dict[str, Any]) -> Dict[str, Any]:
        current = stream_state.get("main_agent_message")
        if isinstance(current, dict):
            return current
        current = {
            "buffer": "",
            "message_id": "",
            "created_at": "",
            "persisted": False,
        }
        stream_state["main_agent_message"] = current
        return current

    def _build_message_created_event(
        self,
        session_id: str,
        phase: str,
        message: Dict[str, Any],
    ) -> str:
        return self._format_event("message.created", session_id, phase, message)

    def _persist_main_agent_message_if_needed(
        self,
        task_id: str,
        stream_state: Dict[str, Any],
        content: str,
    ) -> Optional[Dict[str, Any]]:
        message_state = self._main_agent_message_state(stream_state)
        if bool(message_state.get("persisted")):
            message_id = str(message_state.get("message_id") or "").strip()
            return self.storage.get_ai_search_message(message_id) if message_id else None
        normalized_content = str(content or "").strip()
        if not normalized_content:
            return None
        for item in reversed(self.storage.list_ai_search_messages(task_id)):
            if str(item.get("kind") or "").strip() != "plan_confirmation":
                continue
            if str(item.get("content") or "").strip() == normalized_content:
                message_state["persisted"] = True
                message_state["message_id"] = str(item.get("message_id") or "").strip()
                return None
        message_id = str(message_state.get("message_id") or uuid.uuid4().hex).strip()
        created_at = str(message_state.get("created_at") or utc_now_z())
        self.facade._append_message(
            task_id,
            "assistant",
            "chat",
            normalized_content,
            message_id=message_id,
            plan_version=self._current_active_plan_version(task_id) or None,
            metadata={},
        )
        self.storage.update_ai_search_message(
            message_id,
            created_at=created_at,
        )
        message_state["persisted"] = True
        message_state["message_id"] = message_id
        message_state["created_at"] = created_at
        stored = self.storage.get_ai_search_message(message_id)
        if isinstance(stored, dict):
            stream_state["known_message_ids"].add(message_id)
            return stored
        return {
            "message_id": message_id,
            "task_id": task_id,
            "plan_version": self._current_active_plan_version(task_id) or None,
            "role": "assistant",
            "kind": "chat",
            "content": normalized_content,
            "stream_status": "completed",
            "question_id": None,
            "metadata": {},
            "created_at": created_at,
        }

    def _main_agent_message_content(
        self,
        stream_state: Dict[str, Any],
        *,
        allow_model_fallback: bool = False,
        fallback_values: Optional[Dict[str, Any]] = None,
    ) -> str:
        message_state = self._main_agent_message_state(stream_state)
        content = str(message_state.get("buffer") or "")
        if allow_model_fallback and not content.strip() and isinstance(fallback_values, dict):
            fallback = extract_latest_ai_message(fallback_values)
            if fallback and fallback != str(stream_state.get("previous_assistant") or "").strip():
                content = fallback
        return str(content or "")

    def _flush_main_agent_message_if_needed(
        self,
        task_id: str,
        session_id: str,
        phase: str,
        stream_state: Dict[str, Any],
        *,
        content: Optional[str] = None,
        allow_model_fallback: bool = False,
        fallback_values: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        message_state = self._main_agent_message_state(stream_state)
        if bool(message_state.get("persisted")):
            return []
        resolved_content = str(content or "").strip()
        if not resolved_content:
            resolved_content = self._main_agent_message_content(
                stream_state,
                allow_model_fallback=allow_model_fallback,
                fallback_values=fallback_values,
            ).strip()
        if not resolved_content:
            return []
        message_state["buffer"] = resolved_content
        stored = self._persist_main_agent_message_if_needed(task_id, stream_state, resolved_content)
        if not isinstance(stored, dict):
            return []
        return [self._build_message_created_event(session_id, phase, stored)]

    async def _emit_snapshot_diff_events(
        self,
        previous: AiSearchSnapshotResponse,
        current: AiSearchSnapshotResponse,
        *,
        stream_state: Dict[str, Any],
    ) -> AsyncIterator[str]:
        session_id = current.session.sessionId
        phase = self.snapshots._snapshot_phase(current)

        if phase and phase not in stream_state["emitted_phases"]:
            stream_state["emitted_phases"].add(phase)
        if current.run != previous.run or current.session != previous.session or current.plan != previous.plan or current.artifacts != previous.artifacts:
            yield self._format_event("run.updated", session_id, phase, self._run_updated_payload(current))

        for message in self.snapshots._snapshot_messages(current):
            message_id = str(message.get("message_id") or "").strip()
            if message_id and message_id in stream_state["known_message_ids"]:
                continue
            if message_id:
                stream_state["known_message_ids"].add(message_id)
            yield self._format_event("message.created", session_id, phase, message)

        current_todos = current.retrieval.get("todos") if isinstance(current.retrieval, dict) else []
        previous_todos = previous.retrieval.get("todos") if isinstance(previous.retrieval, dict) else []
        current_active_todo = current.retrieval.get("activeTodo") if isinstance(current.retrieval, dict) else None
        previous_active_todo = previous.retrieval.get("activeTodo") if isinstance(previous.retrieval, dict) else None
        if current_todos != previous_todos or current_active_todo != previous_active_todo:
            yield self._format_event("todo.updated", session_id, phase, {"items": current_todos, "activeTodo": current_active_todo})
        current_pending = current.conversation.get("pendingAction") if isinstance(current.conversation, dict) else None
        previous_pending = previous.conversation.get("pendingAction") if isinstance(previous.conversation, dict) else None
        if current_pending != previous_pending:
            if isinstance(current_pending, dict):
                self.facade.notify_pending_action_required(session_id, current_pending)
            yield self._format_event("pending_action.updated", session_id, phase, current_pending)
        current_candidates = ((current.retrieval.get("documents") or {}).get("candidates")) if isinstance(current.retrieval, dict) else []
        previous_candidates = ((previous.retrieval.get("documents") or {}).get("candidates")) if isinstance(previous.retrieval, dict) else []
        current_selected = ((current.retrieval.get("documents") or {}).get("selected")) if isinstance(current.retrieval, dict) else []
        previous_selected = ((previous.retrieval.get("documents") or {}).get("selected")) if isinstance(previous.retrieval, dict) else []
        if current_candidates != previous_candidates or current_selected != previous_selected:
            yield self._format_event(
                "documents.updated",
                session_id,
                phase,
                {
                    "candidates": current_candidates or [],
                    "selected": current_selected or [],
                },
            )
        current_batch = current.analysis.get("activeBatch") if isinstance(current.analysis, dict) else None
        previous_batch = previous.analysis.get("activeBatch") if isinstance(previous.analysis, dict) else None
        if current_batch != previous_batch and current_batch:
            if not previous_batch or str(previous_batch.get("batch_id") or previous_batch.get("batchId") or "").strip() != str(current_batch.get("batch_id") or current_batch.get("batchId") or "").strip():
                yield self._format_event("batch.created", session_id, phase, current_batch)
        current_feature = current.analysis.get("latestFeatureCompareResult") if isinstance(current.analysis, dict) else None
        previous_feature = current.analysis.get("latestFeatureCompareResult") if isinstance(previous.analysis, dict) else None
        current_close_read = current.analysis.get("latestCloseReadResult") if isinstance(current.analysis, dict) else None
        previous_close_read = previous.analysis.get("latestCloseReadResult") if isinstance(previous.analysis, dict) else None
        if current_batch != previous_batch or current_feature != previous_feature or current_close_read != previous_close_read:
            yield self._format_event(
                "batch.updated",
                session_id,
                phase,
                {
                    "activeBatch": current_batch,
                    "latestCloseReadResult": current_close_read,
                    "latestFeatureCompareResult": current_feature,
                },
            )

    async def _emit_current_snapshot_diff_events(
        self,
        *,
        session_id: str,
        owner_id: str,
        stream_state: Dict[str, Any],
        force: bool = False,
    ) -> AsyncIterator[str]:
        now = time.monotonic()
        last = float(stream_state.get("last_snapshot_diff_monotonic") or 0.0)
        if not force and last > 0 and now - last < 0.5:
            stream_state["snapshot_diff_pending"] = True
            return
        snapshot = self.snapshots.get_snapshot(session_id, owner_id)
        async for event in self._emit_snapshot_diff_events(
            stream_state["last_snapshot"],
            snapshot,
            stream_state=stream_state,
        ):
            yield event
        stream_state["last_snapshot"] = snapshot
        stream_state["last_snapshot_diff_monotonic"] = now
        stream_state["snapshot_diff_pending"] = False

    async def _consume_live_agent_stream(
        self,
        *,
        session_id: str,
        owner_id: str,
        task_id: str,
        agent: Any,
        payload: Any,
        stream_state: Dict[str, Any],
        initial_snapshot: AiSearchSnapshotResponse,
        config: Optional[Dict[str, Any]] = None,
        forward_model_text: bool = True,
        emit_run_started: bool = True,
    ) -> AsyncIterator[str]:
        initial_phase = self.snapshots._snapshot_phase(initial_snapshot)
        if emit_run_started:
            yield self._format_event("run.started", session_id, initial_phase, {})
            thinking_event = self._start_thinking_trace(session_id, initial_phase, stream_state)
            if thinking_event:
                yield thinking_event

        try:
            iterator = agent.astream(
                payload,
                config,
                context=build_runtime_context(self.storage, task_id),
                stream_mode=["updates", "messages", "custom"],
                version="v2",
            )
        except TypeError as exc:
            if "context" not in str(exc):
                raise
            iterator = agent.astream(
                payload,
                config,
                stream_mode=["updates", "messages", "custom"],
                version="v2",
            )
        async for item in self._iterate_stream_with_keepalive(iterator):
            if item is None:
                yield ": keepalive\n\n"
                continue
            mode, raw_payload = self._normalize_stream_item(item)
            if mode == "updates":
                for event in self._trace_events_from_payload(session_id, self._current_phase_value(task_id, initial_phase), stream_state, raw_payload):
                    yield event
                async for event in self._emit_current_snapshot_diff_events(
                    session_id=session_id,
                    owner_id=owner_id,
                    stream_state=stream_state,
                ):
                    yield event
                continue
            if mode == "custom":
                event_type, _event_payload = self._normalize_custom_event(raw_payload)
                if not event_type:
                    continue
                if event_type == "snapshot.changed":
                    async for event in self._emit_current_snapshot_diff_events(
                        session_id=session_id,
                        owner_id=owner_id,
                        stream_state=stream_state,
                    ):
                        yield event
                continue

            if mode != "messages":
                continue

            for event in self._trace_events_from_payload(session_id, self._current_phase_value(task_id, initial_phase), stream_state, raw_payload):
                yield event

            if not self._is_main_agent_message_stream(raw_payload):
                continue
            delta = self._extract_message_delta(raw_payload)
            if not delta:
                continue
            if not forward_model_text:
                continue
            thinking_event = self._finish_thinking_trace(
                session_id,
                self._current_phase_value(task_id, initial_phase),
                stream_state,
            )
            if thinking_event:
                yield thinking_event
            message_state = self._main_agent_message_state(stream_state)
            if not str(message_state.get("created_at") or "").strip():
                message_state["created_at"] = utc_now_z()
            message_state["buffer"] = f"{message_state.get('buffer') or ''}{delta}"

    async def _stream_main_agent_execution(
        self,
        *,
        task: Any,
        owner_id: str,
        thread_id: str,
        payload: Any,
        for_resume: bool = False,
        persist_fallback_assistant: bool = False,
        post_run: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
    ) -> AsyncIterator[str]:
        previous_assistant = self.snapshots._latest_assistant_chat(task.id)
        initial_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
        stream_state = self._init_stream_state(initial_snapshot, previous_assistant)
        initial_phase = self.snapshots._snapshot_phase(initial_snapshot)

        try:
            agent = self.facade._build_main_agent(self.storage, task.id) if self.facade._uses_default_run_main_agent() else None
            if hasattr(agent, "astream") and callable(getattr(agent, "astream")):
                yield self._format_event("run.started", task.id, initial_phase, {})
                thinking_event = self._start_thinking_trace(task.id, initial_phase, stream_state)
                if thinking_event:
                    yield thinking_event
                async for event in self._consume_live_agent_stream(
                    session_id=task.id,
                    owner_id=owner_id,
                    task_id=task.id,
                    agent=agent,
                    payload=payload,
                    stream_state=stream_state,
                    initial_snapshot=initial_snapshot,
                    config=self._main_agent_config(thread_id, for_resume=for_resume),
                    forward_model_text=True,
                    emit_run_started=False,
                ):
                    yield event
                state = None
                if hasattr(agent, "get_state") and hasattr(agent, "checkpointer"):
                    state = agent.get_state(self._main_agent_state_config(agent, thread_id))
                stream_state["final_values"] = state.values if state is not None else {}
            else:
                yield self._format_event("run.started", task.id, initial_phase, {})
                thinking_event = self._start_thinking_trace(task.id, initial_phase, stream_state)
                if thinking_event:
                    yield thinking_event
                result = await asyncio.to_thread(
                    self.facade._run_main_agent,
                    task.id,
                    thread_id,
                    payload,
                    for_resume=for_resume,
                )
                stream_state["final_values"] = result["values"]

            completion_payload = post_run(stream_state) if post_run else {}
            if not isinstance(completion_payload, dict):
                completion_payload = {}

            self._reconcile_drafting_outcome(task.id)
            pre_completion_phase = self._current_phase_value(task.id, self.snapshots._snapshot_phase(stream_state["last_snapshot"]))
            if bool(stream_state.get("snapshot_diff_pending")):
                async for event in self._emit_current_snapshot_diff_events(
                    session_id=task.id,
                    owner_id=owner_id,
                    stream_state=stream_state,
                    force=True,
                ):
                    yield event
            allow_main_agent_fallback = bool(persist_fallback_assistant) and pre_completion_phase not in _AWAITING_USER_ACTION_PHASES
            for event in self._flush_main_agent_message_if_needed(
                task.id,
                task.id,
                pre_completion_phase,
                stream_state,
                allow_model_fallback=allow_main_agent_fallback,
                fallback_values=stream_state.get("final_values") if allow_main_agent_fallback else None,
            ):
                yield event
            final_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
            self.snapshots._validate_drafting_outcome(task.id, final_snapshot)
            async for event in self._emit_snapshot_diff_events(
                stream_state["last_snapshot"],
                final_snapshot,
                stream_state=stream_state,
            ):
                yield event
            stream_state["last_snapshot"] = final_snapshot

            final_phase = self._current_phase_value(task.id, self.snapshots._snapshot_phase(final_snapshot))
            for event in self._finish_open_traces(task.id, final_phase, stream_state, status="completed"):
                yield event
            yield self._format_event(
                "run.completed",
                task.id,
                final_phase,
                self._completion_payload_for_phase(final_phase, completion_payload),
            )
        except ExecutionQueueTakeoverRequested as exc:
            handoff_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
            async for event in self._emit_snapshot_diff_events(
                stream_state["last_snapshot"],
                handoff_snapshot,
                stream_state=stream_state,
            ):
                yield event
            stream_state["last_snapshot"] = handoff_snapshot
            async for event in self._stream_main_agent_execution(
                task=self.storage.get_task(task.id),
                owner_id=owner_id,
                thread_id=thread_id,
                payload={"messages": [{"role": "user", "content": exc.takeover_prompt}]},
                persist_fallback_assistant=True,
            ):
                yield event
        except asyncio.CancelledError:
            try:
                self._recover_cancelled_drafting_run(task.id)
            except Exception:
                pass
            raise
        except Exception as exc:
            failed_phase = self._current_phase_value(task.id, self.snapshots._snapshot_phase(initial_snapshot))
            for event in self._finish_open_traces(task.id, failed_phase, stream_state, status="failed"):
                yield event
            for event in self._fail_open_stage_events(
                task.id,
                failed_phase,
                self._stream_error_payload(exc).get("message", "当前流式轮次执行失败。"),
            ):
                yield event
            yield self._format_event(
                "run.failed",
                task.id,
                failed_phase,
                self._stream_error_payload(exc),
            )

    async def _stream_message_events(self, session_id: str, owner_id: str, content: str) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS)
        if phase in ACTIVE_EXECUTION_PHASES:
            raise HTTPException(
                status_code=409,
                detail={"code": SEARCH_IN_PROGRESS_CODE, "message": "检索执行阶段不支持发送普通消息；如需继续失败步骤，请调用 resume 接口。"},
            )
        if phase == PHASE_AWAITING_HUMAN_DECISION:
            self.sessions._raise_invalid_phase(phase, "当前处于人工决策状态，请使用继续检索或按当前结果完成。")
        if phase == PHASE_AWAITING_USER_ANSWER and self._pending_action(task.id, expected_type="question"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": PENDING_QUESTION_EXISTS_CODE,
                    "message": "我这边还有一个追问没回答。",
                    "suggestion": "你先直接回复那个问题，我再继续往下检索。",
                },
            )
        if phase not in self.facade.DEFAULT_MESSAGE_PHASES:
            self.sessions._raise_invalid_phase(phase, "当前阶段不允许发送普通消息。")

        if phase == PHASE_AWAITING_PLAN_CONFIRMATION:
            active_plan_version = int(meta.get("active_plan_version") or 0)
            if active_plan_version > 0:
                self.storage.update_ai_search_plan(task.id, active_plan_version, status="superseded", superseded_at=utc_now_z())
            pending = self._pending_action(task.id, expected_type="plan_confirmation")
            if pending:
                self._resolve_pending_action(
                    task.id,
                    expected_type="plan_confirmation",
                    resolution={"decision": "rejected"},
                )
            self.facade._update_phase(task.id, PHASE_DRAFTING_PLAN)

        self.facade._append_message(task.id, "user", "chat", content)
        self.facade._update_phase(task.id, PHASE_DRAFTING_PLAN)
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        async for event in self._stream_main_agent_execution(
            task=self.storage.get_task(task.id),
            owner_id=owner_id,
            thread_id=thread_id,
            payload={"messages": [{"role": "user", "content": content}]},
            persist_fallback_assistant=True,
        ):
            yield event

    async def _stream_resume_events(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        resume_action = self._require_resume_action(task)
        pending_action = self._require_pending_action(
            task.id,
            expected_type="resume",
            error_code=RESUME_NOT_AVAILABLE_CODE,
            message="当前没有可恢复的失败执行步骤。",
        )
        checkpoint_ref = pending_action.get("payload") if isinstance(pending_action.get("payload"), dict) else {}
        payload_checkpoint = checkpoint_ref.get("checkpoint_ref") if isinstance(checkpoint_ref, dict) else None
        if isinstance(payload_checkpoint, dict):
            checkpoint_ns = str(payload_checkpoint.get("checkpoint_ns") or "").strip()
            checkpoint_id = str(payload_checkpoint.get("checkpoint_id") or "").strip()
            current_checkpoint_ns = self._resolve_main_checkpoint_ns(thread_id)
            current_checkpoint_id = self._resolve_resume_checkpoint_id(thread_id, current_checkpoint_ns) if current_checkpoint_ns else None
            if checkpoint_ns and checkpoint_ns != current_checkpoint_ns:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": RESUME_NOT_AVAILABLE_CODE,
                        "message": "恢复点已经失效了。",
                        "suggestion": "你可以刷新后再试，或者直接补充新的要求。",
                    },
                )
            if checkpoint_id and current_checkpoint_id and checkpoint_id != current_checkpoint_id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": RESUME_NOT_AVAILABLE_CODE,
                        "message": "恢复点已经失效了。",
                        "suggestion": "你可以刷新后再试，或者直接补充新的要求。",
                    },
                )
        self._resolve_pending_action(task.id, expected_type="resume", resolution={"decision": "resume"})
        async for event in self._stream_main_agent_execution(
            task=task,
            owner_id=owner_id,
            thread_id=thread_id,
            payload={"messages": [{"role": "user", "content": self._build_resume_prompt(resume_action)}]},
        ):
            yield event

    async def _stream_answer_events(self, session_id: str, owner_id: str, question_id: str, answer: str) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or "")
        pending_action = self._require_pending_action(
            task.id,
            expected_type="question",
            error_code=PENDING_QUESTION_EXISTS_CODE,
            message="当前没有待回答的问题。",
        )
        pending_payload = pending_action.get("payload") if isinstance(pending_action, dict) else {}
        pending_question_id = str(pending_payload.get("question_id") or "").strip()
        if phase != PHASE_AWAITING_USER_ANSWER or not pending_question_id:
            self.sessions._raise_invalid_phase(phase, "当前没有待回答的问题。")
        if pending_question_id != question_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": PENDING_QUESTION_EXISTS_CODE,
                    "message": "刚才那个问题已经过期了。",
                    "suggestion": "你可以直接重新说明补充信息，我会按最新内容继续。",
                },
            )
        self.facade._append_message(task.id, "user", "answer", answer, question_id=question_id)
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        async for event in self._stream_main_agent_execution(
            task=task,
            owner_id=owner_id,
            thread_id=thread_id,
            payload=Command(resume=answer),
            for_resume=True,
            persist_fallback_assistant=True,
        ):
            yield event

    async def _stream_plan_confirmation_events(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or "")
        if phase != PHASE_AWAITING_PLAN_CONFIRMATION:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": PLAN_CONFIRMATION_REQUIRED_CODE,
                    "message": "现在还没有待确认的计划。",
                    "suggestion": "你可以先补充要求，等我给出计划后再回复“确认计划”。",
                },
            )
        pending_action = self._require_pending_action(
            task.id,
            expected_type="plan_confirmation",
            error_code=PLAN_CONFIRMATION_REQUIRED_CODE,
            message="当前没有待确认的检索计划。",
        )
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")

        def _post_run(_: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            updated_task = self.storage.get_task(task.id)
            updated_meta = get_ai_search_meta(updated_task)
            active_plan_version = int(updated_meta.get("active_plan_version") or 0)
            updated_plan = self.storage.get_ai_search_plan(task.id, active_plan_version) if active_plan_version > 0 else None
            if not updated_plan or str(updated_plan.get("status") or "") != "confirmed":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": PLAN_CONFIRMATION_REQUIRED_CODE,
                        "message": "这次计划确认还没有生效。",
                        "suggestion": "你可以稍后再试一次，或者先补充修改意见。",
                    },
                )
            return {}

        async for event in self._stream_main_agent_execution(
            task=task,
            owner_id=owner_id,
            thread_id=thread_id,
            payload=Command(resume={"confirmed": True}),
            for_resume=True,
            post_run=_post_run,
        ):
            yield event

    async def _stream_analysis_seed_events(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        source_type = str(meta.get("source_type") or "").strip()
        source_label = "AI 分析" if source_type == "analysis" else "AI 答复"
        if source_type not in {"analysis", "reply"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": ANALYSIS_SEED_REQUIRED_CODE,
                    "message": "当前会话不是从 AI 分析或 AI 答复结果生成的。",
                    "suggestion": "你可以回到 AI 分析或 AI 答复结果页重新发起检索。",
                },
            )
        if str(meta.get("analysis_seed_status") or "").strip() != "pending":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": ANALYSIS_SEED_ALREADY_INITIALIZED_CODE,
                    "message": "这个检索计划已经初始化过了。",
                    "suggestion": "你可以直接继续当前检索。",
                },
            )
        phase = str(meta.get("current_phase") or PHASE_DRAFTING_PLAN)
        seed_prompt = str(meta.get("analysis_seed_prompt") or "").strip()
        if not seed_prompt:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": ANALYSIS_SEED_CONTEXT_MISSING_CODE,
                    "message": f"当前会话缺少 {source_label} 上下文。",
                    "suggestion": f"你可以重新从 {source_label}结果页发起检索。",
                },
            )

        run_error: Optional[Dict[str, Any]] = None
        saw_run_completed = False
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        try:
            async for event in self._stream_main_agent_execution(
                task=task,
                owner_id=owner_id,
                thread_id=thread_id,
                payload={"messages": [{"role": "user", "content": seed_prompt}]},
            ):
                if event.startswith("data: "):
                    try:
                        payload = json.loads(event[6:])
                    except Exception:
                        payload = {}
                    event_type = str(payload.get("type") or "").strip()
                    if event_type == "run.completed":
                        saw_run_completed = True
                    if event_type == "run.failed":
                        maybe_error = payload.get("payload")
                        run_error = maybe_error if isinstance(maybe_error, dict) else {"message": "生成 AI 检索计划失败。"}
                yield event
        except asyncio.CancelledError:
            reconciled_phase = self.analysis_seeds._reconcile_analysis_seed_phase(task.id)
            if reconciled_phase in {PHASE_AWAITING_PLAN_CONFIRMATION, PHASE_AWAITING_USER_ANSWER}:
                self.storage.update_task(
                    task.id,
                    metadata=merge_ai_search_meta(
                        self.storage.get_task(task.id),
                        analysis_seed_status="completed",
                    ),
                )
            raise

        reconciled_phase = self.analysis_seeds._reconcile_analysis_seed_phase(task.id)
        if run_error is not None and reconciled_phase in {PHASE_AWAITING_PLAN_CONFIRMATION, PHASE_AWAITING_USER_ANSWER}:
            run_error = None
        if run_error is not None:
            failure_message = str(run_error.get("message") or "生成 AI 检索计划失败。").strip()
            self.storage.update_task(
                task.id,
                metadata=merge_ai_search_meta(
                    self.storage.get_task(task.id),
                    current_phase=PHASE_FAILED,
                    analysis_seed_status="failed",
                ),
                status=phase_to_task_status(PHASE_FAILED),
                progress=phase_progress(PHASE_FAILED),
                current_step=phase_step(PHASE_FAILED),
                error_message=f"生成 AI 检索计划失败：{failure_message}",
            )
            self.facade._emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_failed",
                owner_id=owner_id,
                task_id=task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=False,
                message="从 AI 分析创建 AI 检索计划失败",
                payload={"analysis_task_id": str(meta.get("source_task_id") or "").strip() or None, "error": failure_message},
            )
            await asyncio.to_thread(
                self.facade.notify_task_terminal_status,
                task.id,
                PHASE_FAILED,
                error_message=f"生成 AI 检索计划失败：{failure_message}",
            )
            return

        self.storage.update_task(
            task.id,
            metadata=merge_ai_search_meta(self.storage.get_task(task.id), analysis_seed_status="completed"),
        )
        self.analysis_seeds._reconcile_analysis_seed_phase(task.id)
        snapshot = self.snapshots.get_snapshot(task.id, owner_id)
        source_task_id = str(meta.get("source_task_id") or "").strip() or None
        source_pn = str(meta.get("source_pn") or "").strip() or None
        self.facade._emit_system_log(
            category="task_execution",
            event_name="ai_search_seed_created",
            owner_id=owner_id,
            task_id=task.id,
            task_type=TaskType.AI_SEARCH.value,
            success=True,
            message="已从 AI 分析创建 AI 检索计划",
            payload={
                "analysis_task_id": source_task_id,
                "analysis_pn": source_pn,
                "phase": self.snapshots._snapshot_phase(snapshot),
            },
        )
        if self.snapshots._snapshot_phase(snapshot) == PHASE_AWAITING_PLAN_CONFIRMATION:
            pending_action = snapshot.conversation.get("pendingAction") if isinstance(snapshot.conversation, dict) else None
            self.facade._emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_plan_ready",
                owner_id=owner_id,
                task_id=task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=True,
                message="AI 检索计划已进入计划确认阶段",
                payload={"analysis_task_id": source_task_id, "plan_version": pending_action.get("plan_version") if isinstance(pending_action, dict) else None},
            )
        if self.snapshots._snapshot_phase(snapshot) == PHASE_AWAITING_USER_ANSWER:
            pending_action = snapshot.conversation.get("pendingAction") if isinstance(snapshot.conversation, dict) else None
            self.facade._emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_question_required",
                owner_id=owner_id,
                task_id=task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=True,
                message="AI 检索计划仍需用户补充信息",
                payload={"analysis_task_id": source_task_id, "question": pending_action.get("prompt") if isinstance(pending_action, dict) else None},
            )
        if not saw_run_completed:
            final_phase = self.snapshots._snapshot_phase(snapshot)
            yield self._format_event(
                "run.completed",
                task.id,
                self._current_phase_value(task.id, final_phase),
                self._completion_payload_for_phase(final_phase, {"analysisSeed": True}),
            )

    async def _stream_decision_continue_events(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        self._require_human_decision_action(task)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or "")
        if phase != PHASE_AWAITING_HUMAN_DECISION:
            self.sessions._raise_invalid_phase(phase, "当前没有待处理的人工决策。")
        plan_version = int(meta.get("active_plan_version") or 0)
        if plan_version <= 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": ACTIVE_PLAN_REQUIRED_CODE,
                    "message": "现在还没有可继续执行的计划。",
                    "suggestion": "你可以先补充要求，或者等我先给出计划。",
                },
            )
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")

        def _post_run(_: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            pending = self._pending_action(task.id, expected_type="human_decision")
            if pending is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": HUMAN_DECISION_REQUIRED_CODE,
                        "message": "人工决策恢复后仍未完成处理。",
                        "suggestion": "请稍后重试，或检查主控代理是否仍停留在人工决策 interrupt。",
                    },
                )
            return {"resumedFromDecision": True}

        async for event in self._stream_main_agent_execution(
            task=task,
            owner_id=owner_id,
            thread_id=thread_id,
            payload=Command(resume={"decision": "continue_search"}),
            for_resume=True,
            persist_fallback_assistant=True,
            post_run=_post_run,
        ):
            yield event

    async def _stream_decision_complete_events(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        self._require_human_decision_action(task)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or "")
        if phase != PHASE_AWAITING_HUMAN_DECISION:
            self.sessions._raise_invalid_phase(phase, "当前没有待处理的人工决策。")
        plan_version = int(meta.get("active_plan_version") or 0)
        if plan_version <= 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": ACTIVE_PLAN_REQUIRED_CODE,
                    "message": "现在还没有可直接完成的计划。",
                    "suggestion": "你可以先补充要求，或者等我先给出计划。",
                },
            )
        selected_documents = self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"])
        if not selected_documents:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": NO_SELECTED_DOCUMENTS_CODE,
                    "message": "现在还没有已选文献。",
                    "suggestion": "你可以先继续筛选，或者告诉我想补充的方向。",
                },
            )

        termination_reason = self._decision_termination_reason(task)
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")

        def _post_run(_: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            updated_task = self.storage.get_task(task.id)
            updated_meta = get_ai_search_meta(updated_task)
            updated_phase = str(updated_meta.get("current_phase") or "")
            if updated_phase != PHASE_COMPLETED:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": HUMAN_DECISION_REQUIRED_CODE,
                        "message": "人工决策恢复后还没有完成当前结果。",
                        "suggestion": "请检查主控代理在恢复后是否调用了 finalize_search_session(force_from_decision=true)。",
                    },
                )
            self.artifacts._finalize_terminal_artifacts(task.id, plan_version, termination_reason=termination_reason)
            self.facade.notify_task_terminal_status(task.id, PHASE_COMPLETED)
            return {"completedFromDecision": True}

        async for event in self._stream_main_agent_execution(
            task=task,
            owner_id=owner_id,
            thread_id=thread_id,
            payload=Command(resume={"decision": "complete_current_results"}),
            for_resume=True,
            post_run=_post_run,
        ):
            yield event

    def _create_manual_close_read_batch(self, task_id: str, plan_version: int, document_ids: List[str]) -> str:
        run = self.storage.get_ai_search_run(task_id, plan_version=plan_version)
        run_id = str(run.get("run_id") or "").strip() if isinstance(run, dict) else ""
        if not run_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": MANUAL_REVIEW_RUN_REQUIRED_CODE,
                    "message": "现在还不能发起人工送审复核。",
                    "suggestion": "你可以先继续当前检索，等执行轮次准备好后再操作。",
                },
            )
        batch_id = uuid.uuid4().hex
        self.storage.create_ai_search_batch(
            {
                "batch_id": batch_id,
                "run_id": run_id,
                "task_id": task_id,
                "plan_version": plan_version,
                "batch_type": "close_read",
                "status": "loaded",
                "input_hash": f"manual_review:{uuid.uuid4().hex[:8]}",
            }
        )
        self.storage.replace_ai_search_batch_documents(batch_id, run_id, document_ids)
        return batch_id

    async def _stream_document_review_events(
        self,
        session_id: str,
        owner_id: str,
        plan_version: int,
        review_document_ids: Optional[List[str]],
        remove_document_ids: Optional[List[str]],
    ) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        self._require_human_decision_action(task)
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if active_plan_version != int(plan_version):
            raise HTTPException(
                status_code=409,
                detail={"code": STALE_PLAN_CONFIRMATION_CODE, "message": "当前只允许操作活动计划版本。"},
            )
        phase = str(meta.get("current_phase") or "")
        if phase != PHASE_AWAITING_HUMAN_DECISION:
            self.sessions._raise_invalid_phase(phase, "当前仅支持在人工决策状态下复核文献。")
        review_ids = [str(item).strip() for item in (review_document_ids or []) if str(item).strip()]
        remove_ids = [str(item).strip() for item in (remove_document_ids or []) if str(item).strip()]
        if not review_ids and not remove_ids:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": DOCUMENT_REVIEW_SELECTION_REQUIRED_CODE,
                    "message": "你还没有选中文献。",
                    "suggestion": "请至少选一篇要送审或移出的文献。",
                },
            )
        overlap_ids = set(review_ids) & set(remove_ids)
        if overlap_ids:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": DOCUMENT_REVIEW_CONFLICT_CODE,
                    "message": f"同一篇文献不能同时送审和移出：{', '.join(sorted(overlap_ids))}",
                    "suggestion": "你可以把送审和移出的文献重新分开选择。",
                },
            )

        documents = self.storage.list_ai_search_documents(task.id, plan_version)
        documents_by_id = {
            str(item.get("document_id") or "").strip(): item
            for item in documents
            if str(item.get("document_id") or "").strip()
        }
        invalid_review_ids = [
            document_id
            for document_id in review_ids
            if str((documents_by_id.get(document_id) or {}).get("stage") or "") != "shortlisted"
        ]
        invalid_remove_ids = [
            document_id
            for document_id in remove_ids
            if str((documents_by_id.get(document_id) or {}).get("stage") or "") != "selected"
        ]
        if invalid_review_ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": DOCUMENT_REVIEW_INVALID_SHORTLISTED_CODE,
                    "message": f"仅允许送审当前 shortlisted 文献：{', '.join(sorted(invalid_review_ids))}",
                    "suggestion": "你可以先确认这些文献是否还在 shortlisted 列表里。",
                },
            )
        if invalid_remove_ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": DOCUMENT_REVIEW_INVALID_SELECTED_CODE,
                    "message": f"仅允许移出当前 selected 文献：{', '.join(sorted(invalid_remove_ids))}",
                    "suggestion": "你可以先确认这些文献是否还在 selected 列表里。",
                },
            )

        previous_assistant = self.snapshots._latest_assistant_chat(task.id)
        initial_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
        stream_state = self._init_stream_state(initial_snapshot, previous_assistant)
        yield self._format_event("run.started", task.id, self.snapshots._snapshot_phase(initial_snapshot), {})

        for document_id in remove_ids:
            self.storage.update_ai_search_document(
                task.id,
                plan_version,
                document_id,
                stage="shortlisted",
                user_pinned=False,
                user_removed=True,
                close_read_status="pending",
                close_read_reason="用户手动移出已选，待复核",
                agent_reason="用户手动移出已选",
            )
        context = AiSearchAgentContext(self.storage, task.id)
        for document_id in review_ids:
            self.storage.update_ai_search_document(
                task.id,
                plan_version,
                document_id,
                stage="shortlisted",
                user_pinned=True,
                user_removed=False,
                close_read_status="pending",
                close_read_reason="人工送审复核",
                agent_reason="人工送审复核",
            )
        selected_count = len(self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"]))
        self.facade._update_phase(
            task.id,
            PHASE_AWAITING_HUMAN_DECISION,
            selected_document_count=selected_count,
            active_plan_version=plan_version,
            active_batch_id=None,
        )
        updated_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
        async for event in self._emit_snapshot_diff_events(
            stream_state["last_snapshot"],
            updated_snapshot,
            stream_state=stream_state,
        ):
            yield event
        stream_state["last_snapshot"] = updated_snapshot

        meta = get_ai_search_meta(self.storage.get_task(task.id))
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")

        if review_ids:
            batch_id = self._create_manual_close_read_batch(task.id, plan_version, review_ids)
            self.facade._update_phase(
                task.id,
                PHASE_CLOSE_READ,
                active_plan_version=plan_version,
                active_batch_id=batch_id,
                selected_document_count=selected_count,
            )
            async for event in self._stream_main_agent_execution(
                task=self.storage.get_task(task.id),
                owner_id=owner_id,
                thread_id=thread_id,
                payload={"messages": [{"role": "user", "content": self._build_resume_close_read_prompt(task.id)}]},
                persist_fallback_assistant=True,
            ):
                yield event
            return

        selected_documents = self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"])
        if selected_documents:
            self.facade._update_phase(
                task.id,
                PHASE_FEATURE_COMPARISON,
                active_plan_version=plan_version,
            )
            async for event in self._stream_main_agent_execution(
                task=self.storage.get_task(task.id),
                owner_id=owner_id,
                thread_id=thread_id,
                payload={"messages": [{"role": "user", "content": self._build_resume_feature_comparison_prompt(task.id)}]},
                persist_fallback_assistant=True,
            ):
                yield event
            return
        else:
            final_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
            async for event in self._emit_snapshot_diff_events(
                stream_state["last_snapshot"],
                final_snapshot,
                stream_state=stream_state,
            ):
                yield event
            stream_state["last_snapshot"] = final_snapshot
            decision_action = {
                "reason": "manual_document_review",
                "summary": "当前无已选对比文献，请送审候选文献或继续检索。",
                "roundCount": int((context._run_state(context.active_run(plan_version)) or {}).get("execution_round_count") or 0),
                "noProgressRoundCount": int((context._run_state(context.active_run(plan_version)) or {}).get("no_progress_round_count") or 0),
                "selectedCount": 0,
            }
            async for event in self._stream_main_agent_execution(
                task=self.storage.get_task(task.id),
                owner_id=owner_id,
                thread_id=thread_id,
                payload={"messages": [{"role": "user", "content": self._build_human_decision_prompt(task.id, decision_action)}]},
                persist_fallback_assistant=True,
            ):
                yield event
            return

        yield self._format_event(
            "run.completed",
            task.id,
            self._current_phase_value(task.id, self.snapshots._snapshot_phase(stream_state["last_snapshot"])),
            self._completion_payload_for_phase(
                self._current_phase_value(task.id, self.snapshots._snapshot_phase(stream_state["last_snapshot"])),
                {"documentReview": True},
            ),
        )

    async def _stream_feature_comparison_events(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if active_plan_version != int(plan_version):
            raise HTTPException(
                status_code=409,
                detail={"code": STALE_PLAN_CONFIRMATION_CODE, "message": "当前只允许生成活动计划版本的特征对比分析结果。"},
            )
        selected_documents = self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"])
        if not selected_documents:
            self.sessions._raise_invalid_phase(PHASE_FEATURE_COMPARISON, "当前没有已选对比文件。")
        self.facade._update_phase(
            task.id,
            PHASE_FEATURE_COMPARISON,
            active_plan_version=plan_version,
        )
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")

        def _post_run(_: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            updated_task = self.storage.get_task(task.id)
            updated_meta = get_ai_search_meta(updated_task)
            updated_phase = str(updated_meta.get("current_phase") or "")
            if updated_phase == PHASE_COMPLETED:
                self.artifacts._finalize_terminal_artifacts(task.id, plan_version, termination_reason="feature_comparison_ready")
                self.facade.notify_task_terminal_status(task.id, PHASE_COMPLETED)
                return {"featureComparisonRequested": True, "completed": True}
            return {"featureComparisonRequested": True, "completed": False}

        async for event in self._stream_main_agent_execution(
            task=self.storage.get_task(task.id),
            owner_id=owner_id,
            thread_id=thread_id,
            payload={"messages": [{"role": "user", "content": self._build_resume_feature_comparison_prompt(task.id)}]},
            persist_fallback_assistant=True,
            post_run=_post_run,
        ):
            yield event

    async def _start_and_subscribe(
        self,
        session_id: str,
        owner_id: str,
        producer_factory: Callable[[], AsyncIterator[str]],
    ) -> AsyncIterator[str]:
        latest = self.storage.get_latest_ai_search_stream_event(session_id)
        after_seq = int(latest.get("seq") or 0) if isinstance(latest, dict) else 0
        await self.start_background_stream(session_id, producer_factory)
        async for event in self.subscribe_stream(session_id, owner_id, after_seq=after_seq):
            yield event

    def _validate_stream_message_request(self, session_id: str, owner_id: str) -> None:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS)
        if phase in ACTIVE_EXECUTION_PHASES:
            raise HTTPException(
                status_code=409,
                detail={"code": SEARCH_IN_PROGRESS_CODE, "message": "检索执行阶段不支持发送普通消息；如需继续失败步骤，请调用 resume 接口。"},
            )
        if phase == PHASE_AWAITING_HUMAN_DECISION:
            self.sessions._raise_invalid_phase(phase, "当前处于人工决策状态，请使用继续检索或按当前结果完成。")
        if phase == PHASE_AWAITING_USER_ANSWER and self._pending_action(task.id, expected_type="question"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": PENDING_QUESTION_EXISTS_CODE,
                    "message": "我这边还有一个追问没回答。",
                    "suggestion": "你先直接回复那个问题，我再继续往下检索。",
                },
            )
        if phase not in self.facade.DEFAULT_MESSAGE_PHASES:
            self.sessions._raise_invalid_phase(phase, "当前阶段不允许发送普通消息。")

    def _validate_stream_plan_confirmation_request(self, session_id: str, owner_id: str) -> None:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS)
        if phase != PHASE_AWAITING_PLAN_CONFIRMATION:
            self.sessions._raise_invalid_phase(phase, "当前阶段不允许确认计划。")
        self._require_pending_action(
            task.id,
            expected_type="plan_confirmation",
            error_code=PLAN_CONFIRMATION_REQUIRED_CODE,
            message="当前没有待确认的计划。",
        )

    def stream_message(self, session_id: str, owner_id: str, content: str) -> AsyncIterator[str]:
        self._validate_stream_message_request(session_id, owner_id)

        async def _runner() -> AsyncIterator[str]:
            async for event in self._start_and_subscribe(
                session_id,
                owner_id,
                lambda: self._stream_message_events(session_id, owner_id, content),
            ):
                yield event

        return _runner()

    async def stream_resume(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self._start_and_subscribe(
            session_id,
            owner_id,
            lambda: self._stream_resume_events(session_id, owner_id),
        ):
            yield event

    async def stream_answer(self, session_id: str, owner_id: str, question_id: str, answer: str) -> AsyncIterator[str]:
        async for event in self._start_and_subscribe(
            session_id,
            owner_id,
            lambda: self._stream_answer_events(session_id, owner_id, question_id, answer),
        ):
            yield event

    def stream_plan_confirmation(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        self._validate_stream_plan_confirmation_request(session_id, owner_id)

        async def _runner() -> AsyncIterator[str]:
            async for event in self._start_and_subscribe(
                session_id,
                owner_id,
                lambda: self._stream_plan_confirmation_events(session_id, owner_id),
            ):
                yield event

        return _runner()

    async def stream_analysis_seed(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self._start_and_subscribe(
            session_id,
            owner_id,
            lambda: self._stream_analysis_seed_events(session_id, owner_id),
        ):
            yield event

    async def stream_decision_continue(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self._start_and_subscribe(
            session_id,
            owner_id,
            lambda: self._stream_decision_continue_events(session_id, owner_id),
        ):
            yield event

    async def stream_decision_complete(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self._start_and_subscribe(
            session_id,
            owner_id,
            lambda: self._stream_decision_complete_events(session_id, owner_id),
        ):
            yield event

    async def stream_document_review(
        self,
        session_id: str,
        owner_id: str,
        plan_version: int,
        review_document_ids: Optional[List[str]],
        remove_document_ids: Optional[List[str]],
    ) -> AsyncIterator[str]:
        async for event in self._start_and_subscribe(
            session_id,
            owner_id,
            lambda: self._stream_document_review_events(
                session_id,
                owner_id,
                plan_version,
                review_document_ids,
                remove_document_ids,
            ),
        ):
            yield event

    async def stream_feature_comparison(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        async for event in self._start_and_subscribe(
            session_id,
            owner_id,
            lambda: self._stream_feature_comparison_events(session_id, owner_id, plan_version),
        ):
            yield event
