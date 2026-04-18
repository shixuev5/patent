"""特征对比子代理工具。"""

from __future__ import annotations

import json
import uuid
from typing import Any, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.runtime_context import resolve_agent_context
from agents.ai_search.src.stage_limits import DEFAULT_SELECTED_LIMIT
from agents.ai_search.src.state import PHASE_FEATURE_COMPARISON
from agents.ai_search.src.subagents.feature_comparer.prompt import build_feature_prompt


def build_feature_comparer_tools() -> List[Any]:
    def run_feature_compare(
        operation: str = "load",
        plan_version: int = 0,
        runtime: ToolRuntime = None,
    ) -> str:
        """读取特征对比上下文。"""
        resolved_context = resolve_agent_context(runtime)
        version = int(plan_version or resolved_context.active_plan_version() or 0)
        if str(operation or "load").strip().lower() != "load":
            raise ValueError("run_feature_compare 仅支持 load 模式。")
        selected_documents = resolved_context.storage.list_ai_search_documents(resolved_context.task_id, version, stages=["selected"])[:DEFAULT_SELECTED_LIMIT]
        selected_ids = [str(item.get("document_id") or "").strip() for item in selected_documents if str(item.get("document_id") or "").strip()]
        batch_id = uuid.uuid4().hex
        run_id = resolved_context.active_run_id(version)
        resolved_context.storage.create_ai_search_batch(
            {
                "batch_id": batch_id,
                "run_id": run_id,
                "task_id": resolved_context.task_id,
                "plan_version": version,
                "batch_type": "feature_comparison",
                "status": "loaded",
            }
        )
        resolved_context.storage.replace_ai_search_batch_documents(batch_id, run_id, selected_ids)
        resolved_context.update_task_phase(
            PHASE_FEATURE_COMPARISON,
            runtime=runtime,
            active_plan_version=version,
            run_id=run_id,
            active_batch_id=batch_id,
        )
        gap_context = resolved_context.latest_gap_context()
        return json.dumps(
            {
                "batch_id": batch_id,
                "plan_version": version,
                "selected_documents": selected_documents,
                "gap_context": gap_context,
                "prompt": build_feature_prompt(resolved_context.current_search_elements(version), selected_documents, gap_context),
            },
            ensure_ascii=False,
        )

    return [run_feature_compare]
