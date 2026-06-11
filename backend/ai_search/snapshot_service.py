"""Snapshot/read-model collaborator for the free-form AI Search runtime."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.time_utils import utc_now_z
from patent_agents.ai_search.src.state import (
    PHASE_IDLE,
    PHASE_RUNNING,
    get_ai_search_meta,
)

from .models import AiSearchArtifactsPayload, AiSearchSessionSummary, AiSearchSnapshotResponse


class AiSearchSnapshotService:
    def __init__(self, facade: Any) -> None:
        self.facade = facade

    @property
    def storage(self):
        return self.facade.storage

    def _activity_state(self, phase: str) -> str:
        return "running" if str(phase or "").strip() == PHASE_RUNNING else "none"

    def _session_summary(self, task: Any) -> AiSearchSessionSummary:
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or PHASE_IDLE).strip() or PHASE_IDLE
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
            activePlanVersion=int(meta.get("active_plan_version") or 1),
            selectedDocumentCount=int(meta.get("selected_document_count") or 0),
            createdAt=utc_now_z() if not getattr(task, "created_at", None) else task.created_at.isoformat(),
            updatedAt=utc_now_z() if not getattr(task, "updated_at", None) else task.updated_at.isoformat(),
        )

    def _display_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        visible_kinds = {"chat", "question", "answer"}
        return [item for item in messages if str(item.get("kind") or "") in visible_kinds]

    def _stream_state(self, task_id: str) -> Dict[str, Any]:
        latest = self.storage.get_latest_ai_search_stream_event(task_id)
        return {"lastEventSeq": int(latest.get("seq") or 0) if isinstance(latest, dict) else 0}

    def _active_run(self, task: Any) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 1)
        return self.storage.get_ai_search_run(task.id, plan_version=active_plan_version) or self.storage.get_ai_search_run(task.id)

    def _analysis_seed(self, task: Any) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        source_type = str(meta.get("source_type") or "").strip()
        if source_type not in {"analysis", "reply"}:
            return None
        status = str(meta.get("analysis_seed_status") or "").strip() or "completed"
        payload: Dict[str, Any] = {"status": status, "sourceType": source_type}
        source_task_id = str(meta.get("source_task_id") or "").strip()
        if source_task_id:
            payload["sourceTaskId"] = source_task_id
        return payload

    def _documents_for_snapshot(self, task: Any) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        run = self._active_run(task)
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or (run.get("plan_version") if run else 1) or 1)
        documents = self.storage.list_ai_search_documents(task.id, plan_version)
        normalized = [self._snapshot_document_item(item) for item in documents]
        selected = [item for item in normalized if str(item.get("stage") or "") == "selected"]
        candidate = [item for item in normalized if str(item.get("stage") or "") not in {"selected", "rejected"}]
        return candidate, selected

    def _snapshot_document_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(item)
        evidence_ready = bool(
            str(item.get("evidence_summary") or "").strip()
            or (item.get("key_passages_json") if isinstance(item.get("key_passages_json"), list) else [])
            or (item.get("claim_ids_json") if isinstance(item.get("claim_ids_json"), list) else [])
        )
        payload["manualAction"] = "can_remove" if str(item.get("stage") or "") == "selected" else "can_review"
        payload["evidenceReady"] = evidence_ready
        payload["reviewReason"] = (
            str(item.get("close_read_reason") or "").strip()
            or str(item.get("agent_reason") or "").strip()
            or str(item.get("coarse_reason") or "").strip()
            or None
        )
        return payload

    def get_snapshot(self, session_id: str, owner_id: str) -> AiSearchSnapshotResponse:
        task = self.facade.sessions._get_owned_session_task(session_id, owner_id)
        messages = self.storage.list_ai_search_messages(task.id)
        candidate_documents, selected_documents = self._documents_for_snapshot(task)
        meta = get_ai_search_meta(task)
        active_run = self._active_run(task)
        active_plan_version = int(meta.get("active_plan_version") or (active_run.get("plan_version") if active_run else 1) or 1)
        session_phase = str(meta.get("current_phase") or PHASE_IDLE).strip() or PHASE_IDLE
        run_phase = str(active_run.get("phase") or session_phase) if active_run else session_phase
        if session_phase != PHASE_RUNNING and run_phase == PHASE_RUNNING:
            run_phase = session_phase
        run_payload = {
            "runId": str(active_run.get("run_id") or "").strip() if active_run else None,
            "phase": run_phase,
            "status": str(active_run.get("status") or task.status.value) if active_run else task.status.value,
            "planVersion": active_plan_version,
            "activeRetrievalTodoId": None,
            "activeBatchId": None,
            "selectedDocumentCount": int(active_run.get("selected_document_count") or meta.get("selected_document_count") or 0) if active_run else int(meta.get("selected_document_count") or 0),
        }
        return AiSearchSnapshotResponse(
            session=self._session_summary(task),
            run=run_payload,
            conversation={
                "messages": self._display_messages(messages),
                "stopPolicy": meta.get("stop_policy") if isinstance(meta.get("stop_policy"), dict) else {},
            },
            stream=self._stream_state(task.id),
            retrieval={
                "documents": {
                    "candidates": candidate_documents,
                    "selected": selected_documents,
                },
            },
            artifacts=AiSearchArtifactsPayload(attachments=self.facade.artifacts._snapshot_attachments(task)),
            analysisSeed=self._analysis_seed(task),
        )
