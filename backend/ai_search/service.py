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
from agents.ai_search.src.claim_support import load_structured_claims_from_patent_data
from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime import format_subagent_label
from agents.ai_search.src.state import (
    ACTIVE_EXECUTION_PHASES,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_CLAIM_DECOMPOSITION,
    PHASE_CLOSE_READ,
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_COMPLETED,
    PHASE_COARSE_SCREEN,
    PHASE_DRAFTING_PLAN,
    PHASE_EXECUTE_SEARCH,
    PHASE_FAILED,
    PHASE_GENERATE_FEATURE_TABLE,
    PHASE_SEARCH_STRATEGY,
    SEARCH_MODE_CLAIM_AWARE,
    SEARCH_MODE_TOPIC,
    build_plan_summary,
    default_ai_search_meta,
    get_ai_search_meta,
    get_ai_search_mode,
    latest_search_elements,
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

from .analysis_seed import (
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


task_manager = get_pipeline_manager()

MAIN_AGENT_CHECKPOINT_NS = "ai_search_main"
MAIN_AGENT_PROGRESS_POLL_SECONDS = 15.0
DEFAULT_MESSAGE_PHASES = {
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_DRAFTING_PLAN,
    PHASE_CLAIM_DECOMPOSITION,
    PHASE_SEARCH_STRATEGY,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_COMPLETED,
}

def _has_usable_patent_claims(patent_payload: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(patent_payload, dict):
        return False
    return bool(load_structured_claims_from_patent_data(patent_payload))


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
            pinned=bool(meta.get("pinned")),
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

    def _resume_action(self, task: Any) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or "").strip()
        current_todo = self._current_todo(task)
        if phase not in ACTIVE_EXECUTION_PHASES or not isinstance(current_todo, dict):
            return None
        if str(current_todo.get("status") or "").strip() != "failed":
            return None
        return {
            "available": True,
            "currentTask": str(meta.get("current_task") or "").strip(),
            "taskTitle": str(current_todo.get("title") or "").strip(),
            "resumeFrom": str(current_todo.get("resume_from") or "").strip(),
            "attemptCount": int(current_todo.get("attempt_count") or 0),
            "lastError": str(current_todo.get("last_error") or "").strip(),
        }

    def _source_summary(self, task: Any) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        source_type = str(meta.get("source_type") or "").strip()
        if source_type != "analysis":
            return None
        return {
            "sourceType": source_type,
            "sourceTaskId": str(meta.get("source_task_id") or "").strip(),
            "sourcePn": str(meta.get("source_pn") or "").strip(),
            "sourceTitle": str(meta.get("source_title") or "").strip(),
            "seedMode": str(meta.get("seed_mode") or "").strip(),
            "searchMode": get_ai_search_mode(task),
            "summaryText": "已从 AI 分析结果导入检索上下文，系统已预填检索要素并起草检索草稿。",
        }

    def _load_analysis_artifacts(self, task: Any) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        output_files = metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {}

        analysis_payload = load_json_file(output_files.get("json"))
        patent_payload = None
        if getattr(task, "output_dir", None):
            patent_payload = load_json_file(str(Path(str(task.output_dir)) / "patent.json"))

        if analysis_payload is None or patent_payload is None:
            r2_storage = _build_r2_storage()
            if analysis_payload is None:
                analysis_payload = load_json_bytes(r2_storage.get_bytes(str(output_files.get("analysis_r2_key") or "").strip()))
            if patent_payload is None:
                patent_payload = load_json_bytes(r2_storage.get_bytes(str(output_files.get("patent_r2_key") or "").strip()))

        if not isinstance(analysis_payload, dict):
            raise HTTPException(status_code=409, detail="AI 分析结果不存在，暂时无法生成检索草稿。")
        return analysis_payload, patent_payload if isinstance(patent_payload, dict) else None

    def get_snapshot(self, session_id: str, owner_id: str) -> AiSearchSnapshotResponse:
        task = self._get_owned_session_task(session_id, owner_id)
        messages = self.storage.list_ai_search_messages(task.id)
        current_plan = self._current_plan(task)
        search_elements = latest_search_elements(messages)
        candidate_documents, selected_documents = self._documents_for_snapshot(task)
        feature_table = None
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        current_feature_table_id = str(meta.get("current_feature_table_id") or "").strip()
        if active_plan_version > 0 and current_feature_table_id:
            feature_table = self.storage.get_ai_search_feature_table(
                task.id,
                active_plan_version,
                feature_table_id=current_feature_table_id,
            )
        return AiSearchSnapshotResponse(
            session=self._session_summary(task),
            phase=str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS),
            messages=self._display_messages(messages),
            sourceSummary=self._source_summary(task),
            searchElements=search_elements,
            currentPlan=current_plan,
            candidateDocuments=candidate_documents,
            selectedDocuments=selected_documents,
            featureTable=feature_table,
            pendingQuestion=self._pending_question(task, messages),
            pendingConfirmation=self._pending_confirmation(task, current_plan),
            resumeAction=self._resume_action(task),
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
        messages = self.storage.list_ai_search_messages(task_id)
        for item in reversed(messages):
            if str(item.get("role") or "") == "assistant" and str(item.get("kind") or "") == "chat":
                return str(item.get("content") or "").strip()
        return ""

    def _current_todo(self, task: Any) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        current_task = str(meta.get("current_task") or "").strip()
        todos = meta.get("todos") if isinstance(meta.get("todos"), list) else []
        if not current_task:
            return None
        for item in todos:
            if not isinstance(item, dict):
                continue
            if str(item.get("key") or "").strip() == current_task:
                return item
        return None

    def _resolve_main_checkpoint_ns(self, thread_id: str) -> str:
        checkpoints = self.storage.list_ai_search_checkpoints(thread_id, limit=50)
        for item in checkpoints:
            checkpoint_ns = str(item.get("checkpoint_ns") or "")
            if not checkpoint_ns.startswith("tools:"):
                return checkpoint_ns
        return MAIN_AGENT_CHECKPOINT_NS

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
            "仅围绕当前失败步骤恢复并推进到下一个合法阶段。\n\n"
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

    def create_session_from_analysis(self, owner_id: str, analysis_task_id: str) -> AiSearchCreateSessionResponse:
        _enforce_daily_quota(owner_id, task_type=TaskType.AI_SEARCH.value)
        analysis_task = self.storage.get_task(str(analysis_task_id or "").strip())
        if (
            not analysis_task
            or str(analysis_task.owner_id or "") != str(owner_id or "")
            or str(analysis_task.task_type or "") != TaskType.PATENT_ANALYSIS.value
        ):
            raise HTTPException(status_code=404, detail="AI 分析任务不存在。")
        if str(getattr(analysis_task.status, "value", analysis_task.status) or "") != "completed":
            raise HTTPException(status_code=409, detail="仅支持从已完成的 AI 分析任务生成检索草稿。")

        emit_system_log(
            category="task_execution",
            event_name="ai_search_seed_requested",
            owner_id=owner_id,
            task_id=str(analysis_task.id),
            task_type=TaskType.AI_SEARCH.value,
            success=True,
            message="请求从 AI 分析创建 AI 检索草稿",
            payload={"analysis_task_id": str(analysis_task.id), "analysis_pn": str(analysis_task.pn or "").strip() or None},
        )

        analysis_payload, patent_payload = self._load_analysis_artifacts(analysis_task)
        seeded_search_elements = seed_search_elements_from_analysis(analysis_payload, patent_payload)
        source_pn = str(
            analysis_payload.get("metadata", {}).get("resolved_pn")
            if isinstance(analysis_payload.get("metadata"), dict) else ""
        ).strip() or str(getattr(analysis_task, "pn", "") or "").strip()
        source_title = str(getattr(analysis_task, "title", "") or "").strip()
        search_mode = SEARCH_MODE_CLAIM_AWARE if _has_usable_patent_claims(patent_payload) else SEARCH_MODE_TOPIC
        seed_prompt = seed_prompt_from_analysis(
            analysis_payload,
            patent_payload,
            seeded_search_elements,
            search_mode=search_mode,
        )
        task = task_manager.create_task(
            owner_id=owner_id,
            task_type=TaskType.AI_SEARCH.value,
            title=f"AI 检索草稿 - {source_pn or source_title or analysis_task.id}",
        )
        thread_id = f"ai-search-{task.id}"
        seed_meta = default_ai_search_meta(thread_id)
        seed_meta["current_phase"] = PHASE_DRAFTING_PLAN
        seed_meta["search_mode"] = search_mode
        self.storage.update_task(
            task.id,
            metadata=merge_ai_search_meta(
                task,
                **seed_meta,
                source_type="analysis",
                source_task_id=str(analysis_task.id),
                source_pn=source_pn or None,
                source_title=source_title or None,
                seed_mode="analysis",
            ),
            status=phase_to_task_status(PHASE_DRAFTING_PLAN),
            progress=phase_progress(PHASE_DRAFTING_PLAN),
            current_step=phase_step(PHASE_DRAFTING_PLAN),
        )
        self.storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": task.id,
                "role": "assistant",
                "kind": "search_elements_update",
                "content": str(seeded_search_elements.get("clarification_summary") or "").strip() or None,
                "stream_status": "completed",
                "metadata": seeded_search_elements,
            }
        )
        self._append_message(
            task.id,
            "assistant",
            "chat",
            "已从 AI 分析结果导入检索上下文，正在生成检索草稿。",
        )

        try:
            previous_assistant = self._latest_assistant_chat(task.id)
            result = self._run_main_agent(
                task.id,
                thread_id,
                {"messages": [{"role": "user", "content": seed_prompt}]},
            )
            assistant_text = extract_latest_ai_message(result["values"])
            active_plan_version = int(get_ai_search_meta(self.storage.get_task(task.id)).get("active_plan_version") or 0)
            if assistant_text and assistant_text != previous_assistant:
                self._append_message(task.id, "assistant", "chat", assistant_text, plan_version=active_plan_version or None)
        except Exception as exc:
            self.storage.update_task(
                task.id,
                metadata=merge_ai_search_meta(self.storage.get_task(task.id), current_phase=PHASE_FAILED),
                status=phase_to_task_status(PHASE_FAILED),
                progress=phase_progress(PHASE_FAILED),
                current_step=phase_step(PHASE_FAILED),
                error_message=f"生成 AI 检索草稿失败：{exc}",
            )
            emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_failed",
                owner_id=owner_id,
                task_id=task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=False,
                message="从 AI 分析创建 AI 检索草稿失败",
                payload={"analysis_task_id": str(analysis_task.id), "error": str(exc)},
            )
            raise

        snapshot = self.get_snapshot(task.id, owner_id)
        emit_system_log(
            category="task_execution",
            event_name="ai_search_seed_created",
            owner_id=owner_id,
            task_id=task.id,
            task_type=TaskType.AI_SEARCH.value,
            success=True,
            message="已从 AI 分析创建 AI 检索草稿",
            payload={
                "analysis_task_id": str(analysis_task.id),
                "analysis_pn": source_pn or None,
                "phase": snapshot.phase,
            },
        )
        if snapshot.phase == PHASE_AWAITING_PLAN_CONFIRMATION:
            emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_plan_ready",
                owner_id=owner_id,
                task_id=task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=True,
                message="AI 检索草稿已进入计划确认阶段",
                payload={"analysis_task_id": str(analysis_task.id), "plan_version": snapshot.pendingConfirmation.get("planVersion") if snapshot.pendingConfirmation else None},
            )
        if snapshot.phase == PHASE_AWAITING_USER_ANSWER:
            emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_question_required",
                owner_id=owner_id,
                task_id=task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=True,
                message="AI 检索草稿仍需用户补充信息",
                payload={"analysis_task_id": str(analysis_task.id), "question": snapshot.pendingQuestion.get("prompt") if snapshot.pendingQuestion else None},
            )
        return AiSearchCreateSessionResponse(sessionId=task.id, taskId=task.id, threadId=thread_id)

    def list_sessions(self, owner_id: str) -> AiSearchSessionListResponse:
        tasks = [
            task
            for task in task_manager.list_tasks(owner_id=owner_id, limit=200)
            if str(task.task_type or "") == TaskType.AI_SEARCH.value
        ]
        return AiSearchSessionListResponse(items=[self._session_summary(task) for task in tasks], total=len(tasks))

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
                raise HTTPException(status_code=422, detail="会话标题不能为空。")
            updates["title"] = normalized_title
        if pinned is not None:
            updates["metadata"] = merge_ai_search_meta(task, pinned=bool(pinned))

        if not updates:
            return self._session_summary(task)

        self.storage.update_task(session_id, **updates)
        updated = self._get_owned_session_task(session_id, owner_id)
        return self._session_summary(updated)

    def delete_session(self, session_id: str, owner_id: str) -> Dict[str, bool]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS)
        if phase in ACTIVE_EXECUTION_PHASES:
            raise HTTPException(status_code=409, detail="检索执行中，请稍后再删除会话。")

        task_manager.delete_task(session_id)
        return {"deleted": True}

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
        agent = build_main_agent(self.storage, task_id)
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

    def _init_stream_state(self, snapshot: AiSearchSnapshotResponse, previous_assistant: str) -> Dict[str, Any]:
        known_message_ids = {
            str(item.get("message_id") or "").strip()
            for item in snapshot.messages
            if str(item.get("message_id") or "").strip()
        }
        emitted_phases = {str(snapshot.phase or "").strip()} if str(snapshot.phase or "").strip() else set()
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
                item = await asyncio.wait_for(asyncio.shield(pending), timeout=MAIN_AGENT_PROGRESS_POLL_SECONDS)
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
        phase = current.phase

        if phase and phase not in stream_state["emitted_phases"]:
            stream_state["emitted_phases"].add(phase)
            yield self._format_event("phase.changed", session_id, phase, {"phase": phase})

        for message in current.messages:
            message_id = str(message.get("message_id") or "").strip()
            if message_id and message_id in stream_state["known_message_ids"]:
                continue
            if message_id:
                stream_state["known_message_ids"].add(message_id)
            if str(message.get("role") or "").strip() == "assistant" and str(message.get("kind") or "").strip() == "chat":
                for event in self._assistant_completed_events(
                    session_id,
                    phase,
                    stream_state,
                    str(message.get("content") or ""),
                    message_id=message_id or None,
                ):
                    yield event

        if current.searchElements != previous.searchElements:
            yield self._format_event("search_elements.updated", session_id, phase, current.searchElements)
        if current.currentPlan != previous.currentPlan:
            yield self._format_event("plan.updated", session_id, phase, current.currentPlan)
        if current.pendingQuestion != previous.pendingQuestion and current.pendingQuestion is not None:
            yield self._format_event("question.required", session_id, phase, current.pendingQuestion)
        if current.pendingConfirmation != previous.pendingConfirmation and current.pendingConfirmation is not None:
            yield self._format_event("plan.awaiting_confirmation", session_id, phase, current.pendingConfirmation)
        if current.candidateDocuments != previous.candidateDocuments:
            yield self._format_event(
                "documents.updated",
                session_id,
                phase,
                {"count": len(current.candidateDocuments), "items": current.candidateDocuments},
            )
        if current.selectedDocuments != previous.selectedDocuments:
            yield self._format_event(
                "selection.updated",
                session_id,
                phase,
                {"count": len(current.selectedDocuments), "items": current.selectedDocuments},
            )
        if current.featureTable != previous.featureTable:
            yield self._format_event("feature_table.updated", session_id, phase, current.featureTable)

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
    ) -> AsyncIterator[str]:
        yield self._format_event("run.started", session_id, initial_snapshot.phase, {})
        if previous_phase and previous_phase != initial_snapshot.phase:
            yield self._format_event("phase.changed", session_id, initial_snapshot.phase, {"phase": initial_snapshot.phase})

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
                    snapshot = self.get_snapshot(session_id, owner_id)
                    async for event in self._emit_snapshot_diff_events(
                        stream_state["last_snapshot"],
                        snapshot,
                        stream_state=stream_state,
                    ):
                        yield event
                    stream_state["last_snapshot"] = snapshot
                    continue

                current_phase = self._current_phase_value(task_id, stream_state["last_snapshot"].phase)
                if event_type == "phase.changed":
                    phase = str(event_payload.get("phase") or current_phase).strip() or current_phase
                    if phase and phase not in stream_state["emitted_phases"]:
                        stream_state["emitted_phases"].add(phase)
                        yield self._format_event("phase.changed", session_id, phase, {"phase": phase})
                elif event_type in {"subagent.started", "subagent.completed"}:
                    yield self._format_event(
                        event_type,
                        session_id,
                        current_phase,
                        self._normalize_subagent_payload(event_type, event_payload),
                    )

                snapshot = self.get_snapshot(session_id, owner_id)
                async for event in self._emit_snapshot_diff_events(
                    stream_state["last_snapshot"],
                    snapshot,
                    stream_state=stream_state,
                ):
                    yield event
                stream_state["last_snapshot"] = snapshot
                continue

            if mode != "messages" or not self._is_root_namespace(namespace):
                continue

            delta = self._extract_message_delta(raw_payload)
            if not delta:
                continue
            current_phase = self._current_phase_value(task_id, stream_state["last_snapshot"].phase)
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

    async def _emit_final_assistant_if_needed(self, task_id: str, stream_state: Dict[str, Any]) -> AsyncIterator[str]:
        if stream_state["assistant_completed"]:
            return
        content = str(stream_state.get("assistant_buffer") or "")
        if not content.strip() and stream_state.get("final_values"):
            fallback = extract_latest_ai_message(stream_state["final_values"])
            if fallback and fallback != stream_state.get("previous_assistant"):
                content = fallback
        if not content.strip():
            return

        phase = self._current_phase_value(task_id, stream_state["last_snapshot"].phase)
        message_id = str(stream_state.get("assistant_message_id") or uuid.uuid4().hex).strip()
        self._append_message(
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
        previous_assistant = self._latest_assistant_chat(task.id)
        initial_snapshot = self.get_snapshot(task.id, owner_id)
        stream_state = self._init_stream_state(initial_snapshot, previous_assistant)

        try:
            agent = build_main_agent(self.storage, task.id)
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
                ):
                    yield event
                state = None
                if hasattr(agent, "get_state") and hasattr(agent, "checkpointer"):
                    state = agent.get_state(self._main_agent_state_config(agent, thread_id))
                stream_state["final_values"] = state.values if state is not None else {}
                stream_state["interrupted"] = bool(getattr(state, "interrupts", None)) if state is not None else False
            else:
                yield self._format_event("run.started", task.id, initial_snapshot.phase, {})
                if previous_phase and previous_phase != initial_snapshot.phase:
                    yield self._format_event("phase.changed", task.id, initial_snapshot.phase, {"phase": initial_snapshot.phase})
                result = await asyncio.to_thread(
                    self._run_main_agent,
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

            final_snapshot = self.get_snapshot(task.id, owner_id)
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
                self._current_phase_value(task.id, final_snapshot.phase),
                {"interrupted": stream_state["interrupted"], **completion_payload},
            )
        except Exception as exc:
            yield self._format_event(
                "run.error",
                task.id,
                self._current_phase_value(task.id, initial_snapshot.phase),
                self._stream_error_payload(exc),
            )

    async def _stream_feature_agent_execution(
        self,
        *,
        task: Any,
        owner_id: str,
        plan_version: int,
        previous_phase: str = "",
    ) -> AsyncIterator[str]:
        previous_assistant = self._latest_assistant_chat(task.id)
        initial_snapshot = self.get_snapshot(task.id, owner_id)
        stream_state = self._init_stream_state(initial_snapshot, previous_assistant)
        agent = build_feature_comparer_agent(self.storage, task.id)
        prompt = {
            "messages": [
                {
                    "role": "user",
                    "content": "请基于当前活动计划和已选对比文件生成特征对比表，并使用工具加载上下文后持久化结果。",
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
                yield self._format_event("run.started", task.id, initial_snapshot.phase, {})
                if previous_phase and previous_phase != initial_snapshot.phase:
                    yield self._format_event("phase.changed", task.id, initial_snapshot.phase, {"phase": initial_snapshot.phase})
                current_phase = self._current_phase_value(task.id, initial_snapshot.phase)
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
            refreshed_meta = get_ai_search_meta(refreshed_task)
            feature_table_id = str(refreshed_meta.get("current_feature_table_id") or "").strip()
            progress = AiSearchAgentContext(self.storage, task.id).evaluate_gap_progress_payload(plan_version)
            final_phase = (
                PHASE_COMPLETED
                if str(progress.get("recommended_action") or "").strip() == "complete_execution"
                else PHASE_GENERATE_FEATURE_TABLE
            )
            selected_count = len(self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"]))
            self._update_phase(
                task.id,
                final_phase,
                current_feature_table_id=feature_table_id or None,
                selected_document_count=selected_count,
                current_task=None if final_phase == PHASE_COMPLETED else "generate_feature_table",
            )

            final_snapshot = self.get_snapshot(task.id, owner_id)
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
                self._current_phase_value(task.id, final_snapshot.phase),
                {
                    "interrupted": False,
                    "featureTableId": feature_table_id or None,
                    "recommendedAction": progress.get("recommended_action"),
                },
            )
        except Exception as exc:
            yield self._format_event(
                "run.error",
                task.id,
                self._current_phase_value(task.id, initial_snapshot.phase),
                self._stream_error_payload(exc),
            )

    async def stream_message(self, session_id: str, owner_id: str, content: str) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS)
        if phase in ACTIVE_EXECUTION_PHASES:
            raise HTTPException(
                status_code=409,
                detail={"code": SEARCH_IN_PROGRESS_CODE, "message": "检索执行阶段不支持发送普通消息；如需继续失败步骤，请调用 resume 接口。"},
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
        async for event in self._stream_main_agent_execution(
            task=self.storage.get_task(task.id),
            owner_id=owner_id,
            thread_id=thread_id,
            payload={"messages": [{"role": "user", "content": content}]},
            previous_phase=phase,
        ):
            yield event

    async def stream_resume(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        resume_action = self._require_resume_action(task)
        async for event in self._stream_main_agent_execution(
            task=task,
            owner_id=owner_id,
            thread_id=thread_id,
            payload={"messages": [{"role": "user", "content": self._build_resume_prompt(resume_action)}]},
        ):
            yield event

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
        if phase not in {PHASE_CLOSE_READ, PHASE_GENERATE_FEATURE_TABLE, PHASE_COMPLETED}:
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
        next_phase = PHASE_GENERATE_FEATURE_TABLE if selected_count > 0 else PHASE_CLOSE_READ
        self._update_phase(
            task.id,
            next_phase,
            selected_document_count=selected_count,
            current_feature_table_id=None,
            current_task="generate_feature_table" if selected_count > 0 else "close_read",
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
            self._raise_invalid_phase(PHASE_GENERATE_FEATURE_TABLE, "当前没有已选对比文件。")
        previous_phase = str(meta.get("current_phase") or "")
        self._update_phase(
            task.id,
            PHASE_GENERATE_FEATURE_TABLE,
            active_plan_version=plan_version,
            current_task="generate_feature_table",
        )
        async for event in self._stream_feature_agent_execution(
            task=self.storage.get_task(task.id),
            owner_id=owner_id,
            plan_version=plan_version,
            previous_phase=previous_phase,
        ):
            yield event
