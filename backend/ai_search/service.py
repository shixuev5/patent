"""
AI search session service.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from fastapi import HTTPException
from langgraph.types import Command

from agents.ai_search.main import (
    build_feature_comparer_agent,
    build_main_agent,
    extract_latest_ai_message,
)
from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.orchestration.action_runtime import build_pending_action_view, current_pending_action
from agents.ai_search.src.runtime import format_subagent_label
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
    default_ai_search_meta,
    get_ai_search_meta,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from backend.system_logs import emit_system_log
from backend.storage import TaskType, get_pipeline_manager
from backend.time_utils import utc_now_z
from backend.usage import _enforce_daily_quota
from backend.utils import _build_r2_storage

from .agent_run_service import AiSearchAgentRunService
from .analysis_seed_service import AiSearchAnalysisSeedService
from .artifacts_service import AiSearchArtifactsService
from .reporting import build_ai_search_terminal_artifacts
from .analysis_seed import (
    build_analysis_seed_user_message,
    build_execution_spec_from_analysis,
    load_json_bytes,
    load_json_file,
    seed_prompt_from_analysis,
    seed_search_elements_from_analysis,
)
from .models import (
    AI_SEARCH_SESSION_NOT_FOUND_CODE,
    INVALID_SESSION_PHASE_CODE,
    PENDING_QUESTION_EXISTS_CODE,
    PLAN_CONFIRMATION_REQUIRED_CODE,
    RESUME_NOT_AVAILABLE_CODE,
    SEARCH_IN_PROGRESS_CODE,
    STALE_PLAN_CONFIRMATION_CODE,
    AiSearchCreateSessionResponse,
    AiSearchSessionListResponse,
    AiSearchSessionSummary,
    AiSearchSnapshotResponse,
)
from .session_service import AiSearchSessionService
from .snapshot_service import AiSearchSnapshotService


task_manager = get_pipeline_manager()

MAIN_AGENT_CHECKPOINT_NS = "ai_search_main"
MAIN_AGENT_PROGRESS_POLL_SECONDS = 15.0
DEFAULT_MESSAGE_PHASES = {
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_DRAFTING_PLAN,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_COMPLETED,
}


class AiSearchService:
    def __init__(self):
        self.storage = task_manager.storage
        self.task_manager = task_manager
        self.MAIN_AGENT_CHECKPOINT_NS = MAIN_AGENT_CHECKPOINT_NS
        self.MAIN_AGENT_PROGRESS_POLL_SECONDS = MAIN_AGENT_PROGRESS_POLL_SECONDS
        self.DEFAULT_MESSAGE_PHASES = DEFAULT_MESSAGE_PHASES
        self.sessions = AiSearchSessionService(self)
        self.snapshots = AiSearchSnapshotService(self)
        self.artifacts = AiSearchArtifactsService(self)
        self.analysis_seeds = AiSearchAnalysisSeedService(self)
        self.agent_runs = AiSearchAgentRunService(self)

    def _uses_default_run_main_agent(self) -> bool:
        runner = getattr(self, "_run_main_agent", None)
        return getattr(runner, "__func__", None) is AiSearchService._run_main_agent

    def _emit_system_log(self, **kwargs: Any) -> None:
        emit_system_log(**kwargs)

    def _enforce_daily_quota(self, owner_id: str, *, task_type: Optional[str] = None) -> None:
        _enforce_daily_quota(owner_id, task_type=task_type)

    def _build_main_agent(self, storage: Any, task_id: str) -> Any:
        return build_main_agent(storage, task_id)

    def _build_feature_comparer_agent(self, storage: Any, task_id: str) -> Any:
        return build_feature_comparer_agent(storage, task_id)

    def _extract_latest_ai_message(self, values: Any) -> str:
        return extract_latest_ai_message(values)

    def _build_terminal_artifacts(self, **kwargs: Any) -> Dict[str, Any]:
        return build_ai_search_terminal_artifacts(**kwargs)

    def _main_agent_progress_poll_seconds(self) -> float:
        return MAIN_AGENT_PROGRESS_POLL_SECONDS

    def _raise_session_not_found(self) -> None:
        self.sessions._raise_session_not_found()

    def _raise_invalid_phase(self, phase: str, message: str) -> None:
        self.sessions._raise_invalid_phase(phase, message)

    def _get_owned_session_task(self, session_id: str, owner_id: str) -> Any:
        return self.sessions._get_owned_session_task(session_id, owner_id)

    def _session_summary(self, task: Any) -> AiSearchSessionSummary:
        return self.snapshots._session_summary(task)

    def _display_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self.snapshots._display_messages(messages)

    def _snapshot_phase(self, snapshot: AiSearchSnapshotResponse) -> str:
        return self.snapshots._snapshot_phase(snapshot)

    def _snapshot_messages(self, snapshot: AiSearchSnapshotResponse) -> List[Dict[str, Any]]:
        return self.snapshots._snapshot_messages(snapshot)

    def _artifact_download_url(self, snapshot: AiSearchSnapshotResponse) -> Optional[str]:
        return self.snapshots._artifact_download_url(snapshot)

    def _active_run(self, task: Any) -> Optional[Dict[str, Any]]:
        return self.snapshots._active_run(task)

    def _current_plan(self, task: Any) -> Optional[Dict[str, Any]]:
        return self.snapshots._current_plan(task)

    def _pending_action(self, task: Any, action_type: str) -> Optional[Dict[str, Any]]:
        return self.snapshots._pending_action(task, action_type)

    def _analysis_seed(self, task: Any) -> Optional[Dict[str, Any]]:
        return self.snapshots._analysis_seed(task)

    def _has_planner_draft(self, task: Any) -> bool:
        return self.snapshots._has_planner_draft(task)

    def _validate_drafting_outcome(self, task_id: str, snapshot: AiSearchSnapshotResponse) -> None:
        self.snapshots._validate_drafting_outcome(task_id, snapshot)

    def _plan_payload(self, plan: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return self.snapshots._plan_payload(plan)

    def _execution_todos(self, task: Any) -> List[Dict[str, Any]]:
        return self.snapshots._execution_todos(task)

    def _documents_for_snapshot(self, task: Any) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        return self.snapshots._documents_for_snapshot(task)

    def _resume_action(self, task: Any) -> Optional[Dict[str, Any]]:
        phase = self._current_phase_value(task.id)
        pending = current_pending_action(self.storage, task_id=task.id)
        if phase not in ACTIVE_EXECUTION_PHASES or not isinstance(pending, dict):
            return None
        if str(pending.get("action_type") or "").strip() != "resume":
            return None
        payload = build_pending_action_view(pending, camel_case=True) or {}
        current_todo = self._current_todo(task)
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

    def _human_decision_action(self, task: Any) -> Optional[Dict[str, Any]]:
        payload = self._pending_action(task, "human_decision")
        if not payload:
            return None
        return payload

    def _load_analysis_artifacts(self, task: Any) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        return self.analysis_seeds._load_analysis_artifacts(task)

    def _snapshot_download_url(self, task: Any) -> Optional[str]:
        return self.artifacts._snapshot_download_url(task)

    def _current_feature_comparison(
        self,
        task: Any,
        plan_version: int,
        *,
        fallback_latest: bool = False,
    ) -> Optional[Dict[str, Any]]:
        return self.artifacts._current_feature_comparison(task, plan_version, fallback_latest=fallback_latest)

    def _finalize_terminal_artifacts(
        self,
        task_id: str,
        plan_version: int,
        *,
        termination_reason: str = "",
    ) -> Dict[str, Any]:
        return self.artifacts._finalize_terminal_artifacts(task_id, plan_version, termination_reason=termination_reason)

    def get_snapshot(self, session_id: str, owner_id: str) -> AiSearchSnapshotResponse:
        return self.snapshots.get_snapshot(session_id, owner_id)

    def _update_phase(self, task_id: str, phase: str, **meta_updates: Any) -> None:
        task = self.storage.get_task(task_id)
        metadata = merge_ai_search_meta(task, current_phase=phase, **meta_updates)
        self.storage.update_task(
            task_id,
            metadata=metadata,
            status=phase_to_task_status(phase),
            progress=phase_progress(phase),
            current_step=phase_step(phase),
        )
        active_plan_version = int(meta_updates.get("active_plan_version") or metadata.get("ai_search", {}).get("active_plan_version") or 0) if isinstance(metadata, dict) else int(meta_updates.get("active_plan_version") or 0)
        if active_plan_version > 0:
            run = self.storage.get_ai_search_run(task_id, plan_version=active_plan_version)
            if run:
                run_updates: Dict[str, Any] = {
                    "phase": phase,
                    "status": phase_to_task_status(phase),
                }
                if "selected_document_count" in meta_updates:
                    run_updates["selected_document_count"] = int(meta_updates.get("selected_document_count") or 0)
                if "current_task" in meta_updates:
                    run_updates["active_retrieval_todo_id"] = meta_updates.get("current_task")
                if "active_batch_id" in meta_updates:
                    run_updates["active_batch_id"] = meta_updates.get("active_batch_id")
                self.storage.update_ai_search_run(task_id, str(run.get("run_id") or ""), **run_updates)

    def _append_message(
        self,
        task_id: str,
        role: str,
        kind: str,
        content: str,
        *,
        message_id: Optional[str] = None,
        plan_version: Optional[int] = None,
        question_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.storage.create_ai_search_message(
            {
                "message_id": str(message_id or uuid.uuid4().hex),
                "task_id": task_id,
                "plan_version": plan_version,
                "role": role,
                "kind": kind,
                "content": content,
                "stream_status": "completed",
                "question_id": question_id,
                "metadata": metadata or {},
            }
        )

    def _latest_assistant_chat(self, task_id: str) -> str:
        return self.snapshots._latest_assistant_chat(task_id)

    def _current_todo(self, task: Any) -> Optional[Dict[str, Any]]:
        return self.snapshots._current_todo(task)

    def _resolve_main_checkpoint_ns(self, thread_id: str) -> str:
        return self.agent_runs._resolve_main_checkpoint_ns(thread_id)

    def _resolve_resume_checkpoint_id(self, thread_id: str, checkpoint_ns: str) -> Optional[str]:
        return self.agent_runs._resolve_resume_checkpoint_id(thread_id, checkpoint_ns)

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

    def _require_resume_action(self, task: Any) -> Dict[str, Any]:
        resume_action = self._resume_action(task)
        if resume_action is None:
            raise HTTPException(
                status_code=409,
                detail={"code": RESUME_NOT_AVAILABLE_CODE, "message": "当前没有可恢复的失败执行步骤。"},
            )
        return resume_action

    def _require_human_decision_action(self, task: Any) -> Dict[str, Any]:
        decision_action = self._human_decision_action(task)
        if decision_action is None:
            raise HTTPException(status_code=409, detail="当前不在人工决策状态。")
        return decision_action

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
        decision = self._human_decision_action(task) or {}
        reason = str(decision.get("reason") or "").strip()
        summary = str(decision.get("summary") or "").strip()
        parts = ["人工决策后按当前结果完成"]
        if reason:
            parts.append(f"原因：{reason}")
        if summary:
            parts.append(summary)
        return "；".join(parts)

    def create_session(self, owner_id: str) -> AiSearchCreateSessionResponse:
        return self.sessions.create_session(owner_id)

    def _analysis_seed_response(
        self,
        task: Any,
        *,
        reused: bool = False,
        source_task_id: Optional[str] = None,
    ) -> AiSearchCreateSessionResponse:
        return self.analysis_seeds._analysis_seed_response(task, reused=reused, source_task_id=source_task_id)

    def _get_completed_analysis_task(self, owner_id: str, analysis_task_id: str) -> Any:
        return self.analysis_seeds._get_completed_analysis_task(owner_id, analysis_task_id)

    def _find_existing_analysis_seed_session(self, owner_id: str, analysis_task_id: str) -> Optional[Any]:
        return self.analysis_seeds._find_existing_analysis_seed_session(owner_id, analysis_task_id)

    def _prepare_session_from_analysis(self, owner_id: str, analysis_task_id: str) -> AiSearchCreateSessionResponse:
        return self.analysis_seeds._prepare_session_from_analysis(owner_id, analysis_task_id)

    def _complete_analysis_seed(self, owner_id: str, session_id: str) -> AiSearchSnapshotResponse:
        return self.analysis_seeds._complete_analysis_seed(owner_id, session_id)

    def create_session_from_analysis_seed(self, owner_id: str, analysis_task_id: str) -> AiSearchCreateSessionResponse:
        return self.analysis_seeds._prepare_session_from_analysis(owner_id, analysis_task_id)

    def create_session_from_analysis(self, owner_id: str, analysis_task_id: str) -> AiSearchCreateSessionResponse:
        created = self.analysis_seeds._prepare_session_from_analysis(owner_id, analysis_task_id)
        task = self.storage.get_task(created.sessionId)
        meta = get_ai_search_meta(task) if task else {}
        if not created.reused or str(meta.get("analysis_seed_status") or "").strip() == "pending":
            self.analysis_seeds._complete_analysis_seed(owner_id, created.sessionId)
        return created

    def list_sessions(self, owner_id: str) -> AiSearchSessionListResponse:
        return self.sessions.list_sessions(owner_id)

    def update_session(
        self,
        session_id: str,
        owner_id: str,
        *,
        title: Optional[str] = None,
        pinned: Optional[bool] = None,
    ) -> AiSearchSessionSummary:
        return self.sessions.update_session(session_id, owner_id, title=title, pinned=pinned)

    def delete_session(self, session_id: str, owner_id: str) -> Dict[str, bool]:
        return self.sessions.delete_session(session_id, owner_id)

    def _main_agent_config(self, thread_id: str, *, for_resume: bool = False) -> Dict[str, Any]:
        return self.agent_runs._main_agent_config(thread_id, for_resume=for_resume)

    def _main_agent_state_config(self, agent: Any, thread_id: str) -> Dict[str, Any]:
        return self.agent_runs._main_agent_state_config(agent, thread_id)

    def _run_main_agent(self, task_id: str, thread_id: str, payload: Any, *, for_resume: bool = False) -> Dict[str, Any]:
        return self.agent_runs._run_main_agent(task_id, thread_id, payload, for_resume=for_resume)

    def _format_event(self, event_type: str, session_id: str, phase: str, payload: Any) -> str:
        return self.agent_runs._format_event(event_type, session_id, phase, payload)

    def _stream_error_payload(self, exc: Exception) -> Dict[str, Any]:
        return self.agent_runs._stream_error_payload(exc)

    def _current_phase_value(self, task_id: str, fallback: str = PHASE_COLLECTING_REQUIREMENTS) -> str:
        return self.agent_runs._current_phase_value(task_id, fallback)

    def _current_active_plan_version(self, task_id: str) -> int:
        return self.agent_runs._current_active_plan_version(task_id)

    def _reconcile_analysis_seed_phase(self, task_id: str) -> str:
        return self.analysis_seeds._reconcile_analysis_seed_phase(task_id)

    def _init_stream_state(self, snapshot: AiSearchSnapshotResponse, previous_assistant: str) -> Dict[str, Any]:
        return self.agent_runs._init_stream_state(snapshot, previous_assistant)

    async def _iterate_stream_with_keepalive(self, iterator: AsyncIterator[Any]) -> AsyncIterator[Any]:
        async for item in self.agent_runs._iterate_stream_with_keepalive(iterator):
            yield item

    def _normalize_stream_item(self, item: Any) -> tuple[Any, str, Any]:
        return self.agent_runs._normalize_stream_item(item)

    def _is_root_namespace(self, namespace: Any) -> bool:
        return self.agent_runs._is_root_namespace(namespace)

    def _content_to_text(self, content: Any) -> str:
        return self.agent_runs._content_to_text(content)

    def _extract_message_delta(self, payload: Any) -> str:
        return self.agent_runs._extract_message_delta(payload)

    def _normalize_custom_event(self, payload: Any) -> tuple[str, Dict[str, Any]]:
        return self.agent_runs._normalize_custom_event(payload)

    def _normalize_subagent_payload(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.agent_runs._normalize_subagent_payload(event_type, payload)

    def _run_updated_payload(self, snapshot: AiSearchSnapshotResponse) -> Dict[str, Any]:
        return self.agent_runs._run_updated_payload(snapshot)

    def _assistant_started_event(self, session_id: str, phase: str, stream_state: Dict[str, Any], message_id: Optional[str] = None) -> Optional[str]:
        return self.agent_runs._assistant_started_event(session_id, phase, stream_state, message_id)

    def _assistant_completed_events(
        self,
        session_id: str,
        phase: str,
        stream_state: Dict[str, Any],
        content: str,
        *,
        message_id: Optional[str] = None,
    ) -> List[str]:
        return self.agent_runs._assistant_completed_events(session_id, phase, stream_state, content, message_id=message_id)

    async def _emit_snapshot_diff_events(
        self,
        previous: AiSearchSnapshotResponse,
        current: AiSearchSnapshotResponse,
        *,
        stream_state: Dict[str, Any],
    ) -> AsyncIterator[str]:
        async for event in self.agent_runs._emit_snapshot_diff_events(previous, current, stream_state=stream_state):
            yield event

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
        async for event in self.agent_runs._consume_live_agent_stream(
            session_id=session_id,
            owner_id=owner_id,
            task_id=task_id,
            agent=agent,
            payload=payload,
            stream_state=stream_state,
            initial_snapshot=initial_snapshot,
            previous_phase=previous_phase,
            config=config,
            forward_model_text=forward_model_text,
        ):
            yield event

    async def _emit_final_assistant_if_needed(
        self,
        task_id: str,
        stream_state: Dict[str, Any],
        *,
        allow_model_fallback: bool = True,
    ) -> AsyncIterator[str]:
        async for event in self.agent_runs._emit_final_assistant_if_needed(
            task_id,
            stream_state,
            allow_model_fallback=allow_model_fallback,
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
        async for event in self.agent_runs._stream_main_agent_execution(
            task=task,
            owner_id=owner_id,
            thread_id=thread_id,
            payload=payload,
            previous_phase=previous_phase,
            for_resume=for_resume,
            post_run=post_run,
        ):
            yield event

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
        async for event in self.agent_runs._stream_feature_agent_execution(
            task=task,
            owner_id=owner_id,
            plan_version=plan_version,
            previous_phase=previous_phase,
            force_complete=force_complete,
            termination_reason=termination_reason,
        ):
            yield event

    async def stream_message(self, session_id: str, owner_id: str, content: str) -> AsyncIterator[str]:
        async for event in self.agent_runs.stream_message(session_id, owner_id, content):
            yield event

    async def stream_resume(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self.agent_runs.stream_resume(session_id, owner_id):
            yield event

    async def stream_answer(self, session_id: str, owner_id: str, question_id: str, answer: str) -> AsyncIterator[str]:
        async for event in self.agent_runs.stream_answer(session_id, owner_id, question_id, answer):
            yield event

    async def stream_plan_confirmation(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        async for event in self.agent_runs.stream_plan_confirmation(session_id, owner_id, plan_version):
            yield event

    async def stream_analysis_seed(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self.agent_runs.stream_analysis_seed(session_id, owner_id):
            yield event

    async def stream_decision_continue(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self.agent_runs.stream_decision_continue(session_id, owner_id):
            yield event

    async def stream_decision_complete(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self.agent_runs.stream_decision_complete(session_id, owner_id):
            yield event

    def patch_selected_documents(
        self,
        session_id: str,
        owner_id: str,
        plan_version: int,
        add_document_ids: Optional[List[str]],
        remove_document_ids: Optional[List[str]],
    ) -> AiSearchSnapshotResponse:
        return self.agent_runs.patch_selected_documents(session_id, owner_id, plan_version, add_document_ids, remove_document_ids)

    async def stream_feature_comparison(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        async for event in self.agent_runs.stream_feature_comparison(session_id, owner_id, plan_version):
            yield event
