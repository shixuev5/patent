"""特征对比子代理定义。"""

from __future__ import annotations

from typing import Any

from agents.ai_search.src.subagents.common import StructuredPersistingSubagent
from agents.ai_search.src.subagents.feature_comparer.schemas import FeatureCompareOutput
from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.runtime import build_guard_middleware, large_model
from agents.ai_search.src.subagents.feature_comparer.prompt import FEATURE_COMPARER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.feature_comparer.tools import build_feature_comparer_tools


def _persist_feature_compare(context: object, output: FeatureCompareOutput, *, runtime: Any | None = None) -> None:
    context.persist_feature_compare_result(output.model_dump(mode="python"), runtime=runtime)


def build_feature_comparer_subagent() -> dict:
    return {
        "name": "feature-comparer",
        "description": "基于已选文献和关键证据段落生成特征对比分析结果。",
        "runnable": StructuredPersistingSubagent(
            name="feature-comparer",
            model=large_model(),
            system_prompt=FEATURE_COMPARER_SYSTEM_PROMPT,
            response_format=FeatureCompareOutput,
            persist_result=_persist_feature_compare,
            tools=build_feature_comparer_tools(),
            middleware=[build_guard_middleware()],
            context_schema=AiSearchRuntimeContext,
        ),
    }
