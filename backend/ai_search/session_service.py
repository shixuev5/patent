"""Session lifecycle collaborator for AI Search."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from agents.ai_search.src.state import (
    ACTIVE_EXECUTION_PHASES,
    PHASE_COLLECTING_REQUIREMENTS,
    default_ai_search_meta,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from backend.storage import TaskType
from .models import (
    AI_SEARCH_SESSION_NOT_FOUND_CODE,
    INVALID_SESSION_PHASE_CODE,
    SESSION_DELETE_BLOCKED_CODE,
    SESSION_TITLE_REQUIRED_CODE,
    AiSearchCreateSessionResponse,
    AiSearchSessionListResponse,
    AiSearchSessionSummary,
)


class AiSearchSessionService:
    def __init__(self, facade: Any) -> None:
        self.facade = facade

    @property
    def storage(self):
        return self.facade.storage

    def _raise_session_not_found(self) -> None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": AI_SEARCH_SESSION_NOT_FOUND_CODE,
                "message": "AI 检索会话不存在。",
            },
        )

    def _raise_invalid_phase(self, phase: str, message: str) -> None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": INVALID_SESSION_PHASE_CODE,
                "message": message,
                "phase": phase,
            },
        )

    def _get_owned_session_task(self, session_id: str, owner_id: str) -> Any:
        task = self.storage.get_task(session_id)
        if not task or str(task.owner_id or "") != str(owner_id or "") or str(task.task_type or "") != TaskType.AI_SEARCH.value:
            self._raise_session_not_found()
        return task

    def create_session(self, owner_id: str) -> AiSearchCreateSessionResponse:
        self.facade._enforce_daily_quota(owner_id, task_type=TaskType.AI_SEARCH.value)
        task = self.facade.task_manager.create_task(
            owner_id=owner_id,
            task_type=TaskType.AI_SEARCH.value,
            title=None,
        )
        thread_id = f"ai-search-{task.id}"
        self.storage.update_task(
            task.id,
            title=f"AI 检索会话 - {task.id}",
            metadata=merge_ai_search_meta(task, **default_ai_search_meta(thread_id)),
            status=phase_to_task_status(PHASE_COLLECTING_REQUIREMENTS),
            progress=phase_progress(PHASE_COLLECTING_REQUIREMENTS),
            current_step=phase_step(PHASE_COLLECTING_REQUIREMENTS),
        )
        self.facade._append_message(
            task.id,
            "assistant",
            "chat",
            "请描述检索目标、核心技术方案、关注特征，并尽量提供申请人、申请日或优先权日等约束条件。",
        )
        return AiSearchCreateSessionResponse(sessionId=task.id, taskId=task.id, threadId=thread_id)

    def list_sessions(self, owner_id: str) -> AiSearchSessionListResponse:
        tasks = [
            task
            for task in self.facade.task_manager.list_tasks(owner_id=owner_id, limit=200)
            if str(task.task_type or "") == TaskType.AI_SEARCH.value
        ]
        return AiSearchSessionListResponse(items=[self.facade.snapshots._session_summary(task) for task in tasks], total=len(tasks))

    def update_session(
        self,
        session_id: str,
        owner_id: str,
        *,
        title: Optional[str] = None,
        pinned: Optional[bool] = None,
    ) -> AiSearchSessionSummary:
        task = self._get_owned_session_task(session_id, owner_id)

        updates: Dict[str, Any] = {}
        if title is not None:
            normalized_title = str(title).strip()
            if not normalized_title:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": SESSION_TITLE_REQUIRED_CODE,
                        "message": "会话标题不能为空。",
                        "suggestion": "你可以换一个更具体的标题后再试。",
                    },
                )
            updates["title"] = normalized_title
        if pinned is not None:
            updates["metadata"] = merge_ai_search_meta(task, pinned=bool(pinned))

        if not updates:
            return self.facade.snapshots._session_summary(task)

        self.storage.update_task(session_id, **updates)
        updated = self._get_owned_session_task(session_id, owner_id)
        return self.facade.snapshots._session_summary(updated)

    def delete_session(self, session_id: str, owner_id: str) -> Dict[str, bool]:
        task = self._get_owned_session_task(session_id, owner_id)
        phase = self.facade.agent_runs._current_phase_value(task.id, PHASE_COLLECTING_REQUIREMENTS)
        if phase in ACTIVE_EXECUTION_PHASES:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": SESSION_DELETE_BLOCKED_CODE,
                    "message": "这个检索还在执行中。",
                    "suggestion": "你可以等它结束后再删除。",
                },
            )

        self.facade.task_manager.delete_task(session_id)
        return {"deleted": True}
