"""特征对比子代理工具。"""

from __future__ import annotations

import json
import uuid
from typing import Any, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.exceptions import ExecutionQueueTakeoverRequested
from agents.ai_search.src.subagents.stage_log_tools import build_stage_log_tools
from agents.ai_search.src.runtime import extract_json_object
from agents.ai_search.src.stage_limits import DEFAULT_SELECTED_LIMIT
from agents.ai_search.src.state import PHASE_FEATURE_COMPARISON
from agents.ai_search.src.subagents.feature_comparer.prompt import build_feature_prompt
from backend.time_utils import utc_now_z


def build_feature_comparer_tools(context: Any) -> List[Any]:
    def run_feature_compare(
        operation: str = "load",
        payload_json: str = "",
        plan_version: int = 0,
        runtime: ToolRuntime = None,
    ) -> str:
        """执行特征对比领域动作：读取对比上下文，或提交对比结果。"""
        version = int(plan_version or context.active_plan_version() or 0)
        op = str(operation or "load").strip().lower()
        try:
            if op != "commit":
                selected_documents = context.storage.list_ai_search_documents(context.task_id, version, stages=["selected"])[:DEFAULT_SELECTED_LIMIT]
                selected_ids = [str(item.get("document_id") or "").strip() for item in selected_documents if str(item.get("document_id") or "").strip()]
                batch_id = uuid.uuid4().hex
                run_id = context.active_run_id(version)
                context.storage.create_ai_search_batch(
                    {
                        "batch_id": batch_id,
                        "run_id": run_id,
                        "task_id": context.task_id,
                        "plan_version": version,
                        "batch_type": "feature_comparison",
                        "status": "loaded",
                    }
                )
                context.storage.replace_ai_search_batch_documents(batch_id, run_id, selected_ids)
                context.update_task_phase(
                    PHASE_FEATURE_COMPARISON,
                    runtime=runtime,
                    active_plan_version=version,
                    run_id=run_id,
                    active_batch_id=batch_id,
                )
                gap_context = context.latest_gap_context()
                return json.dumps(
                    {
                        "batch_id": batch_id,
                        "plan_version": version,
                        "selected_documents": selected_documents,
                        "gap_context": gap_context,
                        "prompt": build_feature_prompt(context.current_search_elements(version), selected_documents, gap_context),
                    },
                    ensure_ascii=False,
                )

            if not str(payload_json or "").strip():
                raise ValueError("run_feature_compare 在 commit 模式下必须提供 payload_json。")
            payload = extract_json_object(payload_json)
            batch_id = str(payload.get("batch_id") or "").strip()
            batch = context.storage.get_ai_search_batch(batch_id)
            if not batch or str(batch.get("batch_type") or "") != "feature_comparison":
                raise ValueError("feature_compare commit 缺少有效 batch_id。")
            if str(batch.get("status") or "") == "committed":
                raise ValueError("feature_compare batch 已提交，不能重复提交。")
            feature_comparison_id = uuid.uuid4().hex
            context.storage.create_ai_search_feature_comparison(
                {
                    "feature_comparison_id": feature_comparison_id,
                    "run_id": str(batch.get("run_id") or ""),
                    "batch_id": batch_id,
                    "task_id": context.task_id,
                    "plan_version": version,
                    "table_rows": payload.get("table_rows") or [],
                    "summary_markdown": payload.get("summary_markdown") or "",
                    "overall_findings": payload.get("overall_findings"),
                    "coverage_gaps": payload.get("coverage_gaps") or [],
                    "difference_highlights": payload.get("difference_highlights") or [],
                    "follow_up_search_hints": payload.get("follow_up_search_hints") or [],
                    "creativity_readiness": payload.get("creativity_readiness"),
                    "readiness_rationale": payload.get("readiness_rationale"),
                }
            )
            findings = str(payload.get("overall_findings") or "特征对比分析结果已生成。").strip()
            if findings:
                context.storage.create_ai_search_message(
                    {
                        "message_id": uuid.uuid4().hex,
                        "task_id": context.task_id,
                        "plan_version": version,
                        "role": "assistant",
                        "kind": "chat",
                        "content": findings,
                        "stream_status": "completed",
                        "metadata": {},
                    }
                )
            context.storage.update_ai_search_batch(batch_id, status="committed", committed_at=utc_now_z())
            context.update_task_phase(
                PHASE_FEATURE_COMPARISON,
                runtime=runtime,
                active_plan_version=version,
                run_id=str(batch.get("run_id") or ""),
                active_batch_id=batch_id,
            )
            takeover = context.consume_execution_message_queue_for_takeover(runtime=runtime)
            if takeover is not None:
                raise takeover
            return json.dumps({"feature_comparison_id": feature_comparison_id}, ensure_ascii=False)
        except ExecutionQueueTakeoverRequested:
            raise
        except Exception as exc:
            return context.record_todo_failure(
                "feature_comparison",
                str(exc),
                current_task="feature_comparison",
                resume_from="run_feature_compare",
            )

    return build_stage_log_tools(context, "feature-comparer") + [run_feature_compare]
