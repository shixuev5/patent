"""Coarse-screener specialist tools."""

from __future__ import annotations

import json
from typing import Any, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.stage_limits import DEFAULT_SHORTLIST_LIMIT
from backend.time_utils import utc_now_z


def build_coarse_screener_tools(context: Any) -> List[Any]:
    def run_coarse_screen_batch(
        operation: str = "load",
        payload_json: str = "",
        plan_version: int = 0,
        limit: int = DEFAULT_SHORTLIST_LIMIT,
        runtime: ToolRuntime = None,
    ) -> str:
        """执行粗筛领域动作：读取待筛批次，或提交粗筛结果。"""
        version = int(plan_version or context.active_plan_version() or 0)
        op = str(operation or "load").strip().lower()
        try:
            if op != "commit":
                records = context.storage.list_ai_search_documents(context.task_id, version)
                pending = [
                    item
                    for item in records
                    if str(item.get("coarse_status") or "pending") == "pending" and str(item.get("stage") or "") in {"candidate", ""}
                ][: max(int(limit or DEFAULT_SHORTLIST_LIMIT), 1)]
                pending_ids = [str(item.get("document_id") or "").strip() for item in pending if str(item.get("document_id") or "").strip()]
                context.update_todos(
                    "coarse_screen",
                    "in_progress",
                    current_task="coarse_screen",
                    resume_from="run_coarse_screen_batch.commit",
                    state_updates={"pending_document_ids": pending_ids, "plan_version": version},
                )
                return json.dumps(
                    {
                        "plan_version": version,
                        "search_elements": context.current_search_elements(version),
                        "documents": pending,
                    },
                    ensure_ascii=False,
                )

            if not str(payload_json or "").strip():
                raise ValueError("run_coarse_screen_batch 在 commit 模式下必须提供 payload_json。")
            payload = json.loads(payload_json)
            keep_ids = {str(item).strip() for item in (payload.get("keep") or []) if str(item).strip()}
            discard_ids = {str(item).strip() for item in (payload.get("discard") or []) if str(item).strip()}
            current_records = context.storage.list_ai_search_documents(context.task_id, version)
            applied = {"kept": 0, "discarded": 0}
            for item in current_records:
                if str(item.get("coarse_status") or "pending") != "pending":
                    continue
                if str(item.get("stage") or "") not in {"candidate", ""}:
                    continue
                document_id = str(item.get("document_id") or "")
                if document_id in keep_ids:
                    context.storage.update_ai_search_document(
                        context.task_id,
                        version,
                        document_id,
                        stage="shortlisted",
                        coarse_status="kept",
                        coarse_reason="粗筛保留",
                        coarse_screened_at=utc_now_z(),
                    )
                    applied["kept"] += 1
                elif document_id in discard_ids:
                    context.storage.update_ai_search_document(
                        context.task_id,
                        version,
                        document_id,
                        stage="rejected",
                        coarse_status="discarded",
                        coarse_reason="粗筛排除",
                        coarse_screened_at=utc_now_z(),
                    )
                    applied["discarded"] += 1
            context.notify_snapshot_changed(runtime, reason="documents")
            context.update_todos(
                "coarse_screen",
                "completed",
                current_task="close_read",
                state_updates={"applied": applied, "kept_ids": sorted(keep_ids), "discard_ids": sorted(discard_ids)},
            )
            return json.dumps(applied, ensure_ascii=False)
        except Exception as exc:
            return context.record_todo_failure("coarse_screen", str(exc), current_task="coarse_screen", resume_from="run_coarse_screen_batch")

    return [run_coarse_screen_batch]
