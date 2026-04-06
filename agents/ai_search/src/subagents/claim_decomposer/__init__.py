"""Claim decomposer specialist package."""


def build_claim_decomposer_agent(*args, **kwargs):
    from agents.ai_search.src.subagents.claim_decomposer.agent import build_claim_decomposer_agent as impl

    return impl(*args, **kwargs)


def build_claim_decomposer_subagent(*args, **kwargs):
    from agents.ai_search.src.subagents.claim_decomposer.agent import build_claim_decomposer_subagent as impl

    return impl(*args, **kwargs)

__all__ = [
    "build_claim_decomposer_agent",
    "build_claim_decomposer_subagent",
]
