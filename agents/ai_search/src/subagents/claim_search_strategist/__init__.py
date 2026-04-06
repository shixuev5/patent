"""Claim search strategist specialist package."""


def build_claim_search_strategist_agent(*args, **kwargs):
    from agents.ai_search.src.subagents.claim_search_strategist.agent import build_claim_search_strategist_agent as impl

    return impl(*args, **kwargs)


def build_claim_search_strategist_subagent(*args, **kwargs):
    from agents.ai_search.src.subagents.claim_search_strategist.agent import build_claim_search_strategist_subagent as impl

    return impl(*args, **kwargs)

__all__ = [
    "build_claim_search_strategist_agent",
    "build_claim_search_strategist_subagent",
]
