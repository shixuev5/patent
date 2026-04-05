from __future__ import annotations

from config import settings


_EMBEDDING_SUFFIX = "/compatible-mode/v1"
_RERANK_SUFFIX = "/compatible-api/v1"


def get_retrieval_api_key() -> str:
    return str(settings.RETRIEVAL_API_KEY or settings.LLM_API_KEY or "").strip()


def get_retrieval_base_url(interface: str) -> str:
    raw_value = str(settings.RETRIEVAL_BASE_URL or settings.LLM_BASE_URL or "").strip().rstrip("/")
    if not raw_value:
        return ""

    if raw_value.endswith(_EMBEDDING_SUFFIX):
        if interface == "embedding":
            return raw_value
        return raw_value[: -len(_EMBEDDING_SUFFIX)] + _RERANK_SUFFIX

    if raw_value.endswith(_RERANK_SUFFIX):
        if interface == "rerank":
            return raw_value
        return raw_value[: -len(_RERANK_SUFFIX)] + _EMBEDDING_SUFFIX

    if interface == "embedding":
        return raw_value + _EMBEDDING_SUFFIX
    if interface == "rerank":
        return raw_value + _RERANK_SUFFIX
    raise ValueError(f"Unsupported retrieval interface: {interface}")


def require_retrieval_gateway(interface: str) -> tuple[str, str]:
    api_key = get_retrieval_api_key()
    base_url = get_retrieval_base_url(interface)
    if api_key and base_url:
        return api_key, base_url
    raise ValueError(
        "RETRIEVAL_API_KEY/BASE_URL or LLM_API_KEY/BASE_URL are required for retrieval embeddings and rerank"
    )
