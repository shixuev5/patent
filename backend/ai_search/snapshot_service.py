"""Snapshot/read-model collaborator for AI Search."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.orchestration.action_runtime import build_pending_action_view, current_pending_action
from agents.ai_search.src.state import (
    ACTIVE_EXECUTION_PHASES,
    PHASE_AWAITING_HUMAN_DECISION,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_DRAFTING_PLAN,
    get_ai_search_meta,
)
from backend.time_utils import utc_now_z

from .models import AiSearchArtifactsPayload, AiSearchSessionSummary, AiSearchSnapshotResponse


class AiSearchSnapshotService:
    def __init__(self, facade: Any) -> None:
        self.facade = facade

    @property
    def storage(self):
        return self.facade.storage

    def _activity_state(self, phase: str) -> str:
        normalized_phase = str(phase or "").strip()
        if normalized_phase in ({PHASE_DRAFTING_PLAN} | set(ACTIVE_EXECUTION_PHASES)):
            return "running"
        if normalized_phase in {
            PHASE_AWAITING_USER_ANSWER,
            PHASE_AWAITING_PLAN_CONFIRMATION,
            PHASE_AWAITING_HUMAN_DECISION,
        }:
            return "paused"
        return "none"

    def _session_summary(self, task: Any) -> AiSearchSessionSummary:
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS)
        return AiSearchSessionSummary(
            sessionId=task.id,
            taskId=task.id,
            title=str(task.title or "未命名 AI 检索会话"),
            status=task.status.value,
            phase=phase,
            activityState=self._activity_state(phase),
            sourceTaskId=str(meta.get("source_task_id") or "").strip() or None,
            sourceType=str(meta.get("source_type") or "").strip() or None,
            pinned=bool(meta.get("pinned")),
            activePlanVersion=meta.get("active_plan_version"),
            selectedDocumentCount=int(meta.get("selected_document_count") or 0),
            createdAt=utc_now_z() if not getattr(task, "created_at", None) else task.created_at.isoformat(),
            updatedAt=utc_now_z() if not getattr(task, "updated_at", None) else task.updated_at.isoformat(),
        )

    def _display_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        visible_kinds = {"chat", "question", "answer", "plan_confirmation"}
        return [item for item in messages if str(item.get("kind") or "") in visible_kinds]

    def _process_events(self, task_id: str, *, limit: int = 200) -> List[Dict[str, Any]]:
        events = self.storage.list_ai_search_stream_events(task_id, after_seq=0)
        if limit and int(limit) > 0:
            events = events[-int(limit) :]
        flattened: List[Dict[str, Any]] = []
        for item in events:
            if str(item.get("event_type") or "").strip() != "process.event":
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            detail = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
            flattened.append(
                {
                    **detail,
                    "seq": int(item.get("seq") or 0),
                    "createdAt": str(item.get("created_at") or ""),
                    "runId": str(item.get("run_id") or "").strip() or None,
                }
            )
        return flattened

    def _stream_state(self, task_id: str) -> Dict[str, Any]:
        latest = self.storage.get_latest_ai_search_stream_event(task_id)
        return {"lastEventSeq": int(latest.get("seq") or 0) if isinstance(latest, dict) else 0}

    def _snapshot_phase(self, snapshot: AiSearchSnapshotResponse) -> str:
        run = snapshot.run if isinstance(snapshot.run, dict) else {}
        session = snapshot.session
        return str(run.get("phase") or getattr(session, "phase", "") or PHASE_COLLECTING_REQUIREMENTS).strip() or PHASE_COLLECTING_REQUIREMENTS

    def _snapshot_messages(self, snapshot: AiSearchSnapshotResponse) -> List[Dict[str, Any]]:
        conversation = snapshot.conversation if isinstance(snapshot.conversation, dict) else {}
        messages = conversation.get("messages")
        return messages if isinstance(messages, list) else []

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

    def _execution_message_queue(self, task: Any) -> Dict[str, Any]:
        run = self._active_run(task)
        run_id = str(run.get("run_id") or "").strip() if isinstance(run, dict) else ""
        if not run_id:
            return {"items": []}
        items = self.storage.list_ai_search_execution_queue_messages(task.id, run_id, statuses=["pending"])
        return {
            "items": [
                {
                    "queueMessageId": str(item.get("queue_message_id") or "").strip(),
                    "runId": str(item.get("run_id") or "").strip(),
                    "content": str(item.get("content") or ""),
                    "ordinal": int(item.get("ordinal") or 0),
                    "createdAt": item.get("created_at"),
                }
                for item in items
                if str(item.get("queue_message_id") or "").strip()
            ]
        }

    def _has_visible_plan_confirmation(self, snapshot: AiSearchSnapshotResponse) -> bool:
        for item in reversed(self._snapshot_messages(snapshot)):
            if str(item.get("role") or "").strip() != "assistant":
                continue
            if str(item.get("kind") or "").strip() != "plan_confirmation":
                continue
            if str(item.get("content") or "").strip():
                return True
        return False

    def _has_latest_assistant_chat(self, snapshot: AiSearchSnapshotResponse) -> bool:
        messages = self._snapshot_messages(snapshot)
        if not messages:
            return False
        latest = messages[-1]
        return (
            str(latest.get("role") or "").strip() == "assistant"
            and str(latest.get("kind") or "").strip() == "chat"
            and bool(str(latest.get("content") or "").strip())
        )

    def _validate_drafting_outcome(self, task_id: str, snapshot: AiSearchSnapshotResponse) -> None:
        if self._snapshot_phase(snapshot) != "drafting_plan":
            return
        pending_action = snapshot.conversation.get("pendingAction") if isinstance(snapshot.conversation, dict) else None
        if isinstance(pending_action, dict):
            return
        if self._has_visible_plan_confirmation(snapshot):
            return
        if self._has_latest_assistant_chat(snapshot):
            return
        raise RuntimeError("drafting_plan 结束时未产生待追问或待确认状态。")

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
        active_batch_id = str(run.get("active_batch_id") or "").strip()
        active_batch = self.storage.get_ai_search_batch(active_batch_id) if active_batch_id else None
        active_batch_type = str(active_batch.get("batch_type") or "").strip() if isinstance(active_batch, dict) else ""
        active_batch_documents = set(self.storage.list_ai_search_batch_documents(active_batch_id)) if active_batch_id else set()
        phase = str((self._session_summary(task).phase or "")).strip()
        normalized = [
            self._snapshot_document_item(
                item,
                phase=phase,
                active_batch_type=active_batch_type,
                active_batch_documents=active_batch_documents,
            )
            for item in documents
        ]
        selected = [item for item in normalized if str(item.get("stage") or "") == "selected"]
        candidate = [item for item in normalized if str(item.get("stage") or "") != "selected"]
        return candidate, selected

    def _snapshot_document_item(
        self,
        item: Dict[str, Any],
        *,
        phase: str,
        active_batch_type: str,
        active_batch_documents: set[str],
    ) -> Dict[str, Any]:
        payload = dict(item)
        document_id = str(item.get("document_id") or "").strip()
        stage = str(item.get("stage") or "").strip()
        evidence_ready = bool(
            str(item.get("evidence_summary") or "").strip()
            or (item.get("key_passages_json") if isinstance(item.get("key_passages_json"), list) else [])
            or (item.get("claim_ids_json") if isinstance(item.get("claim_ids_json"), list) else [])
        )
        manual_action = "none"
        if active_batch_type == "close_read" and document_id and document_id in active_batch_documents:
            manual_action = "review_requested"
        elif phase == "awaiting_human_decision":
            if stage == "selected":
                manual_action = "can_remove"
            elif stage == "shortlisted":
                manual_action = "can_review"
        payload["manualAction"] = manual_action
        payload["evidenceReady"] = evidence_ready
        payload["reviewReason"] = (
            str(item.get("close_read_reason") or "").strip()
            or str(item.get("agent_reason") or "").strip()
            or str(item.get("coarse_reason") or "").strip()
            or None
        )
        return payload

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
                "processEvents": self._process_events(task.id),
            },
            stream=self._stream_state(task.id),
            executionMessageQueue=self._execution_message_queue(task),
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
            artifacts=AiSearchArtifactsPayload(attachments=self.facade.artifacts._snapshot_attachments(task)),
            analysisSeed=self._analysis_seed(task),
        )
