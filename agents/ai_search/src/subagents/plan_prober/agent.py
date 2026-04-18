"""计划预检子代理定义。"""

from __future__ import annotations

from agents.ai_search.src.execution_state import PlanProbeFindings
from agents.ai_search.src.subagents.common import StructuredPersistingSubagent
from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.runtime import build_guard_middleware, default_model
from agents.ai_search.src.subagents.plan_prober.prompt import PLAN_PROBER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.plan_prober.tools import build_plan_prober_tools


def _persist_probe_findings(context: object, output: PlanProbeFindings) -> None:
    context.save_planner_probe_findings(output.model_dump(mode="python"))


def build_plan_prober_subagent() -> dict:
    return {
        "name": "plan-prober",
        "description": "在起草计划阶段做低成本预检，只返回规划信号，不写正式执行结果。",
        "runnable": StructuredPersistingSubagent(
            name="plan-prober",
            model=default_model(),
            system_prompt=PLAN_PROBER_SYSTEM_PROMPT,
            response_format=PlanProbeFindings,
            persist_result=_persist_probe_findings,
            tools=build_plan_prober_tools(),
            middleware=[build_guard_middleware()],
            context_schema=AiSearchRuntimeContext,
        ),
    }
