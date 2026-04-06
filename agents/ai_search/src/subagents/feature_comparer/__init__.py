"""Feature comparer specialist package."""

from agents.ai_search.src.subagents.feature_comparer.prompt import build_feature_prompt


def build_feature_comparer_agent(*args, **kwargs):
    from agents.ai_search.src.subagents.feature_comparer.agent import build_feature_comparer_agent as impl

    return impl(*args, **kwargs)


def build_feature_comparer_subagent(*args, **kwargs):
    from agents.ai_search.src.subagents.feature_comparer.agent import build_feature_comparer_subagent as impl

    return impl(*args, **kwargs)

__all__ = [
    "build_feature_comparer_agent",
    "build_feature_comparer_subagent",
    "build_feature_prompt",
]
