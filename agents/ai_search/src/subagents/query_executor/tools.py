"""Query-executor specialist tools."""

from __future__ import annotations

import json
import uuid
from typing import Any, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.runtime import extract_json_object


def build_query_executor_tools(context: Any) -> List[Any]:
    def run_execution_step(
        operation: str = "load",
        payload_json: str = "",
        plan_version: int = 0,
        runtime: ToolRuntime = None,
    ) -> str:
        """读取当前步骤执行上下文，或提交步骤级执行摘要。"""
        version = int(plan_version or context.active_plan_version() or 0)
        op = str(operation or "load").strip().lower()
        current_todo = context.current_todo() or {}
        todo_id = str(current_todo.get("todo_id") or "").strip()
        try:
            if op != "commit":
                if version <= 0:
                    return json.dumps({"plan_version": 0, "directive": {}, "documents": []}, ensure_ascii=False)
                directive = context.build_execution_step_directive(version)
                if todo_id:
                    context.update_todo(
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
                        "documents": context.storage.list_ai_search_documents(context.task_id, version),
                    },
                    ensure_ascii=False,
                )

            if not str(payload_json or "").strip():
                raise ValueError("run_execution_step 在 commit 模式下必须提供 payload_json。")
            payload = extract_json_object(payload_json)
            payload.setdefault("todo_id", todo_id)
            payload.setdefault("step_id", str(current_todo.get("step_id") or "").strip())
            payload.setdefault("sub_plan_id", str(current_todo.get("sub_plan_id") or "").strip())
            if not str(payload.get("todo_id") or "").strip():
                raise ValueError("execution_step_summary 缺少 todo_id。")
            context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": context.task_id,
                    "plan_version": version or None,
                    "role": "assistant",
                    "kind": "execution_step_summary",
                    "content": json.dumps(payload, ensure_ascii=False),
                    "stream_status": "completed",
                    "metadata": payload,
                }
            )
            if todo_id:
                next_recommendation = str(payload.get("next_recommendation") or "").strip()
                plan_change = payload.get("plan_change_assessment") if isinstance(payload.get("plan_change_assessment"), dict) else {}
                if bool(plan_change.get("requires_replan")):
                    context.update_todo(
                        todo_id,
                        "paused",
                        current_task=None,
                        resume_from="await_plan_confirmation",
                        state_updates={"last_summary": payload, "plan_version": version},
                    )
                elif next_recommendation == "retry_current_step":
                    context.update_todo(
                        todo_id,
                        "in_progress",
                        current_task=todo_id,
                        resume_from="run_execution_step.load",
                        state_updates={"last_summary": payload, "plan_version": version},
                    )
                else:
                    context.update_todo(
                        todo_id,
                        "completed",
                        current_task=todo_id,
                        state_updates={"last_summary": payload, "plan_version": version},
                    )
            context.notify_snapshot_changed(runtime, reason="execution_step_summary")
            return "execution step summary saved"
        except Exception as exc:
            return context.record_todo_failure(
                todo_id,
                str(exc),
                current_task=todo_id,
                resume_from="run_execution_step",
            )

    return [run_execution_step]
