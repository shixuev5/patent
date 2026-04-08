"""检索要素子代理定义。"""

from __future__ import annotations

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime import build_guard_middleware, build_streaming_middleware, large_model
from agents.ai_search.src.subagents.search_elements.prompt import SEARCH_ELEMENTS_SYSTEM_PROMPT


def build_search_elements_subagent(storage: object, task_id: str) -> dict:
    context = AiSearchAgentContext(storage, task_id)
    return {
        "name": "search-elements",
        "description": "根据用户需求整理结构化检索要素，并提取申请人和日期边界。",
        "system_prompt": SEARCH_ELEMENTS_SYSTEM_PROMPT,
        "model": large_model(),
        "tools": context.build_search_elements_tools(),
        "middleware": [build_guard_middleware("search-elements", storage, task_id), build_streaming_middleware("search-elements")],
    }
