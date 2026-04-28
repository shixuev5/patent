"""检索要素子代理工具。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.runtime_context import resolve_agent_context
from agents.ai_search.src.subagents.search_elements.schemas import SearchElementsOutput

def build_search_elements_tools() -> List[Any]:
    def save_search_elements(
        payload: Dict[str, Any],
        runtime: ToolRuntime = None,
    ) -> str:
        """保存结构化检索要素。"""
        resolved_context = resolve_agent_context(runtime)
        normalized = SearchElementsOutput.model_validate(payload).model_dump(mode="python")
        stored = resolved_context.save_search_elements_payload(normalized, runtime=runtime.context if runtime else None)
        return json.dumps(
            {
                "status": str(stored.get("status") or "").strip() or "complete",
                "search_element_count": len(stored.get("search_elements") or []),
                "missing_items": stored.get("missing_items") or [],
            },
            ensure_ascii=False,
        )

    return [save_search_elements]
