"""粗筛子代理工具。"""

from __future__ import annotations

import json
import uuid
from typing import Any, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.runtime_context import resolve_agent_context
from agents.ai_search.src.stage_limits import DEFAULT_SHORTLIST_LIMIT


def build_coarse_screener_tools() -> List[Any]:
    def run_coarse_screen_batch(
        operation: str = "load",
        plan_version: int = 0,
        limit: int = DEFAULT_SHORTLIST_LIMIT,
        runtime: ToolRuntime = None,
    ) -> str:
        """读取粗筛待处理批次。"""
        resolved_context = resolve_agent_context(runtime)
        version = int(plan_version or resolved_context.active_plan_version() or 0)
        if str(operation or "load").strip().lower() != "load":
            raise ValueError("run_coarse_screen_batch 仅支持 load 模式。")
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
