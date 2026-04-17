"""检索要素子代理定义。"""

from __future__ import annotations

from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.runtime import build_guard_middleware, large_model
from agents.ai_search.src.subagents.search_elements.prompt import SEARCH_ELEMENTS_SYSTEM_PROMPT
from agents.ai_search.src.subagents.search_elements.tools import build_search_elements_tools


def build_search_elements_subagent(storage: object, task_id: str) -> dict:
    return {
        "name": "search-elements",
        "description": "根据用户需求整理结构化检索要素，并提取申请人和日期边界。",
        "system_prompt": SEARCH_ELEMENTS_SYSTEM_PROMPT,
        "model": large_model(),
        "tools": build_search_elements_tools(),
        "middleware": [build_guard_middleware()],
        "context_schema": AiSearchRuntimeContext,
    }
