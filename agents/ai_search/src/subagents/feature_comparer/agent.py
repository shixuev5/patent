"""特征对比子代理定义。"""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime import build_guard_middleware, build_streaming_middleware, large_model
from agents.ai_search.src.subagents.feature_comparer.prompt import FEATURE_COMPARER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.feature_comparer.schemas import FeatureCompareOutput


def build_feature_comparer_agent(storage: object | None = None, task_id: str = ""):
    context = AiSearchAgentContext(storage, task_id) if storage and task_id else None
    tools = context.build_feature_comparer_tools() if context is not None else []
    return create_deep_agent(
        model=large_model(),
        tools=tools,
        system_prompt=FEATURE_COMPARER_SYSTEM_PROMPT,
        middleware=[build_guard_middleware("feature-comparer", storage, task_id), build_streaming_middleware("feature-comparer", context=context)],
        response_format=FeatureCompareOutput,
        backend=StateBackend,
        name="ai-search-feature-comparer",
    )


def build_feature_comparer_subagent(storage: object, task_id: str) -> dict:
    context = AiSearchAgentContext(storage, task_id)
    return {
        "name": "feature-comparer",
        "description": "基于已选文献和关键证据段落生成特征对比分析结果。",
        "system_prompt": FEATURE_COMPARER_SYSTEM_PROMPT,
        "model": large_model(),
        "tools": context.build_feature_comparer_tools(),
        "middleware": [build_guard_middleware("feature-comparer", storage, task_id), build_streaming_middleware("feature-comparer", context=context)],
    }
