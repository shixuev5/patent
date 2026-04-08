from __future__ import annotations


def build_plan_prober_agent(*args, **kwargs):
    from agents.ai_search.src.subagents.plan_prober.agent import build_plan_prober_agent as _impl

    return _impl(*args, **kwargs)


def build_plan_prober_subagent(*args, **kwargs):
    from agents.ai_search.src.subagents.plan_prober.agent import build_plan_prober_subagent as _impl

    return _impl(*args, **kwargs)


__all__ = ["build_plan_prober_agent", "build_plan_prober_subagent"]
