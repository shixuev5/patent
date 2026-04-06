"""Query executor specialist package."""


def build_query_executor_agent(*args, **kwargs):
    from agents.ai_search.src.subagents.query_executor.agent import build_query_executor_agent as impl

    return impl(*args, **kwargs)


def build_query_executor_subagent(*args, **kwargs):
    from agents.ai_search.src.subagents.query_executor.agent import build_query_executor_subagent as impl

    return impl(*args, **kwargs)


def build_search_tools(*args, **kwargs):
    from agents.ai_search.src.subagents.query_executor.search_backend_tools import build_search_tools as impl

    return impl(*args, **kwargs)

__all__ = [
    "build_query_executor_agent",
    "build_query_executor_subagent",
    "build_search_tools",
]
