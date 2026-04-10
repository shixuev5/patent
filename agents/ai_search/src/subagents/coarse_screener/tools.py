"""粗筛子代理工具。"""

from __future__ import annotations

import json
import uuid
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
                batch_id = uuid.uuid4().hex
                run_id = context.active_run_id(version)
                context.storage.create_ai_search_batch(
                    {
                        "batch_id": batch_id,
                        "run_id": run_id,
                        "task_id": context.task_id,
                        "plan_version": version,
                        "batch_type": "coarse_screen",
                        "status": "loaded",
                    }
                )
                context.storage.replace_ai_search_batch_documents(batch_id, run_id, pending_ids)
                context.update_task_phase("coarse_screen", runtime=runtime, active_plan_version=version, run_id=run_id, active_batch_id=batch_id)
                return json.dumps(
                    {
                        "batch_id": batch_id,
                        "plan_version": version,
                        "search_elements": context.current_search_elements(version),
                        "documents": pending,
                    },
                    ensure_ascii=False,
                )

            if not str(payload_json or "").strip():
                raise ValueError("run_coarse_screen_batch 在 commit 模式下必须提供 payload_json。")
            payload = json.loads(payload_json)
            batch_id = str(payload.get("batch_id") or "").strip()
            batch = context.storage.get_ai_search_batch(batch_id)
            if not batch or str(batch.get("batch_type") or "") != "coarse_screen":
                raise ValueError("coarse_screen commit 缺少有效 batch_id。")
            if str(batch.get("status") or "") == "committed":
                raise ValueError("coarse_screen batch 已提交，不能重复提交。")
            keep_ids = {str(item).strip() for item in (payload.get("keep") or []) if str(item).strip()}
            discard_ids = {str(item).strip() for item in (payload.get("discard") or []) if str(item).strip()}
            current_records = context.storage.list_ai_search_documents(context.task_id, version)
            pending_ids = set(context.storage.list_ai_search_batch_documents(batch_id))
            overlap_ids = keep_ids & discard_ids
            unresolved_ids = pending_ids - keep_ids - discard_ids
            unknown_ids = (keep_ids | discard_ids) - pending_ids
            if overlap_ids:
                raise ValueError(f"coarse_screen 结果中存在重复 document_id: {', '.join(sorted(overlap_ids))}")
            if unresolved_ids:
                raise ValueError(f"coarse_screen 结果遗漏了待处理 document_id: {', '.join(sorted(unresolved_ids))}")
            if unknown_ids:
                raise ValueError(f"coarse_screen 结果包含非待处理 document_id: {', '.join(sorted(unknown_ids))}")
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
                    context.storage.create_ai_search_document_decision(
                        {
                            "decision_id": uuid.uuid4().hex,
                            "run_id": str(batch.get("run_id") or ""),
                            "batch_id": batch_id,
                            "task_id": context.task_id,
                            "plan_version": version,
                            "document_id": document_id,
                            "decision_stage": "coarse_screen",
                            "decision": "kept",
                            "reason": "粗筛保留",
                            "metadata": {"batch_id": batch_id},
                        }
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
                    context.storage.create_ai_search_document_decision(
                        {
                            "decision_id": uuid.uuid4().hex,
                            "run_id": str(batch.get("run_id") or ""),
                            "batch_id": batch_id,
                            "task_id": context.task_id,
                            "plan_version": version,
                            "document_id": document_id,
                            "decision_stage": "coarse_screen",
                            "decision": "discarded",
                            "reason": "粗筛排除",
                            "metadata": {"batch_id": batch_id},
                        }
                    )
                    applied["discarded"] += 1
            context.storage.update_ai_search_batch(batch_id, status="committed", committed_at=utc_now_z())
            context.notify_snapshot_changed(runtime, reason="documents")
            context.update_task_phase("coarse_screen", runtime=runtime, active_plan_version=version, run_id=str(batch.get("run_id") or ""), active_batch_id=batch_id)
            return json.dumps(applied, ensure_ascii=False)
        except Exception as exc:
            return context.record_todo_failure("coarse_screen", str(exc), current_task="coarse_screen", resume_from="run_coarse_screen_batch")

    return [run_coarse_screen_batch]
