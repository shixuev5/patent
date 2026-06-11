"""Analysis-seed collaborator for AI Search."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException

from patent_agents.ai_search.src.state import (
    PHASE_IDLE,
    PHASE_FAILED,
    default_ai_search_meta,
    get_ai_search_meta,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from backend.storage import TaskType
from backend.utils import _build_r2_storage

from patent_agents.ai_search.src.analysis_seed import (
    load_json_bytes,
    load_json_file,
    seed_prompt_from_analysis,
    seed_search_elements_from_analysis,
)
from .models import AiSearchCreateSessionResponse, AiSearchSnapshotResponse


class AiSearchAnalysisSeedService:
    def __init__(self, facade: Any) -> None:
        self.facade = facade

    @property
    def storage(self):
        return self.facade.storage

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
            raise HTTPException(status_code=409, detail="AI 分析结果不存在，暂时无法创建检索会话。")
        return analysis_payload, patent_payload if isinstance(patent_payload, dict) else None

    def _analysis_seed_response(
        self,
        task: Any,
        *,
        reused: bool = False,
        source_task_id: Optional[str] = None,
    ) -> AiSearchCreateSessionResponse:
        meta = get_ai_search_meta(task)
        return AiSearchCreateSessionResponse(
            sessionId=task.id,
            taskId=task.id,
            threadId=str(meta.get("thread_id") or f"ai-search-{task.id}"),
            reused=reused,
            sourceTaskId=source_task_id or str(meta.get("source_task_id") or "").strip() or None,
        )

    def _get_completed_analysis_task(self, owner_id: str, analysis_task_id: str) -> Any:
        analysis_task = self.storage.get_task(str(analysis_task_id or "").strip())
        if (
            not analysis_task
            or str(analysis_task.owner_id or "") != str(owner_id or "")
            or str(analysis_task.task_type or "") != TaskType.PATENT_ANALYSIS.value
        ):
            raise HTTPException(status_code=404, detail="AI 分析任务不存在。")
        if str(getattr(analysis_task.status, "value", analysis_task.status) or "") != "completed":
            raise HTTPException(status_code=409, detail="仅支持从已完成的 AI 分析任务创建检索会话。")
        return analysis_task

    def _find_existing_seed_session(self, owner_id: str, source_task_id: str, *, source_type: str) -> Optional[Any]:
        target_source_task_id = str(source_task_id or "").strip()
        target_source_type = str(source_type or "").strip()
        if not target_source_task_id or not target_source_type:
            return None
        batch_size = 200
        offset = 0
        while True:
            tasks = self.facade.task_manager.list_tasks(owner_id=owner_id, limit=batch_size, offset=offset)
            if not tasks:
                return None
            for task in tasks:
                if str(task.task_type or "") != TaskType.AI_SEARCH.value:
                    continue
                meta = get_ai_search_meta(task)
                if str(meta.get("source_type") or "").strip() != target_source_type:
                    continue
                if str(meta.get("source_task_id") or "").strip() != target_source_task_id:
                    continue
                return task
            if len(tasks) < batch_size:
                return None
            offset += batch_size
        return None

    def _prepare_session_from_analysis(self, owner_id: str, analysis_task_id: str) -> AiSearchCreateSessionResponse:
        analysis_task = self._get_completed_analysis_task(owner_id, analysis_task_id)
        existing_task = self._find_existing_seed_session(owner_id, analysis_task.id, source_type="analysis")
        if existing_task:
            self.facade._emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_reused",
                owner_id=owner_id,
                task_id=existing_task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=True,
                message="复用已存在的 AI 检索会话",
                payload={"analysis_task_id": str(analysis_task.id), "analysis_pn": str(analysis_task.pn or "").strip() or None},
            )
            return self._analysis_seed_response(existing_task, reused=True, source_task_id=str(analysis_task.id))

        self.facade._enforce_daily_quota(owner_id, task_type=TaskType.AI_SEARCH.value)
        self.facade._emit_system_log(
            category="task_execution",
            event_name="ai_search_seed_requested",
            owner_id=owner_id,
            task_id=str(analysis_task.id),
            task_type=TaskType.AI_SEARCH.value,
            success=True,
            message="请求从 AI 分析创建 AI 检索会话",
            payload={"analysis_task_id": str(analysis_task.id), "analysis_pn": str(analysis_task.pn or "").strip() or None},
        )

        analysis_payload, patent_payload = self._load_analysis_artifacts(analysis_task)
        seeded_search_elements = seed_search_elements_from_analysis(analysis_payload, patent_payload)
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
        task = self.facade.task_manager.create_task(
            owner_id=owner_id,
            task_type=TaskType.AI_SEARCH.value,
            title=f"AI 检索会话 - {source_pn or source_title or analysis_task.id}",
        )
        thread_id = f"ai-search-{task.id}"
        seed_meta = default_ai_search_meta(thread_id)
        seed_meta["current_phase"] = PHASE_IDLE
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
            status=phase_to_task_status(PHASE_IDLE),
            progress=phase_progress(PHASE_IDLE),
            current_step=phase_step(PHASE_IDLE),
        )
        self.storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": task.id,
                "role": "assistant",
                "kind": "search_elements_update",
                "content": str(seeded_search_elements.get("objective") or "").strip() or None,
                "stream_status": "completed",
                "metadata": seeded_search_elements,
            }
        )
        return self._analysis_seed_response(task, source_task_id=str(analysis_task.id))

    def _complete_source_seed(self, owner_id: str, session_id: str) -> AiSearchSnapshotResponse:
        task = self.facade.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        seed_prompt = str(meta.get("analysis_seed_prompt") or "").strip()
        source_type = str(meta.get("source_type") or "").strip() or "analysis"
        source_label = "AI 答复" if source_type == "reply" else "AI 分析"
        if not seed_prompt:
            raise HTTPException(status_code=409, detail=f"当前会话缺少 {source_label} 种子上下文。")
        source_task_id = str(meta.get("source_task_id") or "").strip()
        source_pn = str(meta.get("source_pn") or "").strip() or None

        try:
            self.storage.update_task(
                task.id,
                metadata=merge_ai_search_meta(self.storage.get_task(task.id), analysis_seed_status="completed", current_phase=PHASE_IDLE),
                status=phase_to_task_status(PHASE_IDLE),
                progress=phase_progress(PHASE_IDLE),
                current_step=phase_step(PHASE_IDLE),
            )
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
                error_message=f"初始化 AI 检索会话失败：{exc}",
            )
            self.facade._emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_failed",
                owner_id=owner_id,
                task_id=task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=False,
                message=f"从 {source_label} 创建 AI 检索会话失败",
                payload={"analysis_task_id": source_task_id or None, "error": str(exc)},
            )
            self.facade.notify_task_terminal_status(
                task.id,
                PHASE_FAILED,
                error_message=f"初始化 AI 检索会话失败：{exc}",
            )
            raise

        self.storage.update_task(
            task.id,
            metadata=merge_ai_search_meta(self.storage.get_task(task.id), analysis_seed_status="completed"),
        )
        self._reconcile_analysis_seed_phase(task.id)
        snapshot = self.facade.snapshots.get_snapshot(task.id, owner_id)
        self.facade._emit_system_log(
            category="task_execution",
            event_name="ai_search_seed_created",
            owner_id=owner_id,
            task_id=task.id,
            task_type=TaskType.AI_SEARCH.value,
            success=True,
            message=f"已从 {source_label} 创建 AI 检索会话",
            payload={
                "analysis_task_id": source_task_id or None,
                "analysis_pn": source_pn or None,
                "phase": snapshot.session.phase,
            },
        )
        return snapshot

    def _reconcile_analysis_seed_phase(self, task_id: str) -> str:
        task = self.storage.get_task(task_id)
        if not task:
            return PHASE_IDLE
        meta = get_ai_search_meta(task)
        current_phase = str(meta.get("current_phase") or PHASE_IDLE).strip() or PHASE_IDLE
        if current_phase not in {PHASE_IDLE, PHASE_RUNNING}:
            current_phase = PHASE_IDLE
            self.storage.update_task(
                task_id,
                metadata=merge_ai_search_meta(task, current_phase=current_phase),
                status=phase_to_task_status(current_phase),
                progress=phase_progress(current_phase),
                current_step=phase_step(current_phase),
            )
        return current_phase
