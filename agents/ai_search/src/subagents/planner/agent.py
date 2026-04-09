"""检索规划子代理定义。"""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime import build_guard_middleware, build_streaming_middleware, large_model
from agents.ai_search.src.subagents.planner.prompt import PLANNER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.planner.schemas import PlannerDraftOutput


def build_planner_agent(storage: object, task_id: str):
    context = AiSearchAgentContext(storage, task_id)
    return create_deep_agent(
        model=large_model(),
        tools=context.build_planner_tools(),
        system_prompt=PLANNER_SYSTEM_PROMPT,
        middleware=[build_guard_middleware("planner", storage, task_id), build_streaming_middleware("planner")],
        response_format=PlannerDraftOutput,
        backend=StateBackend,
        name=f"ai-search-planner-{task_id}",
    )


def build_planner_subagent(storage: object, task_id: str) -> dict:
    context = AiSearchAgentContext(storage, task_id)
    return {
        "name": "planner",
        "description": "根据检索要素、gap 上下文和可选预检信号生成正式检索计划草案，并提交到中间状态。",
        "system_prompt": PLANNER_SYSTEM_PROMPT,
        "model": large_model(),
        "tools": context.build_planner_tools(),
        "middleware": [build_guard_middleware("planner", storage, task_id), build_streaming_middleware("planner")],
    }
