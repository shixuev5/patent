"""Close reader specialist package."""

from agents.ai_search.src.subagents.close_reader.passages import collect_key_terms, fallback_passages
from agents.ai_search.src.subagents.close_reader.prompt import build_close_reader_prompt


def build_close_reader_agent(*args, **kwargs):
    from agents.ai_search.src.subagents.close_reader.agent import build_close_reader_agent as impl

    return impl(*args, **kwargs)


def build_close_reader_subagent(*args, **kwargs):
    from agents.ai_search.src.subagents.close_reader.agent import build_close_reader_subagent as impl

    return impl(*args, **kwargs)


def detail_fingerprint(*args, **kwargs):
    from agents.ai_search.src.subagents.close_reader.workspace import detail_fingerprint as impl

    return impl(*args, **kwargs)


def detail_to_text(*args, **kwargs):
    from agents.ai_search.src.subagents.close_reader.workspace import detail_to_text as impl

    return impl(*args, **kwargs)


def load_document_details(*args, **kwargs):
    from agents.ai_search.src.subagents.close_reader.workspace import load_document_details as impl

    return impl(*args, **kwargs)


def prepare_close_read_workspace(*args, **kwargs):
    from agents.ai_search.src.subagents.close_reader.workspace import prepare_close_read_workspace as impl

    return impl(*args, **kwargs)

__all__ = [
    "build_close_reader_agent",
    "build_close_reader_subagent",
    "build_close_reader_prompt",
    "collect_key_terms",
    "detail_fingerprint",
    "detail_to_text",
    "fallback_passages",
    "load_document_details",
    "prepare_close_read_workspace",
]
