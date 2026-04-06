"""AI Search main-agent package."""


def build_main_agent(*args, **kwargs):
    from agents.ai_search.src.main_agent.agent import build_main_agent as impl

    return impl(*args, **kwargs)


__all__ = ["build_main_agent"]
