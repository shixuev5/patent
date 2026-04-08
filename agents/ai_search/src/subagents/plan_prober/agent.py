"""计划预检子代理定义。"""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime import build_guard_middleware, build_streaming_middleware, default_model
from agents.ai_search.src.subagents.plan_prober.prompt import PLAN_PROBER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.plan_prober.schemas import PlanProbeFindings


def build_plan_prober_agent(storage: object, task_id: str):
    context = AiSearchAgentContext(storage, task_id)
    return create_deep_agent(
        model=default_model(),
        tools=context.build_plan_prober_tools(),
        system_prompt=PLAN_PROBER_SYSTEM_PROMPT,
        middleware=[build_guard_middleware("plan-prober", storage, task_id), build_streaming_middleware("plan-prober")],
        response_format=PlanProbeFindings,
        backend=StateBackend,
        name=f"ai-search-plan-prober-{task_id}",
    )


def build_plan_prober_subagent(storage: object, task_id: str) -> dict:
    context = AiSearchAgentContext(storage, task_id)
    return {
        "name": "plan-prober",
        "description": "在起草计划阶段做低成本预检，只返回规划信号，不写正式执行结果。",
        "system_prompt": PLAN_PROBER_SYSTEM_PROMPT,
        "model": default_model(),
        "tools": context.build_plan_prober_tools(),
        "middleware": [build_guard_middleware("plan-prober", storage, task_id), build_streaming_middleware("plan-prober")],
    }
