"""
AI search session service.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import HTTPException
from langgraph.types import Command

from agents.ai_search.main import (
    build_feature_comparer_agent,
    build_planning_agent,
    extract_latest_ai_message,
    extract_structured_response,
)
from agents.ai_search.src.execution import run_query_execution_rounds
from agents.ai_search.src.screening import build_feature_prompt, run_screening_pipeline
from agents.ai_search.src.state import (
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_COMPLETED,
    PHASE_DRAFTING_PLAN,
    PHASE_RESULTS_READY,
    PHASE_SEARCHING,
    build_plan_summary,
    default_ai_search_meta,
    get_ai_search_meta,
    latest_search_elements,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from backend.storage import TaskType, get_pipeline_manager
from backend.time_utils import utc_now_z
from backend.usage import _enforce_daily_quota

from .models import (
    AI_SEARCH_SESSION_NOT_FOUND_CODE,
    INVALID_SESSION_PHASE_CODE,
    PENDING_QUESTION_EXISTS_CODE,
    PLAN_CONFIRMATION_REQUIRED_CODE,
    SEARCH_IN_PROGRESS_CODE,
    STALE_PLAN_CONFIRMATION_CODE,
    AiSearchCreateSessionResponse,
    AiSearchSessionListResponse,
    AiSearchSessionSummary,
    AiSearchSnapshotResponse,
)


task_manager = get_pipeline_manager()

PLANNING_CHECKPOINT_NS = "ai_search_planning"
DEFAULT_MESSAGE_PHASES = {
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_DRAFTING_PLAN,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_RESULTS_READY,
    PHASE_COMPLETED,
}


class AiSearchService:
    def __init__(self):
        self.storage = task_manager.storage

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

    def _session_summary(self, task: Any) -> AiSearchSessionSummary:
        meta = get_ai_search_meta(task)
        return AiSearchSessionSummary(
            sessionId=task.id,
            taskId=task.id,
            title=str(task.title or "未命名 AI 检索会话"),
            status=task.status.value,
            phase=str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS),
            activePlanVersion=meta.get("active_plan_version"),
            selectedDocumentCount=int(meta.get("selected_document_count") or 0),
            createdAt=utc_now_z() if not getattr(task, "created_at", None) else task.created_at.isoformat(),
            updatedAt=utc_now_z() if not getattr(task, "updated_at", None) else task.updated_at.isoformat(),
        )

    def _display_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        visible_kinds = {"chat", "question", "answer"}
        return [
            item
            for item in messages
            if str(item.get("kind") or "") in visible_kinds
        ]

    def _current_plan(self, task: Any) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        active_plan_version = meta.get("active_plan_version")
        if active_plan_version:
            plan = self.storage.get_ai_search_plan(task.id, int(active_plan_version))
            if plan:
                return plan
        return self.storage.get_ai_search_plan(task.id)

    def _pending_question(self, task: Any, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        question_id = str(meta.get("pending_question_id") or "").strip()
        if not question_id:
            return None
        for item in reversed(messages):
            if str(item.get("question_id") or "") == question_id:
                metadata = item.get("metadata")
                return metadata if isinstance(metadata, dict) else None
        return None

    def _pending_confirmation(self, task: Any, current_plan: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        pending_plan_version = int(meta.get("pending_confirmation_plan_version") or 0)
        if pending_plan_version <= 0:
            return None
        if current_plan and int(current_plan.get("plan_version") or 0) == pending_plan_version:
            summary = build_plan_summary(current_plan)
            return {
                "planVersion": pending_plan_version,
                "planSummary": summary,
                "confirmationLabel": "确认检索计划",
            }
        return None

    def _documents_for_snapshot(self, task: Any) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 0)
        if plan_version <= 0:
            return [], []
        documents = self.storage.list_ai_search_documents(task.id, plan_version)
        selected = [item for item in documents if str(item.get("stage") or "") == "selected"]
        candidate = [item for item in documents if str(item.get("stage") or "") != "selected"]
        return candidate, selected

    def get_snapshot(self, session_id: str, owner_id: str) -> AiSearchSnapshotResponse:
        task = self._get_owned_session_task(session_id, owner_id)
        messages = self.storage.list_ai_search_messages(task.id)
        current_plan = self._current_plan(task)
        search_elements = latest_search_elements(messages)
        candidate_documents, selected_documents = self._documents_for_snapshot(task)
        feature_table = None
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if active_plan_version > 0:
            feature_table = self.storage.get_ai_search_feature_table(
                task.id,
                active_plan_version,
                feature_table_id=str(meta.get("current_feature_table_id") or "").strip() or None,
            )
        return AiSearchSnapshotResponse(
            session=self._session_summary(task),
            phase=str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS),
            messages=self._display_messages(messages),
            searchElements=search_elements,
            currentPlan=current_plan,
            candidateDocuments=candidate_documents,
            selectedDocuments=selected_documents,
            featureTable=feature_table,
            pendingQuestion=self._pending_question(task, messages),
            pendingConfirmation=self._pending_confirmation(task, current_plan),
        )

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

    def _append_message(
        self,
        task_id: str,
        role: str,
        kind: str,
        content: str,
        *,
        plan_version: Optional[int] = None,
        question_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
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
        messages = self.storage.list_ai_search_messages(task_id)
        for item in reversed(messages):
            if str(item.get("role") or "") == "assistant" and str(item.get("kind") or "") == "chat":
                return str(item.get("content") or "").strip()
        return ""

    def create_session(self, owner_id: str) -> AiSearchCreateSessionResponse:
        _enforce_daily_quota(owner_id, task_type=TaskType.AI_SEARCH.value)
        task = task_manager.create_task(
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
        self._append_message(
            task.id,
            "assistant",
            "chat",
            "请描述检索目标、核心技术方案、关注特征，并尽量提供申请人、申请日或优先权日等约束条件。",
        )
        return AiSearchCreateSessionResponse(sessionId=task.id, taskId=task.id, threadId=thread_id)

    def list_sessions(self, owner_id: str) -> AiSearchSessionListResponse:
        tasks = [
            task
            for task in task_manager.list_tasks(owner_id=owner_id, limit=200)
            if str(task.task_type or "") == TaskType.AI_SEARCH.value
        ]
        return AiSearchSessionListResponse(items=[self._session_summary(task) for task in tasks], total=len(tasks))

    def _planning_config(self, thread_id: str) -> Dict[str, Any]:
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": PLANNING_CHECKPOINT_NS,
            }
        }

    def _run_planning_agent(self, task_id: str, thread_id: str, payload: Any) -> Dict[str, Any]:
        agent = build_planning_agent(self.storage, task_id)
        config = self._planning_config(thread_id)
        interrupted = False
        for chunk in agent.stream(payload, config):
            if "__interrupt__" in chunk:
                interrupted = True
        state = agent.get_state(config)
        values = state.values if state else {}
        return {"interrupted": interrupted, "values": values}

    async def _emit_snapshot_events(
        self,
        snapshot: AiSearchSnapshotResponse,
    ) -> AsyncIterator[str]:
        if snapshot.searchElements is not None:
            yield self._format_event("search_elements.updated", snapshot.session.sessionId, snapshot.phase, snapshot.searchElements)
        if snapshot.currentPlan is not None:
            yield self._format_event("plan.updated", snapshot.session.sessionId, snapshot.phase, snapshot.currentPlan)
        if snapshot.pendingQuestion is not None:
            yield self._format_event("question.required", snapshot.session.sessionId, snapshot.phase, snapshot.pendingQuestion)
        if snapshot.pendingConfirmation is not None:
            yield self._format_event("plan.awaiting_confirmation", snapshot.session.sessionId, snapshot.phase, snapshot.pendingConfirmation)
        if snapshot.candidateDocuments:
            yield self._format_event(
                "documents.updated",
                snapshot.session.sessionId,
                snapshot.phase,
                {"count": len(snapshot.candidateDocuments), "items": snapshot.candidateDocuments},
            )
        if snapshot.selectedDocuments:
            yield self._format_event(
                "selection.updated",
                snapshot.session.sessionId,
                snapshot.phase,
                {"count": len(snapshot.selectedDocuments), "items": snapshot.selectedDocuments},
            )
        if snapshot.featureTable is not None:
            yield self._format_event("feature_table.updated", snapshot.session.sessionId, snapshot.phase, snapshot.featureTable)

    def _format_event(self, event_type: str, session_id: str, phase: str, payload: Any) -> str:
        message = {
            "type": event_type,
            "sessionId": session_id,
            "taskId": session_id,
            "phase": phase,
            "payload": payload,
        }
        return f"data: {json.dumps(message, ensure_ascii=False)}\n\n"

    async def stream_message(self, session_id: str, owner_id: str, content: str) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS)
        if phase == PHASE_SEARCHING:
            raise HTTPException(
                status_code=409,
                detail={"code": SEARCH_IN_PROGRESS_CODE, "message": "检索执行中，暂不支持发送新消息。"},
            )
        if phase == PHASE_AWAITING_USER_ANSWER and meta.get("pending_question_id"):
            raise HTTPException(
                status_code=409,
                detail={"code": PENDING_QUESTION_EXISTS_CODE, "message": "请先回答当前追问。"},
            )
        if phase not in DEFAULT_MESSAGE_PHASES:
            self._raise_invalid_phase(phase, "当前阶段不允许发送普通消息。")

        if phase == PHASE_AWAITING_PLAN_CONFIRMATION and meta.get("active_plan_version"):
            active_plan_version = int(meta["active_plan_version"])
            self.storage.update_ai_search_plan(task.id, active_plan_version, status="superseded", superseded_at=utc_now_z())
            self._update_phase(task.id, PHASE_DRAFTING_PLAN, pending_confirmation_plan_version=None)

        self._append_message(task.id, "user", "chat", content)
        self._update_phase(task.id, PHASE_DRAFTING_PLAN)
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        previous_assistant = self._latest_assistant_chat(task.id)
        result = await asyncio.to_thread(
            self._run_planning_agent,
            task.id,
            thread_id,
            {"messages": [{"role": "user", "content": content}]},
        )
        assistant_text = extract_latest_ai_message(result["values"])
        active_plan_version = int(get_ai_search_meta(self.storage.get_task(task.id)).get("active_plan_version") or 0)
        if assistant_text and assistant_text != previous_assistant:
            self._append_message(task.id, "assistant", "chat", assistant_text, plan_version=active_plan_version or None)
            yield self._format_event("message.completed", task.id, self.get_snapshot(task.id, owner_id).phase, {"content": assistant_text})
        snapshot = self.get_snapshot(task.id, owner_id)
        async for event in self._emit_snapshot_events(snapshot):
            yield event
        yield self._format_event("run.completed", task.id, snapshot.phase, {"interrupted": result["interrupted"]})

    async def stream_answer(self, session_id: str, owner_id: str, question_id: str, answer: str) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or "")
        pending_question_id = str(meta.get("pending_question_id") or "").strip()
        if phase != PHASE_AWAITING_USER_ANSWER or not pending_question_id:
            self._raise_invalid_phase(phase, "当前没有待回答的问题。")
        if pending_question_id != question_id:
            raise HTTPException(
                status_code=409,
                detail={"code": PENDING_QUESTION_EXISTS_CODE, "message": "回答的问题已过期。"},
            )
        self._append_message(task.id, "user", "answer", answer, question_id=question_id)
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        previous_assistant = self._latest_assistant_chat(task.id)
        result = await asyncio.to_thread(
            self._run_planning_agent,
            task.id,
            thread_id,
            Command(resume=answer),
        )
        assistant_text = extract_latest_ai_message(result["values"])
        active_plan_version = int(get_ai_search_meta(self.storage.get_task(task.id)).get("active_plan_version") or 0)
        if assistant_text and assistant_text != previous_assistant:
            self._append_message(task.id, "assistant", "chat", assistant_text, plan_version=active_plan_version or None)
            yield self._format_event("message.completed", task.id, self.get_snapshot(task.id, owner_id).phase, {"content": assistant_text})
        snapshot = self.get_snapshot(task.id, owner_id)
        yield self._format_event("question.resolved", task.id, snapshot.phase, {"questionId": question_id, "answer": answer})
        async for event in self._emit_snapshot_events(snapshot):
            yield event
        yield self._format_event("run.completed", task.id, snapshot.phase, {"interrupted": result["interrupted"]})

    async def stream_plan_confirmation(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or "")
        if phase != PHASE_AWAITING_PLAN_CONFIRMATION:
            raise HTTPException(
                status_code=409,
                detail={"code": PLAN_CONFIRMATION_REQUIRED_CODE, "message": "当前没有待确认的检索计划。"},
            )
        pending_plan_version = int(meta.get("pending_confirmation_plan_version") or 0)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if pending_plan_version != plan_version or active_plan_version != plan_version:
            raise HTTPException(
                status_code=409,
                detail={"code": STALE_PLAN_CONFIRMATION_CODE, "message": "当前计划版本已失效，请刷新后重试。"},
            )
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        previous_assistant = self._latest_assistant_chat(task.id)
        planning_result = await asyncio.to_thread(
            self._run_planning_agent,
            task.id,
            thread_id,
            Command(resume={"confirmed": True, "plan_version": plan_version}),
        )
        assistant_text = extract_latest_ai_message(planning_result["values"])
        if assistant_text and assistant_text != previous_assistant:
            self._append_message(task.id, "assistant", "chat", assistant_text, plan_version=plan_version)
            yield self._format_event("message.completed", task.id, PHASE_SEARCHING, {"content": assistant_text})
        yield self._format_event("plan.confirmed", task.id, PHASE_SEARCHING, {"planVersion": plan_version})
        async for event in self._run_search_pipeline(task.id, owner_id, plan_version):
            yield event

    async def _run_search_pipeline(self, task_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        task = self.storage.get_task(task_id)
        if not task:
            self._raise_session_not_found()
        plan = self.storage.get_ai_search_plan(task_id, plan_version)
        if not plan:
            self._raise_invalid_phase(PHASE_SEARCHING, "当前计划不存在。")
        self._update_phase(task_id, PHASE_SEARCHING, active_plan_version=plan_version)
        yield self._format_event("execution.round.started", task_id, PHASE_SEARCHING, {"planVersion": plan_version})
        execution_result = await asyncio.to_thread(run_query_execution_rounds, self.storage, task_id, plan_version)
        for summary in execution_result.get("summaries") or []:
            yield self._format_event("execution.round.completed", task_id, PHASE_SEARCHING, summary)
        candidate_records = self.storage.list_ai_search_documents(task_id, plan_version)
        yield self._format_event(
            "documents.updated",
            task_id,
            PHASE_SEARCHING,
            {"count": len(candidate_records), "items": candidate_records},
        )
        if not candidate_records:
            self._append_message(task_id, "assistant", "chat", "当前计划未检索到候选文献。", plan_version=plan_version)
            self._update_phase(task_id, PHASE_RESULTS_READY, selected_document_count=0)
            snapshot = self.get_snapshot(task_id, owner_id)
            yield self._format_event("execution.stopped", task_id, snapshot.phase, {"selectedCount": 0})
            yield self._format_event("run.completed", task_id, snapshot.phase, {"selectedCount": 0})
            return

        yield self._format_event("execution.screening_entered", task_id, PHASE_SEARCHING, {"candidateCount": len(candidate_records)})
        screening_result = await asyncio.to_thread(run_screening_pipeline, self.storage, task_id, plan_version)
        selected_count = int(screening_result.get("selected_count") or 0)
        self._append_message(
            task_id,
            "assistant",
            "chat",
            f"已完成动态检索、粗筛和精读，推荐 {selected_count} 篇对比文件。",
            plan_version=plan_version,
        )
        self._update_phase(
            task_id,
            PHASE_RESULTS_READY,
            active_plan_version=plan_version,
            selected_document_count=selected_count,
            current_feature_table_id=None,
        )
        snapshot = self.get_snapshot(task_id, owner_id)
        async for event in self._emit_snapshot_events(snapshot):
            yield event
        yield self._format_event("execution.stopped", task_id, snapshot.phase, {"selectedCount": selected_count})
        yield self._format_event("run.completed", task_id, snapshot.phase, {"selectedCount": selected_count})

    def patch_selected_documents(
        self,
        session_id: str,
        owner_id: str,
        plan_version: int,
        add_document_ids: Optional[List[str]],
        remove_document_ids: Optional[List[str]],
    ) -> AiSearchSnapshotResponse:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if active_plan_version != int(plan_version):
            raise HTTPException(
                status_code=409,
                detail={"code": STALE_PLAN_CONFIRMATION_CODE, "message": "当前只允许操作活动计划版本。"},
            )
        phase = str(meta.get("current_phase") or "")
        if phase not in {PHASE_RESULTS_READY, PHASE_COMPLETED}:
            self._raise_invalid_phase(phase, "当前阶段不允许调整对比文件。")
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
                agent_reason="用户手动移出对比文件",
            )
        selected_count = len(self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"]))
        self._update_phase(
            task.id,
            PHASE_RESULTS_READY,
            selected_document_count=selected_count,
            current_feature_table_id=None,
        )
        return self.get_snapshot(task.id, owner_id)

    async def stream_feature_table(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if active_plan_version != int(plan_version):
            raise HTTPException(
                status_code=409,
                detail={"code": STALE_PLAN_CONFIRMATION_CODE, "message": "当前只允许生成活动计划版本的特征对比表。"},
            )
        selected_documents = self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"])
        if not selected_documents:
            self._raise_invalid_phase(PHASE_RESULTS_READY, "当前没有已选对比文件。")
        plan = self.storage.get_ai_search_plan(task.id, plan_version) or {}
        search_elements = plan.get("search_elements_json") if isinstance(plan.get("search_elements_json"), dict) else {}
        yield self._format_event("subagent.started", task.id, PHASE_RESULTS_READY, {"name": "feature-comparer"})
        feature_agent = build_feature_comparer_agent()
        result = await asyncio.to_thread(
            feature_agent.invoke,
            {"messages": [{"role": "user", "content": build_feature_prompt(search_elements, selected_documents)}]},
        )
        structured = extract_structured_response(result)
        feature_table_id = uuid.uuid4().hex
        self.storage.create_ai_search_feature_table(
            {
                "feature_table_id": feature_table_id,
                "task_id": task.id,
                "plan_version": plan_version,
                "status": "completed",
                "table_json": structured.get("table_rows") or [],
                "summary_markdown": structured.get("summary_markdown") or "",
            }
        )
        self._append_message(
            task.id,
            "assistant",
            "chat",
            str(structured.get("overall_findings") or "特征对比表已生成。"),
            plan_version=plan_version,
        )
        self._update_phase(
            task.id,
            PHASE_COMPLETED,
            current_feature_table_id=feature_table_id,
            selected_document_count=len(selected_documents),
        )
        yield self._format_event("subagent.completed", task.id, PHASE_COMPLETED, {"name": "feature-comparer"})
        snapshot = self.get_snapshot(task.id, owner_id)
        async for event in self._emit_snapshot_events(snapshot):
            yield event
        yield self._format_event("run.completed", task.id, snapshot.phase, {"featureTableId": feature_table_id})
