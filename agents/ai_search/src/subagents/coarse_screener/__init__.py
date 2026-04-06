"""Coarse screener specialist package."""


def build_coarse_screener_agent(*args, **kwargs):
    from agents.ai_search.src.subagents.coarse_screener.agent import build_coarse_screener_agent as impl

    return impl(*args, **kwargs)


def build_coarse_screener_subagent(*args, **kwargs):
    from agents.ai_search.src.subagents.coarse_screener.agent import build_coarse_screener_subagent as impl

    return impl(*args, **kwargs)

__all__ = [
    "build_coarse_screener_agent",
    "build_coarse_screener_subagent",
]
