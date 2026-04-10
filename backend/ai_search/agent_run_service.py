"""Agent invocation and streaming collaborator for AI Search."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from fastapi import HTTPException
from langgraph.types import Command

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.orchestration.action_runtime import (
    build_pending_action_view,
    current_pending_action,
    resolve_pending_action,
)
from agents.ai_search.src.orchestration.execution_runtime import commit_round_evaluation, enter_human_decision
from agents.ai_search.src.runtime import extract_latest_ai_message, format_subagent_label
from agents.ai_search.src.state import (
    ACTIVE_EXECUTION_PHASES,
    PHASE_AWAITING_HUMAN_DECISION,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_CLOSE_READ,
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_COMPLETED,
    PHASE_DRAFTING_PLAN,
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
    PENDING_QUESTION_EXISTS_CODE,
    PLAN_CONFIRMATION_REQUIRED_CODE,
    RESUME_NOT_AVAILABLE_CODE,
    SEARCH_IN_PROGRESS_CODE,
    STALE_PLAN_CONFIRMATION_CODE,
    AiSearchSnapshotResponse,
)

class AiSearchAgentRunService:
    def __init__(self, facade: Any) -> None:
        self.facade = facade
        self.storage = facade.storage
        self.sessions = facade.sessions
        self.snapshots = facade.snapshots
        self.artifacts = facade.artifacts
        self.analysis_seeds = facade.analysis_seeds

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
        interrupted = False
        for chunk in agent.stream(payload, config):
            if "__interrupt__" in chunk:
                interrupted = True
        state = agent.get_state(self._main_agent_state_config(agent, thread_id))
        values = state.values if state else {}
        return {"interrupted": interrupted, "values": values}

    def _format_event(self, event_type: str, session_id: str, phase: str, payload: Any) -> str:
        message = {
            "type": event_type,
            "sessionId": session_id,
            "taskId": session_id,
            "phase": phase,
            "payload": payload,
        }
        return f"data: {json.dumps(message, ensure_ascii=False)}\n\n"

    def _append_process_message(self, task_id: str, phase: str, payload: Dict[str, Any]) -> None:
        summary = str(payload.get("summary") or payload.get("statusText") or payload.get("label") or payload.get("toolLabel") or "").strip()
        if not summary:
            return
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
                    "phase": phase,
                },
            }
        )

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
                detail={"code": RESUME_NOT_AVAILABLE_CODE, "message": "当前没有可恢复的失败执行步骤。"},
            )
        return resume_action

    def _require_human_decision_action(self, task: Any) -> Dict[str, Any]:
        pending_action = self.snapshots._pending_action(task, "human_decision")
        if pending_action is None:
            raise HTTPException(status_code=409, detail="当前不在人工决策状态。")
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
            "interrupted": False,
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
        return {
            "name": name,
            "label": label,
            "statusText": str(payload.get("statusText") or "").strip() or default_status,
        }

    def _run_updated_payload(self, snapshot: AiSearchSnapshotResponse) -> Dict[str, Any]:
        return {
            "session": snapshot.session.model_dump(mode="python"),
            "run": snapshot.run if isinstance(snapshot.run, dict) else {},
            "plan": snapshot.plan.get("currentPlan") if isinstance(snapshot.plan, dict) else None,
            "artifacts": snapshot.artifacts if isinstance(snapshot.artifacts, dict) else {},
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
    ) -> AsyncIterator[str]:
        initial_phase = self.snapshots._snapshot_phase(initial_snapshot)
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
                    self._append_process_message(task_id, current_phase, normalized_payload)
                    yield self._format_event(
                        event_type,
                        session_id,
                        current_phase,
                        normalized_payload,
                    )
                elif event_type in {"tool.started", "tool.completed", "tool.failed"} and isinstance(event_payload, dict):
                    self._append_process_message(task_id, current_phase, event_payload)
                    yield self._format_event(event_type, session_id, current_phase, event_payload)

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
        content = str(stream_state.get("assistant_buffer") or "")
        if allow_model_fallback and not content.strip() and stream_state.get("final_values"):
            fallback = extract_latest_ai_message(stream_state["final_values"])
            if fallback and fallback != stream_state.get("previous_assistant"):
                content = fallback
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

    async def _stream_main_agent_execution(
        self,
        *,
        task: Any,
        owner_id: str,
        thread_id: str,
        payload: Any,
        previous_phase: str = "",
        for_resume: bool = False,
        post_run: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
    ) -> AsyncIterator[str]:
        previous_assistant = self.snapshots._latest_assistant_chat(task.id)
        initial_snapshot = self.snapshots.get_snapshot(task.id, owner_id)
        stream_state = self._init_stream_state(initial_snapshot, previous_assistant)

        try:
            agent = self.facade._build_main_agent(self.storage, task.id) if self.facade._uses_default_run_main_agent() else None
            if hasattr(agent, "astream") and callable(getattr(agent, "astream")):
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
                    forward_model_text=False,
                ):
                    yield event
                state = None
                if hasattr(agent, "get_state") and hasattr(agent, "checkpointer"):
                    state = agent.get_state(self._main_agent_state_config(agent, thread_id))
                stream_state["final_values"] = state.values if state is not None else {}
                stream_state["interrupted"] = bool(getattr(state, "interrupts", None)) if state is not None else False
            else:
                initial_phase = self.snapshots._snapshot_phase(initial_snapshot)
                yield self._format_event("run.started", task.id, initial_phase, {})
                result = await asyncio.to_thread(
                    self.facade._run_main_agent,
                    task.id,
                    thread_id,
                    payload,
                    for_resume=for_resume,
                )
                stream_state["final_values"] = result["values"]
                stream_state["interrupted"] = bool(result["interrupted"])

            completion_payload = post_run(stream_state) if post_run else {}
            if not isinstance(completion_payload, dict):
                completion_payload = {}

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

            yield self._format_event(
                "run.completed",
                task.id,
                self._current_phase_value(task.id, self.snapshots._snapshot_phase(final_snapshot)),
                {"interrupted": stream_state["interrupted"], **completion_payload},
            )
        except Exception as exc:
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
                ):
                    yield event
            else:
                initial_phase = self.snapshots._snapshot_phase(initial_snapshot)
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
            elif bool(round_evaluation.get("should_request_decision")):
                final_phase = PHASE_AWAITING_HUMAN_DECISION
            elif str(progress.get("recommended_action") or "").strip() == "complete_execution":
                final_phase = PHASE_COMPLETED
            selected_count = len(self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"]))
            if final_phase == PHASE_AWAITING_HUMAN_DECISION:
                summary = str(round_evaluation.get("decision_summary") or "").strip() or "自动检索已停止，需要人工决策。"
                enter_human_decision(
                    context,
                    reason=str(round_evaluation.get("decision_reason") or "no_progress_limit_reached").strip(),
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

            yield self._format_event(
                "run.completed",
                task.id,
                self._current_phase_value(task.id, self.snapshots._snapshot_phase(final_snapshot)),
                {
                    "interrupted": False,
                    "featureComparisonId": feature_comparison_id or None,
                    "recommendedAction": progress.get("recommended_action"),
                    "humanDecision": final_phase == PHASE_AWAITING_HUMAN_DECISION,
                },
            )
        except Exception as exc:
            yield self._format_event(
                "run.error",
                task.id,
                self._current_phase_value(task.id, self.snapshots._snapshot_phase(initial_snapshot)),
                self._stream_error_payload(exc),
            )

    async def stream_message(self, session_id: str, owner_id: str, content: str) -> AsyncIterator[str]:
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
                detail={"code": PENDING_QUESTION_EXISTS_CODE, "message": "请先回答当前追问。"},
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
        ):
            yield event

    async def stream_resume(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
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
                    detail={"code": RESUME_NOT_AVAILABLE_CODE, "message": "恢复点已失效，请刷新后重试。"},
                )
            if checkpoint_id and current_checkpoint_id and checkpoint_id != current_checkpoint_id:
                raise HTTPException(
                    status_code=409,
                    detail={"code": RESUME_NOT_AVAILABLE_CODE, "message": "恢复点已失效，请刷新后重试。"},
                )
        self._resolve_pending_action(task.id, expected_type="resume", resolution={"decision": "resume"})
        async for event in self._stream_main_agent_execution(
            task=task,
            owner_id=owner_id,
            thread_id=thread_id,
            payload={"messages": [{"role": "user", "content": self._build_resume_prompt(resume_action)}]},
        ):
            yield event

    async def stream_answer(self, session_id: str, owner_id: str, question_id: str, answer: str) -> AsyncIterator[str]:
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
                detail={"code": PENDING_QUESTION_EXISTS_CODE, "message": "回答的问题已过期。"},
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
        ):
            yield event

    async def stream_plan_confirmation(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or "")
        if phase != PHASE_AWAITING_PLAN_CONFIRMATION:
            raise HTTPException(
                status_code=409,
                detail={"code": PLAN_CONFIRMATION_REQUIRED_CODE, "message": "当前没有待确认的检索计划。"},
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
                detail={"code": STALE_PLAN_CONFIRMATION_CODE, "message": "当前计划版本已失效，请刷新后重试。"},
            )
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")

        def _post_run(_: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            updated_plan = self.storage.get_ai_search_plan(task.id, plan_version)
            if not updated_plan or str(updated_plan.get("status") or "") != "confirmed":
                raise HTTPException(
                    status_code=409,
                    detail={"code": PLAN_CONFIRMATION_REQUIRED_CODE, "message": "计划确认未生效，请重试。"},
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

    async def stream_analysis_seed(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        if str(meta.get("source_type") or "").strip() != "analysis":
            raise HTTPException(status_code=409, detail="当前会话不是从 AI 分析创建的检索计划。")
        if str(meta.get("analysis_seed_status") or "").strip() != "pending":
            raise HTTPException(status_code=409, detail="当前检索计划已生成，不能重复初始化。")
        phase = str(meta.get("current_phase") or PHASE_DRAFTING_PLAN)
        seed_prompt = str(meta.get("analysis_seed_prompt") or "").strip()
        if not seed_prompt:
            raise HTTPException(status_code=409, detail="当前会话缺少 AI 分析种子上下文。")

        run_error: Optional[Dict[str, Any]] = None
        saw_run_completed = False
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
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
                {
                    "interrupted": final_phase in {PHASE_AWAITING_PLAN_CONFIRMATION, PHASE_AWAITING_USER_ANSWER},
                    "analysisSeed": True,
                },
            )

    async def stream_decision_continue(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        decision_action = self._require_human_decision_action(task)
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 0)
        if plan_version <= 0:
            raise HTTPException(status_code=409, detail="当前没有活动计划版本，无法继续检索。")
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
        ):
            yield event

    async def stream_decision_complete(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        self._require_human_decision_action(task)
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 0)
        if plan_version <= 0:
            raise HTTPException(status_code=409, detail="当前没有活动计划版本，无法按当前结果完成。")
        selected_documents = self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"])
        if not selected_documents:
            raise HTTPException(status_code=409, detail="当前没有已选对比文献，无法按当前结果完成。")

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
            {"interrupted": False, "completedFromTakeover": True},
        )

    def patch_selected_documents(
        self,
        session_id: str,
        owner_id: str,
        plan_version: int,
        add_document_ids: Optional[List[str]],
        remove_document_ids: Optional[List[str]],
    ) -> AiSearchSnapshotResponse:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if active_plan_version != int(plan_version):
            raise HTTPException(
                status_code=409,
                detail={"code": STALE_PLAN_CONFIRMATION_CODE, "message": "当前只允许操作活动计划版本。"},
            )
        phase = str(meta.get("current_phase") or "")
        if phase not in {PHASE_CLOSE_READ, PHASE_FEATURE_COMPARISON, PHASE_AWAITING_HUMAN_DECISION, PHASE_COMPLETED}:
            self.sessions._raise_invalid_phase(phase, "当前阶段不允许调整对比文件。")
        add_ids = [str(item).strip() for item in (add_document_ids or []) if str(item).strip()]
        remove_ids = [str(item).strip() for item in (remove_document_ids or []) if str(item).strip()]
        for document_id in add_ids:
            self.storage.update_ai_search_document(
                task.id,
                plan_version,
                document_id,
                stage="selected",
                user_pinned=True,
                user_removed=False,
                close_read_status="selected",
                close_read_reason="用户手动加入对比文件",
                agent_reason="用户手动加入对比文件",
            )
        for document_id in remove_ids:
            self.storage.update_ai_search_document(
                task.id,
                plan_version,
                document_id,
                stage="rejected",
                user_pinned=False,
                user_removed=True,
                close_read_status="rejected",
                close_read_reason="用户手动移出对比文件",
                agent_reason="用户手动移出对比文件",
            )
        selected_count = len(self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"]))
        next_phase = PHASE_FEATURE_COMPARISON if selected_count > 0 else PHASE_CLOSE_READ
        context = AiSearchAgentContext(self.storage, task.id)
        context.reset_execution_control(plan_version, clear_human_decision=True)
        self._resolve_pending_action(
            task.id,
            expected_type="human_decision",
            resolution={"decision": "continue_search"},
        )
        self.facade._update_phase(
            task.id,
            next_phase,
            selected_document_count=selected_count,
            active_batch_id=None,
        )
        return self.snapshots.get_snapshot(task.id, owner_id)

    async def stream_feature_comparison(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
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
