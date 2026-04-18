"""精读子代理定义。"""

from __future__ import annotations

from typing import Any

from agents.ai_search.src.subagents.close_reader.schemas import CloseReaderOutput
from agents.ai_search.src.subagents.common import StructuredPersistingSubagent
from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.runtime import build_guard_middleware, large_model
from agents.ai_search.src.subagents.close_reader.prompt import CLOSE_READER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.close_reader.tools import build_close_reader_tools


def _persist_close_reader(context: object, output: CloseReaderOutput, *, runtime: Any | None = None) -> None:
    context.persist_close_read_result(output.model_dump(mode="python"), runtime=runtime)


def build_close_reader_subagent() -> dict:
    return {
        "name": "close-reader",
        "description": "基于全文证据、权利要求限制、权利要求与说明书判断候选短名单文献是否纳入对比文件。",
        "runnable": StructuredPersistingSubagent(
            name="close-reader",
            model=large_model(),
            system_prompt=CLOSE_READER_SYSTEM_PROMPT,
            response_format=CloseReaderOutput,
            persist_result=_persist_close_reader,
            tools=build_close_reader_tools(),
            middleware=[build_guard_middleware()],
            context_schema=AiSearchRuntimeContext,
        ),
    }
