"""
AI search session service.
"""

from __future__ import annotations

import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from agents.ai_search.src.state import (
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_COMPLETED,
    PHASE_DRAFTING_PLAN,
    get_ai_search_meta,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from backend.notifications import build_task_notification_dispatcher
from backend.system_logs import emit_system_log
from backend.storage import TaskType, get_pipeline_manager
from backend.task_usage_tracking import (
    create_task_usage_collector,
    persist_task_usage,
    task_usage_collection,
)
from backend.usage import _enforce_daily_quota
from .models import (
    AiSearchCreateSessionResponse,
    AiSearchExecutionQueueResponse,
    AiSearchSessionListResponse,
    AiSearchSessionSummary,
    AiSearchSnapshotResponse,
)


task_manager = get_pipeline_manager()

MAIN_AGENT_CHECKPOINT_NS = "ai_search_main"
MAIN_AGENT_PROGRESS_POLL_SECONDS = 15.0
DEFAULT_MESSAGE_PHASES = {
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_DRAFTING_PLAN,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_COMPLETED,
}


def build_main_agent(storage: Any, task_id: str) -> Any:
    from agents.ai_search.main import build_main_agent as _build_main_agent

    return _build_main_agent(storage, task_id)


def build_feature_comparer_agent(storage: Any, task_id: str) -> Any:
    from agents.ai_search.main import build_feature_comparer_agent as _build_feature_comparer_agent

    return _build_feature_comparer_agent(storage, task_id)


def build_ai_search_terminal_artifacts(**kwargs: Any) -> Dict[str, Any]:
    from .reporting import build_ai_search_terminal_artifacts as _build_ai_search_terminal_artifacts

    return _build_ai_search_terminal_artifacts(**kwargs)


class AiSearchService:
    def __init__(self):
        from .agent_run_service import AiSearchAgentRunService
        from .analysis_seed_service import AiSearchAnalysisSeedService
        from .artifacts_service import AiSearchArtifactsService
        from .session_service import AiSearchSessionService
        from .snapshot_service import AiSearchSnapshotService

        self.task_manager = task_manager
        self.MAIN_AGENT_CHECKPOINT_NS = MAIN_AGENT_CHECKPOINT_NS
        self.MAIN_AGENT_PROGRESS_POLL_SECONDS = MAIN_AGENT_PROGRESS_POLL_SECONDS
        self.DEFAULT_MESSAGE_PHASES = DEFAULT_MESSAGE_PHASES
        self.sessions = AiSearchSessionService(self)
        self.snapshots = AiSearchSnapshotService(self)
        self.artifacts = AiSearchArtifactsService(self)
        self.analysis_seeds = AiSearchAnalysisSeedService(self)
        self.agent_runs = AiSearchAgentRunService(self)

    @property
    def storage(self):
        return self.task_manager.storage

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

    def _build_terminal_artifacts(self, **kwargs: Any) -> Dict[str, Any]:
        return build_ai_search_terminal_artifacts(**kwargs)

    def notify_task_terminal_status(
        self,
        task_id: str,
        terminal_status: str,
        *,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        service = build_task_notification_dispatcher(
            storage=self.storage,
            system_log_emitter=self._emit_system_log,
        )
        return service.notify_task_terminal_status(
            task_id,
            terminal_status=terminal_status,
            task_type=TaskType.AI_SEARCH.value,
            error_message=error_message,
        )

    def _main_agent_progress_poll_seconds(self) -> float:
        return MAIN_AGENT_PROGRESS_POLL_SECONDS

    async def _stream_with_task_usage(
        self,
        session_id: str,
        owner_id: str,
        stream_factory,
    ) -> AsyncIterator[str]:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        usage_collector = create_task_usage_collector(
            task_id=task.id,
            owner_id=owner_id,
            task_type=TaskType.AI_SEARCH.value,
        )
        try:
            with task_usage_collection(usage_collector):
                async for event in stream_factory():
                    yield event
        finally:
            latest_task = self.storage.get_task(task.id)
            if latest_task:
                latest_status = getattr(latest_task.status, "value", latest_task.status)
                usage_collector.mark_status(latest_status)
            persist_task_usage(self.storage, usage_collector, merge=True)

    def get_snapshot(self, session_id: str, owner_id: str) -> AiSearchSnapshotResponse:
        return self.snapshots.get_snapshot(session_id, owner_id)

    def append_execution_queue_message(self, session_id: str, owner_id: str, content: str) -> AiSearchExecutionQueueResponse:
        return self.agent_runs.append_execution_queue_message(session_id, owner_id, content)

    def delete_execution_queue_message(self, session_id: str, owner_id: str, queue_message_id: str) -> AiSearchExecutionQueueResponse:
        return self.agent_runs.delete_execution_queue_message(session_id, owner_id, queue_message_id)

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

    def create_session(self, owner_id: str) -> AiSearchCreateSessionResponse:
        return self.sessions.create_session(owner_id)

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

    def _run_main_agent(self, task_id: str, thread_id: str, payload: Any, *, for_resume: bool = False) -> Dict[str, Any]:
        return self.agent_runs._run_main_agent(task_id, thread_id, payload, for_resume=for_resume)

    async def stream_message(self, session_id: str, owner_id: str, content: str) -> AsyncIterator[str]:
        async for event in self._stream_with_task_usage(
            session_id,
            owner_id,
            lambda: self.agent_runs.stream_message(session_id, owner_id, content),
        ):
            yield event

    async def stream_resume(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self._stream_with_task_usage(
            session_id,
            owner_id,
            lambda: self.agent_runs.stream_resume(session_id, owner_id),
        ):
            yield event

    async def stream_answer(self, session_id: str, owner_id: str, question_id: str, answer: str) -> AsyncIterator[str]:
        async for event in self._stream_with_task_usage(
            session_id,
            owner_id,
            lambda: self.agent_runs.stream_answer(session_id, owner_id, question_id, answer),
        ):
            yield event

    async def stream_plan_confirmation(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        async for event in self._stream_with_task_usage(
            session_id,
            owner_id,
            lambda: self.agent_runs.stream_plan_confirmation(session_id, owner_id, plan_version),
        ):
            yield event

    async def stream_analysis_seed(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self._stream_with_task_usage(
            session_id,
            owner_id,
            lambda: self.agent_runs.stream_analysis_seed(session_id, owner_id),
        ):
            yield event

    async def stream_decision_continue(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self._stream_with_task_usage(
            session_id,
            owner_id,
            lambda: self.agent_runs.stream_decision_continue(session_id, owner_id),
        ):
            yield event

    async def stream_decision_complete(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self._stream_with_task_usage(
            session_id,
            owner_id,
            lambda: self.agent_runs.stream_decision_complete(session_id, owner_id),
        ):
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
        async for event in self._stream_with_task_usage(
            session_id,
            owner_id,
            lambda: self.agent_runs.stream_feature_comparison(session_id, owner_id, plan_version),
        ):
            yield event
