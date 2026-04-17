"""检索执行子代理工具。"""

from __future__ import annotations

import json
import uuid
from typing import Any, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.runtime_context import resolve_agent_context
from agents.ai_search.src.exceptions import ExecutionQueueTakeoverRequested
from agents.ai_search.src.orchestration.execution_runtime import build_step_directive
from agents.ai_search.src.runtime import extract_json_object


def build_query_executor_tools() -> List[Any]:
    def run_execution_step(
        operation: str = "load",
        payload_json: str = "",
        plan_version: int = 0,
        runtime: ToolRuntime = None,
    ) -> str:
        """读取当前步骤执行上下文，或提交步骤级执行摘要。"""
        resolved_context = resolve_agent_context(runtime)
        version = int(plan_version or resolved_context.active_plan_version() or 0)
        op = str(operation or "load").strip().lower()
        current_todo = resolved_context.current_todo() or {}
        todo_id = str(current_todo.get("todo_id") or "").strip()
        try:
            if op != "commit":
                if version <= 0:
                    return json.dumps({"plan_version": 0, "directive": {}, "documents": []}, ensure_ascii=False)
                directive = build_step_directive(resolved_context, version)
                if todo_id:
                    resolved_context.update_todo(
                        todo_id,
                        "in_progress",
                        current_task=todo_id,
                        resume_from="run_execution_step.commit",
                        state_updates={"plan_version": version},
                    )
                return json.dumps(
                    {
                        "plan_version": version,
                        "directive": directive,
                        "documents": resolved_context.storage.list_ai_search_documents(resolved_context.task_id, version),
                    },
                    ensure_ascii=False,
                )

            if not str(payload_json or "").strip():
                raise ValueError("run_execution_step 在 commit 模式下必须提供 payload_json。")
            payload = extract_json_object(payload_json)
            payload.setdefault("todo_id", todo_id)
            payload.setdefault("step_id", str(current_todo.get("step_id") or "").strip())
            payload.setdefault("sub_plan_id", str(current_todo.get("sub_plan_id") or "").strip())
            payload.setdefault(
                "outcome_signals",
                {
                    "primary_goal_reached": False,
                    "recall_quality": "balanced",
                    "triggered_by_adjustment": False,
                },
            )
            if not str(payload.get("todo_id") or "").strip():
                raise ValueError("execution_step_summary 缺少 todo_id。")
            run_id = resolved_context.active_run_id(version)
            resolved_context.storage.create_ai_search_execution_summary(
                {
                    "summary_id": uuid.uuid4().hex,
                    "run_id": run_id,
                    "task_id": resolved_context.task_id,
                    "plan_version": version,
                    "todo_id": str(payload.get("todo_id") or "").strip(),
                    "step_id": str(payload.get("step_id") or "").strip(),
                    "sub_plan_id": str(payload.get("sub_plan_id") or "").strip(),
                    "plan_change_assessment": payload.get("plan_change_assessment") or {},
                    "candidate_pool_size": int(payload.get("candidate_pool_size") or 0),
                    "new_unique_candidates": int(payload.get("new_unique_candidates") or 0),
                    "metadata": payload,
                }
            )
            if todo_id:
                plan_change = payload.get("plan_change_assessment") if isinstance(payload.get("plan_change_assessment"), dict) else {}
                if bool(plan_change.get("requires_replan")):
                    resolved_context.update_todo(
                        todo_id,
                        "paused",
                        current_task=None,
                        resume_from="await_plan_confirmation",
                        state_updates={"last_summary": payload, "plan_version": version},
                    )
                else:
                    resolved_context.update_todo(
                        todo_id,
                        "completed",
                        current_task=todo_id,
                        state_updates={"last_summary": payload, "plan_version": version},
                    )
            resolved_context.notify_snapshot_changed(runtime, reason="execution_step_summary")
            takeover = resolved_context.consume_execution_message_queue_for_takeover(runtime=runtime)
            if takeover is not None:
                raise takeover
            return "execution step summary saved"
        except ExecutionQueueTakeoverRequested:
            raise
        except Exception as exc:
            return resolved_context.record_todo_failure(
                todo_id,
                str(exc),
                current_task=todo_id,
                resume_from="run_execution_step",
            )

    return [run_execution_step]
