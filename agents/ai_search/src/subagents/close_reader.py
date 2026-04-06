"""
精读子 agent 定义。
"""

from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from pydantic import BaseModel, Field

from agents.ai_search.src.runtime import build_guard_middleware, large_model


CLOSE_READER_SYSTEM_PROMPT = """
你是 `close-reader` 子 agent。

唯一职责：根据检索要素、候选文献详情和全文证据，判断 shortlisted 文献是否应纳入对比文件。
必须基于证据作出判断。

你可以使用只读文件系统工具：
- `ls`
- `glob`
- `grep`
- `read_file`

严禁写文件、编辑文件或执行命令。
优先在提供的 workspace 中用关键词定位证据段落，再结合标题、摘要、权利要求和说明书做判断。

输出必须为结构化对象：
- selected
- rejected
- key_passages
- selection_summary
""".strip()


class KeyPassageOutput(BaseModel):
    document_id: str
    passage: str
    reason: str = ""
    location: Optional[str] = None


class CloseReaderOutput(BaseModel):
    selected: List[str] = Field(default_factory=list)
    rejected: List[str] = Field(default_factory=list)
    key_passages: List[KeyPassageOutput] = Field(default_factory=list)
    selection_summary: str = ""


@lru_cache(maxsize=1)
def build_close_reader_agent():
    return create_deep_agent(
        model=large_model(),
        tools=[],
        system_prompt=CLOSE_READER_SYSTEM_PROMPT,
        middleware=[
            build_guard_middleware("close-reader"),
            FilesystemMiddleware(),
        ],
        response_format=CloseReaderOutput,
        backend=StateBackend,
        name="ai-search-close-reader",
    )


def build_close_reader_subagent() -> dict:
    return {
        "name": "close-reader",
        "description": "基于全文证据、权利要求与说明书判断 shortlist 文献是否纳入对比文件。",
        "system_prompt": CLOSE_READER_SYSTEM_PROMPT,
        "model": large_model(),
        "tools": [],
        "middleware": [
            build_guard_middleware("close-reader"),
            FilesystemMiddleware(),
        ],
    }
