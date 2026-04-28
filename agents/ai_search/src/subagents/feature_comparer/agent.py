"""特征对比子代理定义。"""

from __future__ import annotations

from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.runtime import build_guard_middleware, large_model
from agents.ai_search.src.subagents.feature_comparer.prompt import FEATURE_COMPARER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.feature_comparer.tools import build_feature_comparer_tools


def build_feature_comparer_subagent() -> dict:
    return {
        "name": "feature-comparer",
        "description": "基于已选文献和关键证据段落生成特征对比分析结果。",
        "model": large_model(),
        "system_prompt": FEATURE_COMPARER_SYSTEM_PROMPT,
        "tools": build_feature_comparer_tools(),
        "middleware": [build_guard_middleware()],
        "context_schema": AiSearchRuntimeContext,
    }
