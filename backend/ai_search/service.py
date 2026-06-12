"""
AI search session service.
"""

from __future__ import annotations

import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from patent_agents.ai_search.src.state import get_ai_search_meta
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
    AiSearchSessionListResponse,
    AiSearchSessionSummary,
    AiSearchSnapshotResponse,
)


task_manager = get_pipeline_manager()

class AiSearchService:
    def __init__(self):
        from .agent_run_service import AiSearchAgentRunService
        from .analysis_seed_service import AiSearchAnalysisSeedService
        from .artifacts_service import AiSearchArtifactsService
        from .reply_seed_service import AiSearchReplySeedService
        from .session_service import AiSearchSessionService
        from .snapshot_service import AiSearchSnapshotService
        from .supplement_service import AiSearchSupplementService

        self.task_manager = task_manager
        self.sessions = AiSearchSessionService(self)
        self.snapshots = AiSearchSnapshotService(self)
        self.artifacts = AiSearchArtifactsService(self)
        self.analysis_seeds = AiSearchAnalysisSeedService(self)
        self.reply_seeds = AiSearchReplySeedService(self)
        self.agent_runs = AiSearchAgentRunService(self)
        self.supplements = AiSearchSupplementService(self)

    @property
    def storage(self):
        return self.task_manager.storage

    def _emit_system_log(self, **kwargs: Any) -> None:
        emit_system_log(**kwargs)

    def _enforce_daily_quota(self, owner_id: str, *, task_type: Optional[str] = None) -> None:
        _enforce_daily_quota(owner_id, task_type=task_type)

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
        iterator = None
        try:
            stream = stream_factory()
            iterator = stream.__aiter__()
            while True:
                try:
                    with task_usage_collection(usage_collector):
                        event = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                yield event
        finally:
            close_stream = getattr(iterator, "aclose", None) if iterator is not None else None
            if callable(close_stream):
                await close_stream()
            latest_task = self.storage.get_task(task.id)
            if latest_task:
                latest_status = getattr(latest_task.status, "value", latest_task.status)
                usage_collector.mark_status(latest_status)
            persist_task_usage(self.storage, usage_collector, merge=True)

    def get_snapshot(self, session_id: str, owner_id: str) -> AiSearchSnapshotResponse:
        self.agent_runs.repair_stale_running_state(session_id, owner_id)
        return self.snapshots.get_snapshot(session_id, owner_id)

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

    def create_session_from_reply_seed(self, owner_id: str, reply_task_id: str) -> AiSearchCreateSessionResponse:
        return self.reply_seeds._prepare_session_from_reply(owner_id, reply_task_id)

    def create_session_from_analysis(self, owner_id: str, analysis_task_id: str) -> AiSearchCreateSessionResponse:
        created = self.analysis_seeds._prepare_session_from_analysis(owner_id, analysis_task_id)
        task = self.storage.get_task(created.sessionId)
        meta = get_ai_search_meta(task) if task else {}
        if not created.reused or str(meta.get("analysis_seed_status") or "").strip() == "pending":
            self.analysis_seeds._complete_source_seed(owner_id, created.sessionId)
        return created

    def create_session_from_reply(self, owner_id: str, reply_task_id: str) -> AiSearchCreateSessionResponse:
        created = self.reply_seeds._prepare_session_from_reply(owner_id, reply_task_id)
        task = self.storage.get_task(created.sessionId)
        meta = get_ai_search_meta(task) if task else {}
        if not created.reused or str(meta.get("analysis_seed_status") or "").strip() == "pending":
            self.analysis_seeds._complete_source_seed(owner_id, created.sessionId)
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

    def update_stop_policy(self, session_id: str, owner_id: str, policy: Dict[str, Any]) -> AiSearchSnapshotResponse:
        return self.sessions.update_stop_policy(session_id, owner_id, policy)

    def delete_session(self, session_id: str, owner_id: str) -> Dict[str, bool]:
        return self.sessions.delete_session(session_id, owner_id)

    def cancel_current_run(self, session_id: str, owner_id: str) -> AiSearchSnapshotResponse:
        self.agent_runs.cancel_current_run(session_id, owner_id)
        return self.snapshots.get_snapshot(session_id, owner_id)

    def download_attachment(self, session_id: str, owner_id: str, attachment_id: str):
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        return self.artifacts.download_attachment(task, attachment_id)

    def export_report(self, session_id: str, owner_id: str) -> AiSearchSnapshotResponse:
        task = self.sessions._get_owned_session_task(session_id, owner_id)
        self.artifacts.export_session_report(task)
        return self.snapshots.get_snapshot(session_id, owner_id)

    async def supplement_documents(
        self,
        session_id: str,
        owner_id: str,
        *,
        patent_numbers: str = "",
        files: Optional[List[Any]] = None,
        review_goal: str = "",
    ) -> Dict[str, Any]:
        return await self.supplements.supplement_documents(
            session_id,
            owner_id,
            patent_numbers=patent_numbers,
            files=files,
            review_goal=review_goal,
        )

    async def stream_message(self, session_id: str, owner_id: str, content: str) -> AsyncIterator[str]:
        async for event in self._stream_with_task_usage(
            session_id,
            owner_id,
            lambda: self.agent_runs.stream_message(session_id, owner_id, content),
        ):
            yield event

    async def subscribe_stream(self, session_id: str, owner_id: str, *, after_seq: int = 0) -> AsyncIterator[str]:
        async for event in self.agent_runs.subscribe_stream(session_id, owner_id, after_seq=after_seq):
            yield event

    async def stream_analysis_seed(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        async for event in self._stream_with_task_usage(
            session_id,
            owner_id,
            lambda: self.agent_runs.stream_analysis_seed(session_id, owner_id),
        ):
            yield event

    async def stream_document_selection(
        self,
        session_id: str,
        owner_id: str,
        plan_version: int,
        review_document_ids: Optional[List[str]],
        remove_document_ids: Optional[List[str]],
    ) -> AsyncIterator[str]:
        async for event in self._stream_with_task_usage(
            session_id,
            owner_id,
            lambda: self.agent_runs.stream_document_selection(
                session_id,
                owner_id,
                plan_version,
                review_document_ids,
                remove_document_ids,
            ),
        ):
            yield event
