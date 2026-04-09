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
        visible_kinds = {"chat", "question", "answer", "plan_confirmation"}
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
        if current_plan and int(current_plan.get("planVersion") or 0) == pending_plan_version:
            return {
                "planVersion": pending_plan_version,
                "confirmationLabel": "实施此计划",
            }
        return None

    def _analysis_seed(self, task: Any) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        if str(meta.get("source_type") or "").strip() != "analysis":
            return None
        status = str(meta.get("analysis_seed_status") or "").strip() or "completed"
        payload: Dict[str, Any] = {"status": status}
        source_task_id = str(meta.get("source_task_id") or "").strip()
        if source_task_id:
            payload["sourceTaskId"] = source_task_id
        return payload

    def _has_planner_draft(self, task: Any) -> bool:
        meta = get_ai_search_meta(task)
        draft = meta.get("planner_draft")
        return isinstance(draft, dict) and bool(str(draft.get("draft_id") or "").strip())

    def _validate_drafting_outcome(self, task_id: str, snapshot: AiSearchSnapshotResponse) -> None:
        if snapshot.phase != PHASE_DRAFTING_PLAN:
            return
        if snapshot.pendingQuestion or snapshot.pendingConfirmation:
            return
        task = self.storage.get_task(task_id)
        if self._has_planner_draft(task):
            return
        raise RuntimeError("drafting_plan 结束时未产生 planner 草案、待追问或待确认状态。")

    def _plan_payload(self, plan: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(plan, dict):
            return None
        return {
            "taskId": str(plan.get("task_id") or "").strip(),
            "planVersion": int(plan.get("plan_version") or 0),
            "status": str(plan.get("status") or "").strip(),
            "reviewMarkdown": str(plan.get("review_markdown") or "").strip(),
            "executionSpec": plan.get("execution_spec_json") if isinstance(plan.get("execution_spec_json"), dict) else {},
            "createdAt": plan.get("created_at"),
            "confirmedAt": plan.get("confirmed_at"),
            "supersededAt": plan.get("superseded_at"),
        }

    def _execution_todos(self, task: Any) -> List[Dict[str, Any]]:
        return AiSearchAgentContext(self.storage, task.id)._current_todos(task)

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

    def _human_decision_action(self, task: Any) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or "").strip()
        if phase != PHASE_AWAITING_HUMAN_DECISION:
            return None
        return {
            "available": True,
            "reason": str(meta.get("human_decision_reason") or "").strip(),
            "summary": str(meta.get("human_decision_summary") or "").strip(),
            "roundCount": int(meta.get("execution_round_count") or 0),
            "noProgressRoundCount": int(meta.get("no_progress_round_count") or 0),
            "selectedCount": int(meta.get("selected_document_count") or 0),
            "recommendedActions": ["continue_search", "complete_current_results"],
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

    def _snapshot_download_url(self, task: Any) -> Optional[str]:
        if str(getattr(task.status, "value", task.status) or "").strip().lower() != PHASE_COMPLETED:
            return None
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        output_files = metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {}
        bundle_zip = str(output_files.get("bundle_zip") or "").strip()
        if not bundle_zip:
            return None
        bundle_path = Path(bundle_zip)
        if not bundle_path.exists() or not bundle_path.is_file():
            return None
        return f"/api/tasks/{task.id}/download"

    def _current_feature_comparison(
        self,
        task: Any,
        plan_version: int,
        *,
        fallback_latest: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if plan_version <= 0:
            return None
        meta = get_ai_search_meta(task)
        current_feature_comparison_id = str(meta.get("current_feature_comparison_id") or "").strip()
        if current_feature_comparison_id:
            table = self.storage.get_ai_search_feature_comparison(
                task.id,
                plan_version,
                feature_comparison_id=current_feature_comparison_id,
            )
            if table:
                return table
        if fallback_latest:
            return self.storage.get_ai_search_feature_comparison(task.id, plan_version)
        return None

    def _finalize_terminal_artifacts(
        self,
        task_id: str,
        plan_version: int,
        *,
        termination_reason: str = "",
    ) -> Dict[str, Any]:
        task = self.storage.get_task(task_id)
        current_plan = self._plan_payload(self.storage.get_ai_search_plan(task_id, plan_version))
        documents = self.storage.list_ai_search_documents(task_id, plan_version)
        feature_comparison = self._current_feature_comparison(task, plan_version, fallback_latest=True)
        context = AiSearchAgentContext(self.storage, task_id)
        gap_context = context.latest_gap_context()
        artifacts = build_ai_search_terminal_artifacts(
            task=task,
            current_plan=current_plan,
            documents=documents,
            feature_comparison=feature_comparison,
            close_read_result=gap_context.get("close_read_result") if isinstance(gap_context.get("close_read_result"), dict) else None,
            feature_compare_result=gap_context.get("feature_compare_result") if isinstance(gap_context.get("feature_compare_result"), dict) else None,
            source_patent_data=context.load_source_patent_data(),
            termination_reason=termination_reason,
        )
        for item in artifacts.get("classified_documents") or []:
            document_id = str(item.get("document_id") or "").strip()
            if not document_id:
                continue
            self.storage.update_ai_search_document(
                task_id,
                plan_version,
                document_id,
                document_type=str(item.get("document_type") or "").strip().upper() or None,
                report_row_order=int(item.get("report_row_order") or 0) or None,
            )

        refreshed_task = self.storage.get_task(task_id)
        metadata = refreshed_task.metadata if isinstance(refreshed_task.metadata, dict) else {}
        output_files = metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {}
        next_output_files = {
            **output_files,
            "pdf": artifacts.get("pdf"),
            "bundle_zip": artifacts.get("bundle_zip"),
        }
        feature_comparison_csv = str(artifacts.get("feature_comparison_csv") or "").strip()
        if feature_comparison_csv:
            next_output_files["feature_comparison_csv"] = feature_comparison_csv
        self.storage.update_task(
            task_id,
            metadata={
                **metadata,
                "output_files": next_output_files,
            },
        )
        return artifacts

    def get_snapshot(self, session_id: str, owner_id: str) -> AiSearchSnapshotResponse:
        task = self._get_owned_session_task(session_id, owner_id)
        messages = self.storage.list_ai_search_messages(task.id)
        current_plan = self._plan_payload(self._current_plan(task))
        candidate_documents, selected_documents = self._documents_for_snapshot(task)
        feature_comparison = None
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        feature_comparison = self._current_feature_comparison(task, active_plan_version)
        return AiSearchSnapshotResponse(
            session=self._session_summary(task),
            phase=str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS),
            messages=self._display_messages(messages),
            downloadUrl=self._snapshot_download_url(task),
            analysisSeed=self._analysis_seed(task),
            humanDecisionAction=self._human_decision_action(task),
            currentPlan=current_plan,
            executionTodos=self._execution_todos(task),
            candidateDocuments=candidate_documents,
            selectedDocuments=selected_documents,
            featureComparison=feature_comparison,
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
            if str(item.get("todo_id") or "").strip() == current_task:
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
        meta = get_ai_search_meta(task)
        reason = str(meta.get("human_decision_reason") or "").strip()
        summary = str(meta.get("human_decision_summary") or "").strip()
        parts = ["人工决策后按当前结果完成"]
        if reason:
            parts.append(f"原因：{reason}")
        if summary:
            parts.append(summary)
        return "；".join(parts)

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

    def _prepare_session_from_analysis(self, owner_id: str, analysis_task_id: str) -> AiSearchCreateSessionResponse:
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
        seeded_execution_spec = build_execution_spec_from_analysis(analysis_payload, patent_payload, seeded_search_elements)
        source_pn = str(
            analysis_payload.get("metadata", {}).get("resolved_pn")
            if isinstance(analysis_payload.get("metadata"), dict) else ""
        ).strip() or str(getattr(analysis_task, "pn", "") or "").strip()
        source_title = str(getattr(analysis_task, "title", "") or "").strip()
        seed_prompt = seed_prompt_from_analysis(
            analysis_payload,
            patent_payload,
            seeded_search_elements,
        )
        seed_user_message = build_analysis_seed_user_message(
            analysis_payload,
            patent_payload,
            seeded_search_elements,
        )
        task = task_manager.create_task(
            owner_id=owner_id,
            task_type=TaskType.AI_SEARCH.value,
            title=f"AI 检索草稿 - {source_pn or source_title or analysis_task.id}",
        )
        thread_id = f"ai-search-{task.id}"
        seed_meta = default_ai_search_meta(thread_id)
        seed_meta["current_phase"] = PHASE_DRAFTING_PLAN
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
                analysis_seed_prompt=seed_prompt,
                analysis_seed_status="pending",
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
                "metadata": {
                    **seeded_search_elements,
                    "execution_spec_seed": seeded_execution_spec,
                },
            }
        )
        self._append_message(
            task.id,
            "user",
            "chat",
            seed_user_message,
        )
        return AiSearchCreateSessionResponse(sessionId=task.id, taskId=task.id, threadId=thread_id)

    def _complete_analysis_seed(self, owner_id: str, session_id: str) -> AiSearchSnapshotResponse:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        seed_prompt = str(meta.get("analysis_seed_prompt") or "").strip()
        if not seed_prompt:
            raise HTTPException(status_code=409, detail="当前会话缺少 AI 分析种子上下文。")
        source_task_id = str(meta.get("source_task_id") or "").strip()
        source_pn = str(meta.get("source_pn") or "").strip() or None

        try:
            result = self._run_main_agent(
                task.id,
                thread_id,
                {"messages": [{"role": "user", "content": seed_prompt}]},
            )
            assistant_text = extract_latest_ai_message(result["values"])
            active_plan_version = int(get_ai_search_meta(self.storage.get_task(task.id)).get("active_plan_version") or 0)
            if assistant_text and not bool(result.get("interrupted")):
                self._append_message(task.id, "assistant", "chat", assistant_text, plan_version=active_plan_version or None)
        except Exception as exc:
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
                payload={"analysis_task_id": source_task_id or None, "error": str(exc)},
            )
            raise

        self.storage.update_task(
            task.id,
            metadata=merge_ai_search_meta(self.storage.get_task(task.id), analysis_seed_status="completed"),
        )
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
                "analysis_task_id": source_task_id or None,
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
                payload={"analysis_task_id": source_task_id or None, "plan_version": snapshot.pendingConfirmation.get("planVersion") if snapshot.pendingConfirmation else None},
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
                payload={"analysis_task_id": source_task_id or None, "question": snapshot.pendingQuestion.get("prompt") if snapshot.pendingQuestion else None},
            )
        return snapshot

    def create_session_from_analysis_seed(self, owner_id: str, analysis_task_id: str) -> AiSearchCreateSessionResponse:
        return self._prepare_session_from_analysis(owner_id, analysis_task_id)

    def create_session_from_analysis(self, owner_id: str, analysis_task_id: str) -> AiSearchCreateSessionResponse:
        created = self._prepare_session_from_analysis(owner_id, analysis_task_id)
        self._complete_analysis_seed(owner_id, created.sessionId)
        return created

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

        if current.currentPlan != previous.currentPlan:
            yield self._format_event("plan.updated", session_id, phase, current.currentPlan)
        if current.executionTodos != previous.executionTodos:
            yield self._format_event("todos.updated", session_id, phase, {"items": current.executionTodos})
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
        if current.featureComparison != previous.featureComparison:
            yield self._format_event("feature_comparison.updated", session_id, phase, current.featureComparison)
        if current.downloadUrl != previous.downloadUrl:
            yield self._format_event("artifacts.updated", session_id, phase, {"downloadUrl": current.downloadUrl})
        if current.humanDecisionAction != previous.humanDecisionAction:
            yield self._format_event("decision.updated", session_id, phase, current.humanDecisionAction)

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

            if mode != "messages" or not self._is_root_namespace(namespace) or not forward_model_text:
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
                    forward_model_text=False,
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
            self._validate_drafting_outcome(task.id, final_snapshot)
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
        force_complete: bool = False,
        termination_reason: str = "",
    ) -> AsyncIterator[str]:
        previous_assistant = self._latest_assistant_chat(task.id)
        initial_snapshot = self.get_snapshot(task.id, owner_id)
        stream_state = self._init_stream_state(initial_snapshot, previous_assistant)
        agent = build_feature_comparer_agent(self.storage, task.id)
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
            feature_comparison_id = str(refreshed_meta.get("current_feature_comparison_id") or "").strip()
            context = AiSearchAgentContext(self.storage, task.id)
            progress = context.evaluate_gap_progress_payload(plan_version)
            round_evaluation = context.commit_round_evaluation(plan_version)
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
                context.enter_human_decision(
                    reason=str(round_evaluation.get("decision_reason") or "no_progress_limit_reached").strip(),
                    summary=summary,
                )
                self._append_message(
                    task.id,
                    "assistant",
                    "chat",
                    summary,
                    plan_version=plan_version or None,
                    metadata={"reason": round_evaluation.get("decision_reason"), "kind": "human_decision"},
                )
                self._update_phase(
                    task.id,
                    final_phase,
                    current_feature_comparison_id=feature_comparison_id or None,
                    selected_document_count=selected_count,
                    current_task=None,
                    human_decision_reason=str(round_evaluation.get("decision_reason") or "").strip() or None,
                    human_decision_summary=summary,
                )
            else:
                self._update_phase(
                    task.id,
                    final_phase,
                    current_feature_comparison_id=feature_comparison_id or None,
                    selected_document_count=selected_count,
                    current_task=None if final_phase == PHASE_COMPLETED else "feature_comparison",
                    human_decision_reason=None if final_phase == PHASE_COMPLETED else refreshed_meta.get("human_decision_reason"),
                    human_decision_summary=None if final_phase == PHASE_COMPLETED else refreshed_meta.get("human_decision_summary"),
                )
            if final_phase == PHASE_COMPLETED:
                self._finalize_terminal_artifacts(task.id, plan_version, termination_reason=termination_reason)

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
                    "featureComparisonId": feature_comparison_id or None,
                    "recommendedAction": progress.get("recommended_action"),
                    "humanDecision": final_phase == PHASE_AWAITING_HUMAN_DECISION,
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
        if phase == PHASE_AWAITING_HUMAN_DECISION:
            self._raise_invalid_phase(phase, "当前处于人工决策状态，请使用继续检索或按当前结果完成。")
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

    async def stream_analysis_seed(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        if str(meta.get("source_type") or "").strip() != "analysis":
            raise HTTPException(status_code=409, detail="当前会话不是从 AI 分析创建的检索草稿。")
        if str(meta.get("analysis_seed_status") or "").strip() != "pending":
            raise HTTPException(status_code=409, detail="当前检索草稿已生成，不能重复初始化。")
        phase = str(meta.get("current_phase") or PHASE_DRAFTING_PLAN)
        seed_prompt = str(meta.get("analysis_seed_prompt") or "").strip()
        if not seed_prompt:
            raise HTTPException(status_code=409, detail="当前会话缺少 AI 分析种子上下文。")

        run_error: Optional[Dict[str, Any]] = None
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
                if str(payload.get("type") or "").strip() == "run.error":
                    maybe_error = payload.get("payload")
                    run_error = maybe_error if isinstance(maybe_error, dict) else {"message": "生成 AI 检索草稿失败。"}
            yield event

        if run_error is not None:
            failure_message = str(run_error.get("message") or "生成 AI 检索草稿失败。").strip()
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
                error_message=f"生成 AI 检索草稿失败：{failure_message}",
            )
            emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_failed",
                owner_id=owner_id,
                task_id=task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=False,
                message="从 AI 分析创建 AI 检索草稿失败",
                payload={"analysis_task_id": str(meta.get("source_task_id") or "").strip() or None, "error": failure_message},
            )
            return

        self.storage.update_task(
            task.id,
            metadata=merge_ai_search_meta(self.storage.get_task(task.id), analysis_seed_status="completed"),
        )
        snapshot = self.get_snapshot(task.id, owner_id)
        source_task_id = str(meta.get("source_task_id") or "").strip() or None
        source_pn = str(meta.get("source_pn") or "").strip() or None
        emit_system_log(
            category="task_execution",
            event_name="ai_search_seed_created",
            owner_id=owner_id,
            task_id=task.id,
            task_type=TaskType.AI_SEARCH.value,
            success=True,
            message="已从 AI 分析创建 AI 检索草稿",
            payload={
                "analysis_task_id": source_task_id,
                "analysis_pn": source_pn,
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
                payload={"analysis_task_id": source_task_id, "plan_version": snapshot.pendingConfirmation.get("planVersion") if snapshot.pendingConfirmation else None},
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
                payload={"analysis_task_id": source_task_id, "question": snapshot.pendingQuestion.get("prompt") if snapshot.pendingQuestion else None},
            )

    async def stream_decision_continue(self, session_id: str, owner_id: str) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        decision_action = self._require_human_decision_action(task)
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 0)
        if plan_version <= 0:
            raise HTTPException(status_code=409, detail="当前没有活动计划版本，无法继续检索。")
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        context = AiSearchAgentContext(self.storage, task.id)
        context.reset_execution_control(plan_version, clear_human_decision=True)
        self._update_phase(
            task.id,
            PHASE_DRAFTING_PLAN,
            current_task=None,
            human_decision_reason=None,
            human_decision_summary=None,
        )
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
        task = self._get_owned_session_task(session_id, owner_id)
        self._require_human_decision_action(task)
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 0)
        if plan_version <= 0:
            raise HTTPException(status_code=409, detail="当前没有活动计划版本，无法按当前结果完成。")
        selected_documents = self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"])
        if not selected_documents:
            raise HTTPException(status_code=409, detail="当前没有已选对比文献，无法按当前结果完成。")

        termination_reason = self._decision_termination_reason(task)
        feature_comparison = self._current_feature_comparison(task, plan_version)
        if feature_comparison is None:
            self._update_phase(
                task.id,
                PHASE_FEATURE_COMPARISON,
                active_plan_version=plan_version,
                current_task="feature_comparison",
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

        previous_assistant = self._latest_assistant_chat(task.id)
        initial_snapshot = self.get_snapshot(task.id, owner_id)
        stream_state = self._init_stream_state(initial_snapshot, previous_assistant)
        yield self._format_event("run.started", task.id, initial_snapshot.phase, {})
        self._update_phase(
            task.id,
            PHASE_COMPLETED,
            active_plan_version=plan_version,
            current_feature_comparison_id=str(meta.get("current_feature_comparison_id") or "").strip() or None,
            selected_document_count=len(selected_documents),
            current_task=None,
            human_decision_reason=None,
            human_decision_summary=None,
        )
        self._finalize_terminal_artifacts(task.id, plan_version, termination_reason=termination_reason)
        final_snapshot = self.get_snapshot(task.id, owner_id)
        async for event in self._emit_snapshot_diff_events(
            stream_state["last_snapshot"],
            final_snapshot,
            stream_state=stream_state,
        ):
            yield event
        yield self._format_event(
            "run.completed",
            task.id,
            self._current_phase_value(task.id, final_snapshot.phase),
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
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if active_plan_version != int(plan_version):
            raise HTTPException(
                status_code=409,
                detail={"code": STALE_PLAN_CONFIRMATION_CODE, "message": "当前只允许操作活动计划版本。"},
            )
        phase = str(meta.get("current_phase") or "")
        if phase not in {PHASE_CLOSE_READ, PHASE_FEATURE_COMPARISON, PHASE_AWAITING_HUMAN_DECISION, PHASE_COMPLETED}:
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
        next_phase = PHASE_FEATURE_COMPARISON if selected_count > 0 else PHASE_CLOSE_READ
        self._update_phase(
            task.id,
            next_phase,
            selected_document_count=selected_count,
            current_feature_comparison_id=None,
            current_task="feature_comparison" if selected_count > 0 else "close_read",
        )
        return self.get_snapshot(task.id, owner_id)

    async def stream_feature_comparison(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if active_plan_version != int(plan_version):
            raise HTTPException(
                status_code=409,
                detail={"code": STALE_PLAN_CONFIRMATION_CODE, "message": "当前只允许生成活动计划版本的特征对比分析结果。"},
            )
        selected_documents = self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"])
        if not selected_documents:
            self._raise_invalid_phase(PHASE_FEATURE_COMPARISON, "当前没有已选对比文件。")
        previous_phase = str(meta.get("current_phase") or "")
        self._update_phase(
            task.id,
            PHASE_FEATURE_COMPARISON,
            active_plan_version=plan_version,
            current_task="feature_comparison",
        )
        async for event in self._stream_feature_agent_execution(
            task=self.storage.get_task(task.id),
            owner_id=owner_id,
            plan_version=plan_version,
            previous_phase=previous_phase,
        ):
            yield event
