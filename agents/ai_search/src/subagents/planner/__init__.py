"""检索规划子代理包。"""


def build_planner_agent(*args, **kwargs):
    from agents.ai_search.src.subagents.planner.agent import build_planner_agent as impl

    return impl(*args, **kwargs)


def build_planner_subagent(*args, **kwargs):
    from agents.ai_search.src.subagents.planner.agent import build_planner_subagent as impl

    return impl(*args, **kwargs)


__all__ = [
    "build_planner_agent",
    "build_planner_subagent",
]
