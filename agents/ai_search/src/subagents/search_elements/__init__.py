"""Search elements specialist package."""

from agents.ai_search.src.subagents.search_elements.normalize import normalize_date_text, normalize_search_elements_payload


def build_search_elements_subagent(*args, **kwargs):
    from agents.ai_search.src.subagents.search_elements.agent import build_search_elements_subagent as impl

    return impl(*args, **kwargs)

__all__ = [
    "build_search_elements_subagent",
    "normalize_date_text",
    "normalize_search_elements_payload",
]
