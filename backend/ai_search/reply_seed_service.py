"""Reply-seed collaborator for AI Search."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException

from agents.ai_search.src.state import (
    PHASE_DRAFTING_PLAN,
    default_ai_search_meta,
    get_ai_search_meta,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from backend.storage import TaskType
from backend.utils import _build_r2_storage

from .analysis_seed import load_json_bytes, load_json_file
from .models import AiSearchCreateSessionResponse
from .reply_seed import (
    build_execution_spec_from_reply,
    build_reply_seed_user_message,
    seed_prompt_from_reply,
    seed_search_elements_from_reply,
)


class AiSearchReplySeedService:
    def __init__(self, facade: Any) -> None:
        self.facade = facade

    @property
    def storage(self):
        return self.facade.storage

    def _seed_response(
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

    def _load_reply_artifacts(self, task: Any) -> Dict[str, Any]:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        output_files = metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {}

        report_payload = load_json_file(output_files.get("json"))
        if report_payload is None:
            r2_storage = _build_r2_storage()
            report_payload = load_json_bytes(r2_storage.get_bytes(str(output_files.get("ai_reply_r2_key") or "").strip()))

        if not isinstance(report_payload, dict):
            raise HTTPException(status_code=409, detail="AI 答复结果不存在，暂时无法生成检索计划。")
        return report_payload

    def _get_completed_reply_task(self, owner_id: str, reply_task_id: str) -> Any:
        reply_task = self.storage.get_task(str(reply_task_id or "").strip())
        if (
            not reply_task
            or str(reply_task.owner_id or "") != str(owner_id or "")
            or str(reply_task.task_type or "") != TaskType.AI_REPLY.value
        ):
            raise HTTPException(status_code=404, detail="AI 答复任务不存在。")
        if str(getattr(reply_task.status, "value", reply_task.status) or "") != "completed":
            raise HTTPException(status_code=409, detail="仅支持从已完成的 AI 答复任务生成检索计划。")
        return reply_task

    def _find_existing_reply_seed_session(self, owner_id: str, reply_task_id: str) -> Optional[Any]:
        target_source_task_id = str(reply_task_id or "").strip()
        if not target_source_task_id:
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
                if str(meta.get("source_type") or "").strip() != "reply":
                    continue
                if str(meta.get("source_task_id") or "").strip() != target_source_task_id:
                    continue
                return task
            if len(tasks) < batch_size:
                return None
            offset += batch_size
        return None

    def _prepare_session_from_reply(self, owner_id: str, reply_task_id: str) -> AiSearchCreateSessionResponse:
        reply_task = self._get_completed_reply_task(owner_id, reply_task_id)
        reply_payload = self._load_reply_artifacts(reply_task)
        search_followup_section = (
            reply_payload.get("search_followup_section")
            if isinstance(reply_payload.get("search_followup_section"), dict)
            else {}
        )
        if not bool(search_followup_section.get("needed")):
            raise HTTPException(status_code=409, detail="当前 AI 答复报告未生成可用的补检/检索建议。")

        existing_task = self._find_existing_reply_seed_session(owner_id, reply_task.id)
        if existing_task:
            self.facade._emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_reused",
                owner_id=owner_id,
                task_id=existing_task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=True,
                message="复用已存在的 AI 检索计划",
                payload={"reply_task_id": str(reply_task.id), "reply_pn": str(reply_task.pn or "").strip() or None},
            )
            return self._seed_response(existing_task, reused=True, source_task_id=str(reply_task.id))

        self.facade._enforce_daily_quota(owner_id, task_type=TaskType.AI_SEARCH.value)
        self.facade._emit_system_log(
            category="task_execution",
            event_name="ai_search_seed_requested",
            owner_id=owner_id,
            task_id=str(reply_task.id),
            task_type=TaskType.AI_SEARCH.value,
            success=True,
            message="请求从 AI 答复创建 AI 检索计划",
            payload={"reply_task_id": str(reply_task.id), "reply_pn": str(reply_task.pn or "").strip() or None},
        )

        seeded_search_elements = seed_search_elements_from_reply(reply_payload)
        if str(seeded_search_elements.get("status") or "").strip() == "needs_answer":
            raise HTTPException(status_code=409, detail="当前 AI 答复报告中的补检要素不足，暂时无法生成检索计划。")
        seeded_execution_spec = build_execution_spec_from_reply(reply_payload, seeded_search_elements)
        source_pn = str(getattr(reply_task, "pn", "") or "").strip()
        source_title = str(getattr(reply_task, "title", "") or "").strip()
        seed_prompt = seed_prompt_from_reply(reply_payload, seeded_search_elements)
        seed_user_message = build_reply_seed_user_message(reply_payload, seeded_search_elements)
        task = self.facade.task_manager.create_task(
            owner_id=owner_id,
            task_type=TaskType.AI_SEARCH.value,
            title=f"AI 检索计划 - {source_pn or source_title or reply_task.id}",
        )
        thread_id = f"ai-search-{task.id}"
        seed_meta = default_ai_search_meta(thread_id)
        seed_meta["current_phase"] = PHASE_DRAFTING_PLAN
        self.storage.update_task(
            task.id,
            metadata=merge_ai_search_meta(
                task,
                **seed_meta,
                source_type="reply",
                source_task_id=str(reply_task.id),
                source_pn=source_pn or None,
                source_title=source_title or None,
                seed_mode="reply_followup",
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
                "content": str(seeded_search_elements.get("objective") or "").strip() or None,
                "stream_status": "completed",
                "metadata": {
                    **seeded_search_elements,
                    "execution_spec_seed": seeded_execution_spec,
                },
            }
        )
        self.facade._append_message(
            task.id,
            "user",
            "chat",
            seed_user_message,
            metadata={
                "message_variant": "reply_seed_context",
                "render_mode": "markdown",
            },
        )
        return self._seed_response(task, source_task_id=str(reply_task.id))
