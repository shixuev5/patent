"""Snapshot/read-model collaborator for AI Search."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.orchestration.action_runtime import build_pending_action_view, current_pending_action
from agents.ai_search.src.state import PHASE_COLLECTING_REQUIREMENTS, get_ai_search_meta
from backend.time_utils import utc_now_z

from .models import AiSearchSessionSummary, AiSearchSnapshotResponse


class AiSearchSnapshotService:
    def __init__(self, facade: Any) -> None:
        self.facade = facade
        self.storage = facade.storage

    def _session_summary(self, task: Any) -> AiSearchSessionSummary:
        meta = get_ai_search_meta(task)
        return AiSearchSessionSummary(
            sessionId=task.id,
            taskId=task.id,
            title=str(task.title or "未命名 AI 检索会话"),
            status=task.status.value,
            phase=str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS),
            sourceTaskId=str(meta.get("source_task_id") or "").strip() or None,
            sourceType=str(meta.get("source_type") or "").strip() or None,
            pinned=bool(meta.get("pinned")),
            activePlanVersion=meta.get("active_plan_version"),
            selectedDocumentCount=int(meta.get("selected_document_count") or 0),
            createdAt=utc_now_z() if not getattr(task, "created_at", None) else task.created_at.isoformat(),
            updatedAt=utc_now_z() if not getattr(task, "updated_at", None) else task.updated_at.isoformat(),
        )

    def _display_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        visible_kinds = {"chat", "question", "answer", "plan_confirmation", "process"}
        return [item for item in messages if str(item.get("kind") or "") in visible_kinds]

    def _snapshot_phase(self, snapshot: AiSearchSnapshotResponse) -> str:
        run = snapshot.run if isinstance(snapshot.run, dict) else {}
        session = snapshot.session
        return str(run.get("phase") or getattr(session, "phase", "") or PHASE_COLLECTING_REQUIREMENTS).strip() or PHASE_COLLECTING_REQUIREMENTS

    def _snapshot_messages(self, snapshot: AiSearchSnapshotResponse) -> List[Dict[str, Any]]:
        conversation = snapshot.conversation if isinstance(snapshot.conversation, dict) else {}
        messages = conversation.get("messages")
        return messages if isinstance(messages, list) else []

    def _artifact_download_url(self, snapshot: AiSearchSnapshotResponse) -> Optional[str]:
        artifacts = snapshot.artifacts if isinstance(snapshot.artifacts, dict) else {}
        value = artifacts.get("downloadUrl")
        return str(value).strip() if value else None

    def _active_run(self, task: Any) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if active_plan_version > 0:
            return self.storage.get_ai_search_run(task.id, plan_version=active_plan_version)
        return self.storage.get_ai_search_run(task.id)

    def _current_plan(self, task: Any) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        active_plan_version = meta.get("active_plan_version")
        if active_plan_version:
            plan = self.storage.get_ai_search_plan(task.id, int(active_plan_version))
            if plan:
                return plan
        return self.storage.get_ai_search_plan(task.id)

    def _pending_action(self, task: Any, action_type: str = "") -> Optional[Dict[str, Any]]:
        pending = current_pending_action(self.storage, task_id=task.id)
        if not pending:
            return None
        if action_type and str(pending.get("action_type") or "").strip() != str(action_type or "").strip():
            return None
        return build_pending_action_view(pending, camel_case=True)

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
        if self._snapshot_phase(snapshot) != "drafting_plan":
            return
        pending_action = snapshot.conversation.get("pendingAction") if isinstance(snapshot.conversation, dict) else None
        if isinstance(pending_action, dict):
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
        run = self._active_run(task)
        if not run:
            return [], []
        documents = self.storage.list_ai_search_documents(task.id, str(run.get("run_id") or ""))
        selected = [item for item in documents if str(item.get("stage") or "") == "selected"]
        candidate = [item for item in documents if str(item.get("stage") or "") != "selected"]
        return candidate, selected

    def _latest_assistant_chat(self, task_id: str) -> str:
        messages = self.storage.list_ai_search_messages(task_id)
        for item in reversed(messages):
            if str(item.get("role") or "") == "assistant" and str(item.get("kind") or "") == "chat":
                return str(item.get("content") or "").strip()
        return ""

    def _current_todo(self, task: Any) -> Optional[Dict[str, Any]]:
        return AiSearchAgentContext(self.storage, task.id).current_todo()

    def get_snapshot(self, session_id: str, owner_id: str) -> AiSearchSnapshotResponse:
        task = self.facade.sessions._get_owned_session_task(session_id, owner_id)
        messages = self.storage.list_ai_search_messages(task.id)
        current_plan = self._plan_payload(self._current_plan(task))
        candidate_documents, selected_documents = self._documents_for_snapshot(task)
        meta = get_ai_search_meta(task)
        active_run = self._active_run(task)
        active_plan_version = int(meta.get("active_plan_version") or (active_run.get("plan_version") if active_run else 0) or 0)
        feature_comparison = self.facade.artifacts._current_feature_comparison(task, active_plan_version)
        context = AiSearchAgentContext(self.storage, task.id)
        gap_context = context.latest_gap_context(active_plan_version)
        current_todo = context.current_todo()
        pending_action = self._pending_action(task)
        run_payload = {
            "runId": str(active_run.get("run_id") or "").strip() if active_run else None,
            "phase": str(active_run.get("phase") or meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS) if active_run else str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS),
            "status": str(active_run.get("status") or task.status.value) if active_run else task.status.value,
            "planVersion": int(active_run.get("plan_version") or active_plan_version or 0) if active_run else active_plan_version or None,
            "activeRetrievalTodoId": str(active_run.get("active_retrieval_todo_id") or "").strip() or None if active_run else None,
            "activeBatchId": str(active_run.get("active_batch_id") or "").strip() or None if active_run else None,
            "selectedDocumentCount": int(active_run.get("selected_document_count") or meta.get("selected_document_count") or 0) if active_run else int(meta.get("selected_document_count") or 0),
        }
        return AiSearchSnapshotResponse(
            session=self._session_summary(task),
            run=run_payload,
            conversation={
                "messages": self._display_messages(messages),
                "pendingAction": pending_action,
            },
            plan={"currentPlan": current_plan},
            retrieval={
                "todos": self._execution_todos(task),
                "activeTodo": current_todo,
                "documents": {
                    "candidates": candidate_documents,
                    "selected": selected_documents,
                },
            },
            analysis={
                "activeBatch": self.storage.get_ai_search_batch(str(active_run.get("active_batch_id") or "")) if active_run and str(active_run.get("active_batch_id") or "").strip() else None,
                "latestCloseReadResult": gap_context.get("close_read_result") if isinstance(gap_context.get("close_read_result"), dict) else None,
                "latestFeatureCompareResult": feature_comparison,
            },
            artifacts={"downloadUrl": self.facade.artifacts._snapshot_download_url(task)},
            analysisSeed=self._analysis_seed(task),
        )
