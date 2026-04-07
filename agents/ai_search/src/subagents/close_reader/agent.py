"""Close-reader specialist definition."""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime import build_guard_middleware, build_streaming_middleware, large_model
from agents.ai_search.src.subagents.close_reader.prompt import CLOSE_READER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.close_reader.schemas import CloseReaderOutput


def build_close_reader_agent(storage: object | None = None, task_id: str = ""):
    tools = AiSearchAgentContext(storage, task_id).build_close_reader_tools() if storage and task_id else []
    return create_deep_agent(
        model=large_model(),
        tools=tools,
        system_prompt=CLOSE_READER_SYSTEM_PROMPT,
        middleware=[build_guard_middleware("close-reader", storage, task_id), build_streaming_middleware("close-reader")],
        response_format=CloseReaderOutput,
        backend=StateBackend,
        name="ai-search-close-reader",
    )


def build_close_reader_subagent(storage: object, task_id: str) -> dict:
    context = AiSearchAgentContext(storage, task_id)
    return {
        "name": "close-reader",
        "description": "基于全文证据、claim limitation、权利要求与说明书判断 shortlist 文献是否纳入对比文件。",
        "system_prompt": CLOSE_READER_SYSTEM_PROMPT,
        "model": large_model(),
        "tools": context.build_close_reader_tools(),
        "middleware": [build_guard_middleware("close-reader", storage, task_id), build_streaming_middleware("close-reader")],
    }
