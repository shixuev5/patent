"""
特征对比子 agent 定义。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, List

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend
from pydantic import BaseModel, Field

from agents.ai_search.src.runtime import build_guard_middleware, large_model


FEATURE_COMPARER_SYSTEM_PROMPT = """
你是 `feature-comparer` 子 agent。

唯一职责：基于当前 selected 文献和证据段落，输出特征对比表。
不能新增或删除对比文件。

输出必须为结构化对象：
- table_rows
- summary_markdown
- overall_findings
""".strip()


class FeatureCompareOutput(BaseModel):
    table_rows: List[Dict[str, Any]] = Field(default_factory=list)
    summary_markdown: str = ""
    overall_findings: str = ""


@lru_cache(maxsize=1)
def build_feature_comparer_agent():
    return create_deep_agent(
        model=large_model(),
        tools=[],
        system_prompt=FEATURE_COMPARER_SYSTEM_PROMPT,
        middleware=[build_guard_middleware("feature-comparer")],
        response_format=FeatureCompareOutput,
        backend=StateBackend,
        name="ai-search-feature-comparer",
    )


def build_feature_comparer_subagent() -> dict:
    return {
        "name": "feature-comparer",
        "description": "基于已选文献和关键证据段落生成特征对比表。",
        "system_prompt": FEATURE_COMPARER_SYSTEM_PROMPT,
        "model": large_model(),
        "tools": [],
        "middleware": [build_guard_middleware("feature-comparer")],
    }
