"""Feature-comparer specialist tools."""

from __future__ import annotations

import json
import uuid
from typing import Any, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.runtime import extract_json_object
from agents.ai_search.src.stage_limits import DEFAULT_SELECTED_LIMIT
from agents.ai_search.src.state import PHASE_GENERATE_FEATURE_TABLE
from agents.ai_search.src.subagents.feature_comparer.prompt import build_feature_prompt


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
                context.update_todos(
                    "generate_feature_table",
                    "in_progress",
                    current_task="generate_feature_table",
                    resume_from="run_feature_compare.commit",
                    state_updates={"selected_document_ids": selected_ids, "plan_version": version},
                )
                gap_context = context.latest_gap_context()
                return json.dumps(
                    {
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
            feature_table_id = uuid.uuid4().hex
            context.storage.create_ai_search_feature_table(
                {
                    "feature_table_id": feature_table_id,
                    "task_id": context.task_id,
                    "plan_version": version,
                    "status": "completed",
                    "table_json": payload.get("table_rows") or [],
                    "summary_markdown": payload.get("summary_markdown") or "",
                }
            )
            context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": context.task_id,
                    "plan_version": version,
                    "role": "assistant",
                    "kind": "feature_compare_result",
                    "content": str(payload.get("readiness_rationale") or payload.get("overall_findings") or "").strip() or None,
                    "stream_status": "completed",
                    "metadata": payload,
                }
            )
            findings = str(payload.get("overall_findings") or "特征对比表已生成。").strip()
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
            context.update_task_phase(
                PHASE_GENERATE_FEATURE_TABLE,
                runtime=runtime,
                active_plan_version=version,
                current_feature_table_id=feature_table_id,
                current_task="generate_feature_table",
            )
            context.update_todos(
                "generate_feature_table",
                "completed",
                current_task="generate_feature_table",
                state_updates={"feature_table_id": feature_table_id},
            )
            return json.dumps({"feature_table_id": feature_table_id}, ensure_ascii=False)
        except Exception as exc:
            return context.record_todo_failure(
                "generate_feature_table",
                str(exc),
                current_task="generate_feature_table",
                resume_from="run_feature_compare",
            )

    return [run_feature_compare]
