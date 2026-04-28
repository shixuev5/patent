"""粗筛子代理定义。"""

from __future__ import annotations

from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.runtime import build_guard_middleware, default_model
from agents.ai_search.src.subagents.coarse_screener.prompt import COARSE_SCREEN_SYSTEM_PROMPT
from agents.ai_search.src.subagents.coarse_screener.tools import build_coarse_screener_tools


def build_coarse_screener_subagent() -> dict:
    return {
        "name": "coarse-screener",
        "description": "根据标题、摘要、分类号和来源批次对候选文献做轻量粗筛。",
        "model": default_model(),
        "system_prompt": COARSE_SCREEN_SYSTEM_PROMPT,
        "tools": build_coarse_screener_tools(),
        "middleware": [build_guard_middleware()],
        "context_schema": AiSearchRuntimeContext,
    }
