"""检索规划子代理定义。"""

from __future__ import annotations

from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.runtime import build_guard_middleware, large_model
from agents.ai_search.src.subagents.planner.prompt import PLANNER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.planner.tools import build_planner_tools


def build_planner_subagent() -> dict:
    return {
        "name": "planner",
        "description": "根据检索要素、gap 上下文和可选预检信号生成正式检索计划草案，并提交到中间状态。",
        "model": large_model(),
        "system_prompt": PLANNER_SYSTEM_PROMPT,
        "tools": build_planner_tools(),
        "middleware": [build_guard_middleware()],
        "context_schema": AiSearchRuntimeContext,
    }
