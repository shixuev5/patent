"""特征对比子代理工具。"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.runtime_context import resolve_agent_context
from agents.ai_search.src.stage_limits import DEFAULT_SELECTED_LIMIT
from agents.ai_search.src.state import PHASE_FEATURE_COMPARISON
from agents.ai_search.src.subagents.feature_comparer.schemas import FeatureCompareOutput
from agents.ai_search.src.subagents.feature_comparer.prompt import build_feature_prompt


def build_feature_comparer_tools() -> List[Any]:
    def run_feature_compare(
        operation: str = "load",
        plan_version: int = 0,
        payload: Dict[str, Any] | None = None,
        runtime: ToolRuntime = None,
    ) -> str:
        """读取或提交特征对比上下文。"""
        resolved_context = resolve_agent_context(runtime)
        version = int(plan_version or resolved_context.active_plan_version() or 0)
        normalized_operation = str(operation or "load").strip().lower()
        if normalized_operation not in {"load", "commit"}:
            raise ValueError("run_feature_compare 仅支持 load 或 commit 模式。")
        if normalized_operation == "commit":
            result = FeatureCompareOutput.model_validate(payload or {})
            feature_comparison_id = resolved_context.persist_feature_compare_result(
                result.model_dump(mode="python"),
                plan_version=version,
                runtime=runtime.context if runtime else None,
            )
            return json.dumps(
                {
                    "feature_comparison_id": feature_comparison_id,
                    "coverage_gap_count": len(result.coverage_gaps),
                    "readiness": result.creativity_readiness,
                },
                ensure_ascii=False,
            )
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
