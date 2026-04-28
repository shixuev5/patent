"""精读子代理定义。"""

from __future__ import annotations

from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.runtime import build_guard_middleware, large_model
from agents.ai_search.src.subagents.close_reader.prompt import CLOSE_READER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.close_reader.tools import build_close_reader_tools


def build_close_reader_subagent() -> dict:
    return {
        "name": "close-reader",
        "description": "基于全文证据、权利要求限制、权利要求与说明书判断候选短名单文献是否纳入对比文件。",
        "model": large_model(),
        "system_prompt": CLOSE_READER_SYSTEM_PROMPT,
        "tools": build_close_reader_tools(),
        "middleware": [build_guard_middleware()],
        "context_schema": AiSearchRuntimeContext,
    }
