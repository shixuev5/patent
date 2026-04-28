"""粗筛子代理工具。"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.runtime_context import resolve_agent_context
from agents.ai_search.src.stage_limits import DEFAULT_SHORTLIST_LIMIT
from agents.ai_search.src.subagents.coarse_screener.schemas import CoarseScreenOutput


def build_coarse_screener_tools() -> List[Any]:
    def run_coarse_screen_batch(
        operation: str = "load",
        plan_version: int = 0,
        limit: int = DEFAULT_SHORTLIST_LIMIT,
        payload: Dict[str, Any] | None = None,
        runtime: ToolRuntime = None,
    ) -> str:
        """读取或提交粗筛待处理批次。"""
        resolved_context = resolve_agent_context(runtime)
        version = int(plan_version or resolved_context.active_plan_version() or 0)
        normalized_operation = str(operation or "load").strip().lower()
        if normalized_operation not in {"load", "commit"}:
            raise ValueError("run_coarse_screen_batch 仅支持 load 或 commit 模式。")
        if normalized_operation == "commit":
            result = CoarseScreenOutput.model_validate(payload or {})
            applied = resolved_context.persist_coarse_screen_result(
                result.keep,
                result.discard,
                plan_version=version,
                runtime=runtime.context if runtime else None,
            )
            return json.dumps(
                {
                    "kept": int(applied.get("kept") or 0),
                    "discarded": int(applied.get("discarded") or 0),
                },
                ensure_ascii=False,
            )
        records = resolved_context.storage.list_ai_search_documents(resolved_context.task_id, version)
        pending = [
            item
            for item in records
            if str(item.get("coarse_status") or "pending") == "pending" and str(item.get("stage") or "") in {"candidate", ""}
        ][: max(int(limit or DEFAULT_SHORTLIST_LIMIT), 1)]
        pending_ids = [str(item.get("document_id") or "").strip() for item in pending if str(item.get("document_id") or "").strip()]
        batch_id = uuid.uuid4().hex
        run_id = resolved_context.active_run_id(version)
        resolved_context.storage.create_ai_search_batch(
            {
                "batch_id": batch_id,
                "run_id": run_id,
                "task_id": resolved_context.task_id,
                "plan_version": version,
                "batch_type": "coarse_screen",
                "status": "loaded",
            }
        )
        resolved_context.storage.replace_ai_search_batch_documents(batch_id, run_id, pending_ids)
        resolved_context.update_task_phase("coarse_screen", runtime=runtime, active_plan_version=version, run_id=run_id, active_batch_id=batch_id)
        return json.dumps(
            {
                "batch_id": batch_id,
                "plan_version": version,
                "search_elements": resolved_context.current_search_elements(version),
                "documents": pending,
            },
            ensure_ascii=False,
        )

    return [run_coarse_screen_batch]
