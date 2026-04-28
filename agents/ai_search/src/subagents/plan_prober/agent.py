"""计划预检子代理定义。"""

from __future__ import annotations

from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.runtime import build_guard_middleware, default_model
from agents.ai_search.src.subagents.plan_prober.prompt import PLAN_PROBER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.plan_prober.tools import build_plan_prober_tools


def build_plan_prober_subagent() -> dict:
    return {
        "name": "plan-prober",
        "description": "在起草计划阶段做低成本预检，只返回规划信号，不写正式执行结果。",
        "model": default_model(),
        "system_prompt": PLAN_PROBER_SYSTEM_PROMPT,
        "tools": build_plan_prober_tools(),
        "middleware": [build_guard_middleware()],
        "context_schema": AiSearchRuntimeContext,
    }
