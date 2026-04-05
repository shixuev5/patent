"""
粗筛子 agent 定义。
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend
from pydantic import BaseModel, Field

from agents.ai_search.src.runtime import AiSearchGuardMiddleware, default_model


COARSE_SCREEN_SYSTEM_PROMPT = """
你是 `coarse-screener` 子 agent。

唯一职责：根据标题、摘要、分类号和来源批次，对候选结果做相关性粗筛。
不能读取全文长段落，不能决定最终对比文件。

输出必须为结构化对象：
- keep: 保留的 document_id 列表
- discard: 排除的 document_id 列表
- reasoning_summary: 简短原因摘要
""".strip()


class CoarseScreenOutput(BaseModel):
    keep: List[str] = Field(default_factory=list)
    discard: List[str] = Field(default_factory=list)
    reasoning_summary: str = ""


@lru_cache(maxsize=1)
def build_coarse_screener_agent():
    return create_deep_agent(
        model=default_model(),
        tools=[],
        system_prompt=COARSE_SCREEN_SYSTEM_PROMPT,
        middleware=[AiSearchGuardMiddleware()],
        response_format=CoarseScreenOutput,
        backend=StateBackend,
        name="ai-search-coarse-screener",
    )
