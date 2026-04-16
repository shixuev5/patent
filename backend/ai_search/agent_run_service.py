"""Agent invocation and streaming collaborator for AI Search."""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from fastapi import HTTPException
from langgraph.types import Command

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.exceptions import ExecutionQueueTakeoverRequested
from agents.ai_search.src.orchestration.action_runtime import (
    build_pending_action_view,
    current_pending_action,
    resolve_pending_action,
)
from agents.ai_search.src.orchestration.execution_runtime import commit_round_evaluation, enter_human_decision
from agents.ai_search.src.orchestration.planning_runtime import publish_planner_draft
from agents.ai_search.src.runtime import (
    build_process_display_metadata,
    extract_latest_ai_message,
    format_subagent_label,
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
        for chunk in agent.stream(payload, config):
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

    def _should_hide_process_event(self, event_type: str, payload: Dict[str, Any]) -> bool:
        return False

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

    def _normalize_process_stream_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        event_type = str(event.get("type") or "").strip()
        if event_type not in {"subagent.started", "subagent.completed", "tool.started", "tool.completed", "tool.failed"}:
            return event
        payload = dict(event.get("payload") or {})
        if self._should_hide_process_event(event_type, payload):
            return {}
        payload["processEventType"] = event_type
        return {
            **event,
            "type": "process.event",
            "payload": payload,
        }

    def _stream_event_to_record(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(event, dict):
            return None
        normalized = self._normalize_process_stream_event(event)
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

    async def _broadcast_stream_event(self, session_id: str, row: Dict[str, Any]) -> None:
        queues = list(self._stream_subscribers.get(session_id, set()))
        for queue in queues:
            await queue.put(row)

    async def _run_background_stream(self, session_id: str, producer_factory: Callable[[], AsyncIterator[str]]) -> None:
        try:
            async for raw_event in producer_factory():
                parsed = self._parse_sse_event(raw_event)
                if not parsed:
                    continue
                record = self._stream_event_to_record(parsed)
                if not record:
                    continue
                row = self.storage.append_ai_search_stream_event(record)
                if not isinstance(row, dict):
                    continue
                await self._broadcast_stream_event(session_id, row)
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
        for row in self.storage.list_ai_search_stream_events(session_id, after_seq=max(int(after_seq or 0), 0)):
            yield self._format_persisted_stream_event(row)

        queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()
        self._stream_subscribers.setdefault(session_id, set()).add(queue)
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
                yield self._format_persisted_stream_event(item)
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

    def _append_process_message(self, task_id: str, phase: str, payload: Dict[str, Any]) -> None:
        summary = str(payload.get("summary") or payload.get("statusText") or payload.get("label") or payload.get("toolLabel") or "").strip()
        if not summary:
            return
        process_type = str(payload.get("processType") or "").strip()
        display_metadata = build_process_display_metadata(
            process_type=process_type,
            event_id=str(payload.get("eventId") or "").strip(),
            subagent_name=str(payload.get("subagentName") or payload.get("name") or "").strip(),
            tool_name=str(payload.get("toolName") or "").strip(),
            label=str(payload.get("label") or payload.get("toolLabel") or "").strip(),
            summary=summary,
        )
        self.storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": task_id,
                "plan_version": self._current_active_plan_version(task_id) or None,
                "role": "assistant",
                "kind": "process",
                "content": summary,
                "stream_status": "completed",
                "metadata": {
                    **payload,
                    **display_metadata,
                    "phase": phase,
                },
            }
        )

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

    def _build_decision_continue_prompt(self, task_id: str, decision_action: Dict[str, Any]) -> str:
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
            "继续当前 AI 检索，但这不是新的用户需求。"
            "当前会话已进入人工决策态，请基于现有文献池、gap context 和决策摘要重新起草计划，"
            "然后请求用户确认，不要直接恢复旧执行步骤。\n\n"
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

    def _has_plan_confirmation_message(self, task_id: str, plan_version: int) -> bool:
        target_plan_version = int(plan_version or 0)
        for item in reversed(self.storage.list_ai_search_messages(task_id)):
            if str(item.get("role") or "").strip() != "assistant":
                continue
            if str(item.get("kind") or "").strip() != "plan_confirmation":
                continue
            if target_plan_version > 0 and int(item.get("plan_version") or 0) != target_plan_version:
                continue
            if str(item.get("content") or "").strip():
                return True
        return False

    def _reconcile_drafting_outcome(self, task_id: str) -> None:
        if self._current_phase_value(task_id) != PHASE_DRAFTING_PLAN:
            return

        context = AiSearchAgentContext(self.storage, task_id)
        pending = context.current_pending_action()
        if isinstance(pending, dict):
            action_type = str(pending.get("action_type") or "").strip()
            active_plan_version = int(pending.get("plan_version") or context.active_plan_version() or 0) or None
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

        active_plan_version = context.active_plan_version()
        if active_plan_version <= 0:
            return
        plan = self.storage.get_ai_search_plan(task_id, active_plan_version)
        if not isinstance(plan, dict):
            return
        plan_status = str(plan.get("status") or "").strip()
        if plan_status in {"confirmed", "superseded"}:
            return

        plan_summary = str(plan.get("review_markdown") or "").strip()
        if not plan_summary:
            return

        if not self._has_plan_confirmation_message(task_id, active_plan_version):
            self.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": task_id,
                    "plan_version": active_plan_version,
                    "role": "assistant",
                    "kind": "plan_confirmation",
                    "content": plan_summary,
                    "stream_status": "completed",
                    "metadata": {
                        "plan_version": active_plan_version,
                        "plan_summary": plan_summary,
                        "confirmation_label": "实施此计划",
                    },
                }
            )

        self.storage.update_ai_search_plan(task_id, active_plan_version, status="awaiting_confirmation")
        context.create_pending_action(
            "plan_confirmation",
            {
                "plan_version": active_plan_version,
                "plan_summary": plan_summary,
                "confirmation_label": "实施此计划",
            },
            run_id=context.active_run_id(active_plan_version),
            plan_version=active_plan_version,
            source="plan_gate",
        )
        context.update_task_phase(
            PHASE_AWAITING_PLAN_CONFIRMATION,
            active_plan_version=active_plan_version,
            run_id=context.active_run_id(active_plan_version),
        )

    def _recover_cancelled_drafting_run(self, task_id: str) -> None:
        if self._current_phase_value(task_id) != PHASE_DRAFTING_PLAN:
            return

        context = AiSearchAgentContext(self.storage, task_id)
        if context.active_plan_version() <= 0 and context.current_planner_draft():
            publish_planner_draft(context)
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
            "assistant_buffer": "",
            "assistant_completed": False,
            "assistant_message_id": "",
            "assistant_started": False,
            "emitted_phases": emitted_phases,
            "final_values": {},
            "known_message_ids": known_message_ids,
            "last_snapshot": snapshot,
            "previous_assistant": str(previous_assistant or "").strip(),
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

    def _normalize_stream_item(self, item: Any) -> tuple[Any, str, Any]:
        namespace: Any = ()
        mode = ""
        payload = item
        if isinstance(item, tuple):
            if len(item) == 3:
                namespace, mode, payload = item
            elif len(item) == 2:
                first, second = item
                if isinstance(first, str) and first in {"messages", "custom"}:
                    mode, payload = first, second
        elif isinstance(item, dict) and len(item) == 1:
            only_key = next(iter(item.keys()))
            if only_key in {"messages", "custom"}:
                mode = str(only_key)
                payload = item[only_key]
        return namespace, str(mode or ""), payload

    def _is_root_namespace(self, namespace: Any) -> bool:
        if namespace is None:
            return True
        if isinstance(namespace, str):
            return not namespace.strip()
        if isinstance(namespace, (tuple, list, set)):
            return len(namespace) == 0
        return False

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

    def _extract_message_delta(self, payload: Any) -> str:
        chunk = payload
        if isinstance(payload, (tuple, list)) and payload:
            chunk = payload[0]
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

    def _normalize_subagent_payload(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        label = str(payload.get("label") or "").strip() or format_subagent_label(name)
        default_status = f"{label}执行中。" if event_type == "subagent.started" else f"{label}已完成。"
        event_id = str(payload.get("eventId") or f"{name}:{'started' if event_type == 'subagent.started' else 'completed'}").strip()
        return {
            "name": name,
            "label": label,
            "eventId": event_id,
            "processType": "subagent",
            "status": "running" if event_type == "subagent.started" else "completed",
            "statusText": str(payload.get("statusText") or "").strip() or default_status,
            "summary": str(payload.get("summary") or "").strip() or label,
            "subagentName": str(payload.get("subagentName") or name).strip() or None,
            "subagentLabel": str(payload.get("subagentLabel") or label).strip() or None,
            **build_process_display_metadata(
                process_type="subagent",
                event_id=event_id,
                subagent_name=str(payload.get("subagentName") or name).strip(),
                label=label,
                summary=str(payload.get("summary") or "").strip() or label,
            ),
        }

    def _run_updated_payload(self, snapshot: AiSearchSnapshotResponse) -> Dict[str, Any]:
        return {
            "session": snapshot.session.model_dump(mode="python"),
            "run": snapshot.run if isinstance(snapshot.run, dict) else {},
            "plan": snapshot.plan.get("currentPlan") if isinstance(snapshot.plan, dict) else None,
            "artifacts": snapshot.artifacts.model_dump(mode="python"),
        }

    def _assistant_started_event(self, session_id: str, phase: str, stream_state: Dict[str, Any], message_id: Optional[str] = None) -> Optional[str]:
        if stream_state["assistant_started"]:
            return None
        resolved_message_id = str(message_id or stream_state["assistant_message_id"] or uuid.uuid4().hex).strip()
        stream_state["assistant_message_id"] = resolved_message_id
        stream_state["assistant_started"] = True
        return self._format_event(
            "assistant.message.started",
            session_id,
            phase,
            {"messageId": resolved_message_id, "contentType": "markdown"},
        )

    def _assistant_completed_events(
        self,
        session_id: str,
        phase: str,
        stream_state: Dict[str, Any],
        content: str,
        *,
        message_id: Optional[str] = None,
    ) -> List[str]:
        resolved_content = str(content or "")
        if not resolved_content.strip():
            return []
        resolved_message_id = str(message_id or stream_state["assistant_message_id"] or uuid.uuid4().hex).strip()
        events: List[str] = []
        started_event = self._assistant_started_event(session_id, phase, stream_state, resolved_message_id)
        if started_event:
            events.append(started_event)
        stream_state["assistant_buffer"] = resolved_content
        stream_state["assistant_completed"] = True
        stream_state["assistant_message_id"] = resolved_message_id
        events.append(
            self._format_event(
                "assistant.message.completed",
                session_id,
                phase,
                {
                    "messageId": resolved_message_id,
                    "content": resolved_content,
                    "contentType": "markdown",
                },
            )
        )
        return events

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
            if str(message.get("role") or "").strip() == "assistant" and str(message.get("kind") or "").strip() == "chat":
                for event in self._assistant_completed_events(
                    session_id,
                    phase,
                    stream_state,
                    str(message.get("content") or ""),
                    message_id=message_id or None,
                ):
                    yield event

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
        previous_phase: str = "",
        config: Optional[Dict[str, Any]] = None,
        forward_model_text: bool = True,
        emit_run_started: bool = True,
    ) -> AsyncIterator[str]:
        initial_phase = self.snapshots._snapshot_phase(initial_snapshot)
        if emit_run_started:
            yield self._format_event("run.started", session_id, initial_phase, {})

        iterator = agent.astream(
            payload,
            config,
            stream_mode=["messages", "custom"],
            subgraphs=True,
        )
        async for item in self._iterate_stream_with_keepalive(iterator):
            if item is None:
                yield ": keepalive\n\n"
                continue
            namespace, mode, raw_payload = self._normalize_stream_item(item)
            if mode == "custom":
                event_type, event_payload = self._normalize_custom_event(raw_payload)
                if not event_type:
                    continue
                if event_type == "snapshot.changed":
                    snapshot = self.snapshots.get_snapshot(session_id, owner_id)
                    async for event in self._emit_snapshot_diff_events(
                        stream_state["last_snapshot"],
                        snapshot,
                        stream_state=stream_state,
                    ):
                        yield event
                    stream_state["last_snapshot"] = snapshot
                    continue

                current_phase = self._current_phase_value(task_id, self.snapshots._snapshot_phase(stream_state["last_snapshot"]))
                if event_type in {"subagent.started", "subagent.completed"}:
                    normalized_payload = self._normalize_subagent_payload(event_type, event_payload)
                    yield self._format_event(
                        event_type,
                        session_id,
                        current_phase,
                        normalized_payload,
                    )
                elif event_type in {"tool.started", "tool.completed", "tool.failed"} and isinstance(event_payload, dict):
                    yield self._format_event(event_type, session_id, current_phase, dict(event_payload))

                snapshot = self.snapshots.get_snapshot(session_id, owner_id)
                async for event in self._emit_snapshot_diff_events(
                    stream_state["last_snapshot"],
                    snapshot,
                    stream_state=stream_state,
                ):
                    yield event
                stream_state["last_snapshot"] = snapshot
                continue

            if mode != "messages" or not self._is_root_namespace(namespace) or not forward_model_text:
                continue

            delta = self._extract_message_delta(raw_payload)
            if not delta:
                continue
            current_phase = self._current_phase_value(task_id, self.snapshots._snapshot_phase(stream_state["last_snapshot"]))
            started_event = self._assistant_started_event(session_id, current_phase, stream_state)
            if started_event:
                yield started_event
            stream_state["assistant_buffer"] = f"{stream_state['assistant_buffer']}{delta}"
            yield self._format_event(
                "assistant.message.delta",
                session_id,
                current_phase,
                {
                    "messageId": stream_state["assistant_message_id"],
                    "delta": delta,
                },
            )

    async def _emit_final_assistant_if_needed(
        self,
        task_id: str,
        stream_state: Dict[str, Any],
        *,
        allow_model_fallback: bool = True,
    ) -> AsyncIterator[str]:
        if stream_state["assistant_completed"]:
            return
        content = self._final_assistant_content(stream_state, allow_model_fallback=allow_model_fallback)
        if not content.strip():
            return
        phase = self._current_phase_value(task_id, self.snapshots._snapshot_phase(stream_state["last_snapshot"]))
        message_id = str(stream_state.get("assistant_message_id") or uuid.uuid4().hex).strip()
        self.facade._append_message(
            task_id,
            "assistant",
            "chat",
            content,
            message_id=message_id,
            plan_version=self._current_active_plan_version(task_id) or None,
        )
        for event in self._assistant_completed_events(
            task_id,
            phase,
            stream_state,
            content,
            message_id=message_id,
        ):
            yield event

    def _final_assistant_content(
        self,
        stream_state: Dict[str, Any],
        *,
        allow_model_fallback: bool = True,
    ) -> str:
        if stream_state["assistant_completed"]:
            return ""
        content = str(stream_state.get("assistant_buffer") or "")
        if allow_model_fallback and not content.strip() and stream_state.get("final_values"):
            fallback = extract_latest_ai_message(stream_state["final_values"])
            if fallback and fallback != stream_state.get("previous_assistant"):
                content = fallback
        return str(content or "")

    def _persist_final_assistant_if_needed(
        self,
        task_id: str,
        stream_state: Dict[str, Any],
        *,
        allow_model_fallback: bool = True,
    ) -> bool:
        content = self._final_assistant_content(stream_state, allow_model_fallback=allow_model_fallback)
        if not content.strip():
            return False
        message_id = str(stream_state.get("assistant_message_id") or uuid.uuid4().hex).strip()
        self.facade._append_message(
            task_id,
            "assistant",
            "chat",
            content,
            message_id=message_id,
            plan_version=self._current_active_plan_version(task_id) or None,
        )
        stream_state["assistant_buffer"] = content
        stream_state["assistant_message_id"] = message_id
        return True

    async def _stream_main_agent_execution(
        self,
        *,
        task: Any,
        owner_id: str,
        thread_id: str,
        payload: Any,
        previous_phase: str = "",
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
                async for event in self._consume_live_agent_stream(
                    session_id=task.id,
                    owner_id=owner_id,
                    task_id=task.id,
                    agent=agent,
                    payload=payload,
                    stream_state=stream_state,
                    initial_snapshot=initial_snapshot,
                    previous_phase=previous_phase,
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
            if persist_fallback_assistant:
                self._persist_final_assistant_if_needed(
                    task.id,
                    stream_state,
                    allow_model_fallback=True,
                )
            final_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
            self.snapshots._validate_drafting_outcome(task.id, final_snapshot)
            async for event in self._emit_snapshot_diff_events(
                stream_state["last_snapshot"],
                final_snapshot,
                stream_state=stream_state,
            ):
                yield event
            stream_state["last_snapshot"] = final_snapshot

            async for event in self._emit_final_assistant_if_needed(
                task.id,
                stream_state,
                allow_model_fallback=False,
            ):
                yield event

            final_phase = self._current_phase_value(task.id, self.snapshots._snapshot_phase(final_snapshot))
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
                previous_phase=self._current_phase_value(task.id, self.snapshots._snapshot_phase(handoff_snapshot)),
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
            for event in self._fail_open_stage_events(
                task.id,
                self._current_phase_value(task.id, self.snapshots._snapshot_phase(initial_snapshot)),
                self._stream_error_payload(exc).get("message", "当前流式轮次执行失败。"),
            ):
                yield event
            yield self._format_event(
                "run.error",
                task.id,
                self._current_phase_value(task.id, self.snapshots._snapshot_phase(initial_snapshot)),
                self._stream_error_payload(exc),
            )

    async def _stream_feature_agent_execution(
        self,
        *,
        task: Any,
        owner_id: str,
        plan_version: int,
        previous_phase: str = "",
        force_complete: bool = False,
        termination_reason: str = "",
        force_human_decision: bool = False,
        human_decision_reason: str = "",
        human_decision_summary: str = "",
        emit_run_started: bool = True,
        emit_run_completed: bool = True,
    ) -> AsyncIterator[str]:
        previous_assistant = self.snapshots._latest_assistant_chat(task.id)
        initial_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
        stream_state = self._init_stream_state(initial_snapshot, previous_assistant)
        agent = self.facade._build_feature_comparer_agent(self.storage, task.id)
        prompt = {
            "messages": [
                {
                    "role": "user",
                    "content": "请基于当前活动计划和已选对比文件完成特征对比分析，并使用工具加载上下文后持久化结果。",
                }
            ]
        }

        try:
            if hasattr(agent, "astream") and callable(getattr(agent, "astream")):
                async for event in self._consume_live_agent_stream(
                    session_id=task.id,
                    owner_id=owner_id,
                    task_id=task.id,
                    agent=agent,
                    payload=prompt,
                    stream_state=stream_state,
                    initial_snapshot=initial_snapshot,
                    previous_phase=previous_phase,
                    emit_run_started=emit_run_started,
                ):
                    yield event
            else:
                initial_phase = self.snapshots._snapshot_phase(initial_snapshot)
                if emit_run_started:
                    yield self._format_event("run.started", task.id, initial_phase, {})
                current_phase = self._current_phase_value(task.id, initial_phase)
                yield self._format_event(
                    "subagent.started",
                    task.id,
                    current_phase,
                    self._normalize_subagent_payload("subagent.started", {"name": "feature-comparer"}),
                )
                await asyncio.to_thread(agent.invoke, prompt)
                yield self._format_event(
                    "subagent.completed",
                    task.id,
                    self._current_phase_value(task.id, current_phase),
                    self._normalize_subagent_payload("subagent.completed", {"name": "feature-comparer"}),
                )

            refreshed_task = self.storage.get_task(task.id)
            latest_feature = self.artifacts._current_feature_comparison(refreshed_task, plan_version, fallback_latest=True) or {}
            feature_comparison_id = str(latest_feature.get("feature_comparison_id") or latest_feature.get("result_id") or "").strip()
            context = AiSearchAgentContext(self.storage, task.id)
            progress = context.evaluate_gap_progress_payload(plan_version)
            round_evaluation = commit_round_evaluation(context, plan_version)
            final_phase = PHASE_FEATURE_COMPARISON
            if force_complete:
                final_phase = PHASE_COMPLETED
            elif force_human_decision:
                final_phase = PHASE_AWAITING_HUMAN_DECISION
            elif bool(round_evaluation.get("should_request_decision")):
                final_phase = PHASE_AWAITING_HUMAN_DECISION
            elif str(progress.get("recommended_action") or "").strip() == "complete_execution":
                final_phase = PHASE_COMPLETED
            selected_count = len(self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"]))
            if final_phase == PHASE_AWAITING_HUMAN_DECISION:
                findings = str(latest_feature.get("overall_findings") or "").strip()
                summary = (
                    str(human_decision_summary or "").strip()
                    or findings
                    or str(round_evaluation.get("decision_summary") or "").strip()
                    or "自动检索已停止，需要人工决策。"
                )
                enter_human_decision(
                    context,
                    reason=(
                        str(human_decision_reason or "").strip()
                        or str(round_evaluation.get("decision_reason") or "no_progress_limit_reached").strip()
                    ),
                    summary=summary,
                )
                self.facade._append_message(
                    task.id,
                    "assistant",
                    "chat",
                    summary,
                    plan_version=plan_version or None,
                    metadata={"reason": round_evaluation.get("decision_reason"), "kind": "human_decision"},
                )
                self.facade._update_phase(
                    task.id,
                    final_phase,
                    selected_document_count=selected_count,
                )
            else:
                self.facade._update_phase(
                    task.id,
                    final_phase,
                    selected_document_count=selected_count,
                )
            if final_phase == PHASE_COMPLETED:
                self.artifacts._finalize_terminal_artifacts(task.id, plan_version, termination_reason=termination_reason)
                await asyncio.to_thread(self.facade.notify_task_terminal_status, task.id, PHASE_COMPLETED)

            final_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
            async for event in self._emit_snapshot_diff_events(
                stream_state["last_snapshot"],
                final_snapshot,
                stream_state=stream_state,
            ):
                yield event
            stream_state["last_snapshot"] = final_snapshot

            async for event in self._emit_final_assistant_if_needed(task.id, stream_state):
                yield event

            if emit_run_completed:
                yield self._format_event(
                    "run.completed",
                    task.id,
                    self._current_phase_value(task.id, self.snapshots._snapshot_phase(final_snapshot)),
                    self._completion_payload_for_phase(
                        final_phase,
                        {
                            "featureComparisonId": feature_comparison_id or None,
                            "recommendedAction": progress.get("recommended_action"),
                            "humanDecision": final_phase == PHASE_AWAITING_HUMAN_DECISION,
                        },
                    ),
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
            meta = get_ai_search_meta(self.storage.get_task(task.id))
            thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
            async for event in self._stream_main_agent_execution(
                task=self.storage.get_task(task.id),
                owner_id=owner_id,
                thread_id=thread_id,
                payload={"messages": [{"role": "user", "content": exc.takeover_prompt}]},
                previous_phase=self._current_phase_value(task.id, self.snapshots._snapshot_phase(handoff_snapshot)),
                persist_fallback_assistant=True,
            ):
                yield event
        except Exception as exc:
            for event in self._fail_open_stage_events(
                task.id,
                self._current_phase_value(task.id, self.snapshots._snapshot_phase(initial_snapshot)),
                self._stream_error_payload(exc).get("message", "当前流式轮次执行失败。"),
            ):
                yield event
            yield self._format_event(
                "run.error",
                task.id,
                self._current_phase_value(task.id, self.snapshots._snapshot_phase(initial_snapshot)),
                self._stream_error_payload(exc),
            )

    async def _stream_close_reader_agent_execution(
        self,
        *,
        task: Any,
        owner_id: str,
        previous_phase: str = "",
        emit_run_started: bool = True,
        emit_run_completed: bool = True,
    ) -> AsyncIterator[str]:
        previous_assistant = self.snapshots._latest_assistant_chat(task.id)
        initial_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
        stream_state = self._init_stream_state(initial_snapshot, previous_assistant)
        agent = self.facade._build_close_reader_agent(self.storage, task.id)
        prompt = {
            "messages": [
                {
                    "role": "user",
                    "content": "请仅对当前活动 close_read batch 中的文献完成人工送审复核，并使用工具加载上下文后提交结构化精读结果。",
                }
            ]
        }

        try:
            if hasattr(agent, "astream") and callable(getattr(agent, "astream")):
                async for event in self._consume_live_agent_stream(
                    session_id=task.id,
                    owner_id=owner_id,
                    task_id=task.id,
                    agent=agent,
                    payload=prompt,
                    stream_state=stream_state,
                    initial_snapshot=initial_snapshot,
                    previous_phase=previous_phase,
                    emit_run_started=emit_run_started,
                ):
                    yield event
            else:
                initial_phase = self.snapshots._snapshot_phase(initial_snapshot)
                if emit_run_started:
                    yield self._format_event("run.started", task.id, initial_phase, {})
                current_phase = self._current_phase_value(task.id, initial_phase)
                yield self._format_event(
                    "subagent.started",
                    task.id,
                    current_phase,
                    self._normalize_subagent_payload("subagent.started", {"name": "close-reader"}),
                )
                await asyncio.to_thread(agent.invoke, prompt)
                yield self._format_event(
                    "subagent.completed",
                    task.id,
                    self._current_phase_value(task.id, current_phase),
                    self._normalize_subagent_payload("subagent.completed", {"name": "close-reader"}),
                )

            final_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
            async for event in self._emit_snapshot_diff_events(
                stream_state["last_snapshot"],
                final_snapshot,
                stream_state=stream_state,
            ):
                yield event
            stream_state["last_snapshot"] = final_snapshot
            if emit_run_completed:
                yield self._format_event(
                    "run.completed",
                    task.id,
                    self._current_phase_value(task.id, self.snapshots._snapshot_phase(final_snapshot)),
                    self._completion_payload_for_phase(
                        self._current_phase_value(task.id, self.snapshots._snapshot_phase(final_snapshot)),
                        {"closeRead": True},
                    ),
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
            meta = get_ai_search_meta(self.storage.get_task(task.id))
            thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
            async for event in self._stream_main_agent_execution(
                task=self.storage.get_task(task.id),
                owner_id=owner_id,
                thread_id=thread_id,
                payload={"messages": [{"role": "user", "content": exc.takeover_prompt}]},
                previous_phase=self._current_phase_value(task.id, self.snapshots._snapshot_phase(handoff_snapshot)),
                persist_fallback_assistant=True,
            ):
                yield event
        except Exception as exc:
            for event in self._fail_open_stage_events(
                task.id,
                self._current_phase_value(task.id, self.snapshots._snapshot_phase(initial_snapshot)),
                self._stream_error_payload(exc).get("message", "当前流式轮次执行失败。"),
            ):
                yield event
            yield self._format_event(
                "run.error",
                task.id,
                self._current_phase_value(task.id, self.snapshots._snapshot_phase(initial_snapshot)),
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

        if phase == PHASE_AWAITING_PLAN_CONFIRMATION and meta.get("active_plan_version"):
            active_plan_version = int(meta["active_plan_version"])
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
            previous_phase=phase,
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
            previous_phase=phase,
            for_resume=True,
            persist_fallback_assistant=True,
        ):
            yield event

    async def _stream_plan_confirmation_events(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
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
        pending_payload = pending_action.get("payload") if isinstance(pending_action, dict) else {}
        pending_plan_version = int(pending_payload.get("plan_version") or 0)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if pending_plan_version != plan_version or active_plan_version != plan_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": STALE_PLAN_CONFIRMATION_CODE,
                    "message": "当前计划版本已经失效了。",
                    "suggestion": "你可以刷新后再试，或者直接告诉我新的修改意见。",
                },
            )
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")

        def _post_run(_: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            updated_plan = self.storage.get_ai_search_plan(task.id, plan_version)
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
            payload=Command(resume={"confirmed": True, "plan_version": plan_version}),
            previous_phase=phase,
            for_resume=True,
            post_run=_post_run,
        ):
            yield event

    async def _stream_analysis_seed_events(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        if str(meta.get("source_type") or "").strip() != "analysis":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": ANALYSIS_SEED_REQUIRED_CODE,
                    "message": "当前会话不是从 AI 分析生成的。",
                    "suggestion": "你可以回到 AI 分析结果页重新发起检索。",
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
                    "message": "当前会话缺少 AI 分析上下文。",
                    "suggestion": "你可以重新从 AI 分析结果页发起检索。",
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
                previous_phase=phase,
            ):
                if event.startswith("data: "):
                    try:
                        payload = json.loads(event[6:])
                    except Exception:
                        payload = {}
                    event_type = str(payload.get("type") or "").strip()
                    if event_type == "run.completed":
                        saw_run_completed = True
                    if event_type == "run.error":
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
        decision_action = self._require_human_decision_action(task)
        meta = get_ai_search_meta(task)
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
        context = AiSearchAgentContext(self.storage, task.id)
        context.reset_execution_control(plan_version, clear_human_decision=True)
        self._resolve_pending_action(
            task.id,
            expected_type="human_decision",
            resolution={"decision": "continue_search"},
        )
        self.facade._update_phase(task.id, PHASE_DRAFTING_PLAN)
        prompt = self._build_decision_continue_prompt(task.id, decision_action)
        async for event in self._stream_main_agent_execution(
            task=self.storage.get_task(task.id),
            owner_id=owner_id,
            thread_id=thread_id,
            payload={"messages": [{"role": "user", "content": prompt}]},
            previous_phase=PHASE_AWAITING_HUMAN_DECISION,
            persist_fallback_assistant=True,
        ):
            yield event

    async def _stream_decision_complete_events(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        self._require_human_decision_action(task)
        meta = get_ai_search_meta(task)
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
        feature_comparison = self.artifacts._current_feature_comparison(task, plan_version)
        if feature_comparison is None:
            self.facade._update_phase(
                task.id,
                PHASE_FEATURE_COMPARISON,
                active_plan_version=plan_version,
            )
            async for event in self._stream_feature_agent_execution(
                task=self.storage.get_task(task.id),
                owner_id=owner_id,
                plan_version=plan_version,
                previous_phase=PHASE_AWAITING_HUMAN_DECISION,
                force_complete=True,
                termination_reason=termination_reason,
            ):
                yield event
            return

        previous_assistant = self.snapshots._latest_assistant_chat(task.id)
        initial_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
        stream_state = self._init_stream_state(initial_snapshot, previous_assistant)
        yield self._format_event("run.started", task.id, self.snapshots._snapshot_phase(initial_snapshot), {})
        self._resolve_pending_action(
            task.id,
            expected_type="human_decision",
            resolution={"decision": "complete_current_results"},
        )
        self.facade._update_phase(
            task.id,
            PHASE_COMPLETED,
            active_plan_version=plan_version,
            selected_document_count=len(selected_documents),
        )
        self.artifacts._finalize_terminal_artifacts(task.id, plan_version, termination_reason=termination_reason)
        await asyncio.to_thread(self.facade.notify_task_terminal_status, task.id, PHASE_COMPLETED)
        final_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
        async for event in self._emit_snapshot_diff_events(
            stream_state["last_snapshot"],
            final_snapshot,
            stream_state=stream_state,
        ):
            yield event
        yield self._format_event(
            "run.completed",
            task.id,
            self._current_phase_value(task.id, self.snapshots._snapshot_phase(final_snapshot)),
            self._completion_payload_for_phase(
                self._current_phase_value(task.id, self.snapshots._snapshot_phase(final_snapshot)),
                {"completedFromTakeover": True},
            ),
        )

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

        if review_ids:
            batch_id = self._create_manual_close_read_batch(task.id, plan_version, review_ids)
            self.facade._update_phase(
                task.id,
                PHASE_CLOSE_READ,
                active_plan_version=plan_version,
                active_batch_id=batch_id,
                selected_document_count=selected_count,
            )
            refreshed_task = self.storage.get_task(task.id)
            async for event in self._stream_close_reader_agent_execution(
                task=refreshed_task,
                owner_id=owner_id,
                previous_phase=PHASE_AWAITING_HUMAN_DECISION,
                emit_run_started=False,
                emit_run_completed=False,
            ):
                yield event

        selected_documents = self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"])
        if selected_documents:
            self.facade._update_phase(
                task.id,
                PHASE_FEATURE_COMPARISON,
                active_plan_version=plan_version,
            )
            refreshed_task = self.storage.get_task(task.id)
            async for event in self._stream_feature_agent_execution(
                task=refreshed_task,
                owner_id=owner_id,
                plan_version=plan_version,
                previous_phase=self._current_phase_value(task.id),
                force_human_decision=True,
                human_decision_reason="manual_document_review",
                human_decision_summary="人工文献复核已完成，请决定继续检索或按当前结果完成。",
                emit_run_started=False,
                emit_run_completed=False,
            ):
                yield event
        else:
            summary = "当前无已选对比文献，请送审候选文献或继续检索。"
            enter_human_decision(
                context,
                reason="manual_document_review",
                summary=summary,
            )
            self.facade._append_message(
                task.id,
                "assistant",
                "chat",
                summary,
                plan_version=plan_version or None,
                metadata={"reason": "manual_document_review", "kind": "human_decision"},
            )
            final_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
            async for event in self._emit_snapshot_diff_events(
                stream_state["last_snapshot"],
                final_snapshot,
                stream_state=stream_state,
            ):
                yield event
            stream_state["last_snapshot"] = final_snapshot

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
        previous_phase = str(meta.get("current_phase") or "")
        self.facade._update_phase(
            task.id,
            PHASE_FEATURE_COMPARISON,
            active_plan_version=plan_version,
        )
        async for event in self._stream_feature_agent_execution(
            task=self.storage.get_task(task.id),
            owner_id=owner_id,
            plan_version=plan_version,
            previous_phase=previous_phase,
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

    async def stream_message(self, session_id: str, owner_id: str, content: str) -> AsyncIterator[str]:
        async for event in self._start_and_subscribe(
            session_id,
            owner_id,
            lambda: self._stream_message_events(session_id, owner_id, content),
        ):
            yield event

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

    async def stream_plan_confirmation(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        async for event in self._start_and_subscribe(
            session_id,
            owner_id,
            lambda: self._stream_plan_confirmation_events(session_id, owner_id, plan_version),
        ):
            yield event

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
