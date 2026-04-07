"""Search-elements specialist tools."""

from __future__ import annotations

import uuid
from typing import Any, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.runtime import extract_json_object
from agents.ai_search.src.state import PHASE_DRAFTING_PLAN
from agents.ai_search.src.subagents.search_elements.normalize import normalize_search_elements_payload


def build_search_elements_tools(context: Any) -> List[Any]:
    def save_search_elements(payload_json: str, runtime: ToolRuntime = None) -> str:
        """保存结构化检索要素。"""
        payload = normalize_search_elements_payload(extract_json_object(payload_json))
        context.storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": context.task_id,
                "role": "assistant",
                "kind": "search_elements_update",
                "content": str(payload.get("clarification_summary") or payload.get("objective") or "").strip() or None,
                "stream_status": "completed",
                "metadata": payload,
            }
        )
        context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime)
        return "search elements updated"

    return [save_search_elements]
