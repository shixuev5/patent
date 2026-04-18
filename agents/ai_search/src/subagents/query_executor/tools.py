"""检索执行子代理工具。"""

from __future__ import annotations

import json
from typing import Any, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.orchestration.execution_runtime import build_step_directive
from agents.ai_search.src.runtime_context import resolve_agent_context


def build_query_executor_tools() -> List[Any]:
    def run_execution_step(
        operation: str = "load",
        plan_version: int = 0,
        runtime: ToolRuntime = None,
    ) -> str:
        """读取当前步骤执行上下文。"""
        resolved_context = resolve_agent_context(runtime)
        version = int(plan_version or resolved_context.active_plan_version() or 0)
        current_todo = resolved_context.current_todo() or {}
        todo_id = str(current_todo.get("todo_id") or "").strip()
        if str(operation or "load").strip().lower() != "load":
            raise ValueError("run_execution_step 仅支持 load 模式。")
        if version <= 0:
            return json.dumps({"plan_version": 0, "directive": {}, "documents": []}, ensure_ascii=False)
        directive = build_step_directive(resolved_context, version)
        if todo_id:
            resolved_context.update_todo(
                todo_id,
                "in_progress",
                current_task=todo_id,
                resume_from="run_execution_step.load",
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

    return [run_execution_step]
