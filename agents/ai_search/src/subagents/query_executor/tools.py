"""Query-executor specialist tools."""

from __future__ import annotations

import json
import uuid
from typing import Any, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.execution_state import enrich_execution_round_summary
from agents.ai_search.src.execution_state import decide_search_transition as decide_search_transition_from_summary
from agents.ai_search.src.runtime import extract_json_object


def build_query_executor_tools(context: Any) -> List[Any]:
    def run_search_round(
        operation: str = "load",
        payload_json: str = "",
        plan_version: int = 0,
        runtime: ToolRuntime | None = None,
    ) -> str:
        """执行检索轮次领域动作：读取轮次上下文，或提交本轮摘要。"""
        version = int(plan_version or context.active_plan_version() or 0)
        op = str(operation or "load").strip().lower()
        try:
            if op != "commit":
                if version <= 0:
                    return json.dumps({"plan_version": 0, "directive": {}, "documents": []}, ensure_ascii=False)
                directive = context.build_execution_directive(version)
                context.update_todos(
                    "execute_search",
                    "in_progress",
                    current_task="execute_search",
                    resume_from="run_search_round.commit",
                    state_updates={"last_round_id": directive.get("round_id"), "plan_version": version},
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
                raise ValueError("run_search_round 在 commit 模式下必须提供 payload_json。")
            payload = extract_json_object(payload_json)
            round_id = str(payload.get("round_id") or "").strip()
            existing_summaries = context.list_execution_summaries(version)
            if round_id and any(str(item.get("round_id") or "").strip() == round_id for item in existing_summaries):
                context.update_todos(
                    "execute_search",
                    "in_progress",
                    current_task="execute_search",
                    resume_from="decide_search_transition",
                    state_updates={"last_saved_round_id": round_id, "plan_version": version},
                )
                return json.dumps({"ok": True, "deduped": True, "round_id": round_id}, ensure_ascii=False)

            new_unique_candidates = int(payload.get("new_unique_candidates") or 0)
            candidate_pool_size = int(payload.get("candidate_pool_size") or 0)
            if not str(payload.get("novelty_signal") or "").strip():
                payload["novelty_signal"] = "high" if new_unique_candidates >= 3 else ("low" if new_unique_candidates > 0 else "none")
            if not str(payload.get("coverage_signal") or "").strip():
                payload["coverage_signal"] = "broad" if candidate_pool_size >= 8 else ("emerging" if candidate_pool_size > 0 else "empty")
            if not str(payload.get("recommended_next_action") or "").strip() or not str(payload.get("transition_hint") or "").strip():
                decision = decide_search_transition_from_summary(context.execution_plan_json(version), existing_summaries + [payload])
                payload.setdefault("recommended_next_action", str(decision.get("recommended_action") or ""))
                payload.setdefault("transition_hint", str(decision.get("transition_hint") or ""))
            payload = enrich_execution_round_summary(payload)
            context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": context.task_id,
                    "plan_version": version or None,
                    "role": "assistant",
                    "kind": "execution_summary",
                    "content": json.dumps(payload, ensure_ascii=False),
                    "stream_status": "completed",
                    "metadata": payload,
                }
            )
            context.notify_snapshot_changed(runtime, reason="execution_summary")
            stop_signal = str(payload.get("stop_signal") or "").strip().lower()
            if stop_signal in {"ready_for_screening", "screening_ready", "stop"} or candidate_pool_size >= 8:
                context.update_todos(
                    "execute_search",
                    "completed",
                    current_task="coarse_screen",
                    state_updates={"last_saved_round_id": round_id, "last_summary": payload},
                )
            else:
                context.update_todos(
                    "execute_search",
                    "in_progress",
                    current_task="execute_search",
                    resume_from="decide_search_transition",
                    state_updates={"last_saved_round_id": round_id, "last_summary": payload},
                )
            return "execution summary saved"
        except Exception as exc:
            return context.record_todo_failure("execute_search", str(exc), current_task="execute_search", resume_from="run_search_round")

    return [run_search_round]
