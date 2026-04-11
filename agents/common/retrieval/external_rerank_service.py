from __future__ import annotations

import time
from typing import Any, Dict, List, Sequence

from openai import OpenAI
import requests

from agents.common.retrieval.gateway import require_retrieval_gateway
from config import settings


class ExternalEvidenceRerankError(RuntimeError):
    """Raised when the external rerank request cannot produce a usable result."""


class ExternalEvidenceRerankService:
    _TIMEOUT_SECONDS = 20.0
    _MAX_RETRIES = 3
    _RETRY_BACKOFF_SECONDS = 0.2

    def __init__(self):
        api_key, base_url = require_retrieval_gateway("rerank")
        self.model = str(settings.RETRIEVAL_RERANK_MODEL or "").strip() or "qwen3-rerank"
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self._base_url = str(self.client.base_url).rstrip("/")

    def rerank(self, query: str, documents: Sequence[str]) -> List[Dict[str, float]]:
        normalized_query = str(query or "").strip()
        normalized_docs = [str(item or "").strip() for item in documents]
        if not normalized_query:
            raise ExternalEvidenceRerankError("query is required")
        if not normalized_docs:
            return []

        last_error: Exception | None = None
        for attempt in range(self._MAX_RETRIES):
            try:
                response = requests.post(
                    f"{self._base_url}/reranks",
                    headers=self._build_headers(),
                    json={
                        "model": self.model,
                        "query": normalized_query,
                        "documents": normalized_docs,
                        "top_n": len(normalized_docs),
                    },
                    timeout=self._TIMEOUT_SECONDS,
                )
                status_code = int(getattr(response, "status_code", 200))
                if self._is_retryable_status(status_code):
                    raise ExternalEvidenceRerankError(f"http {status_code}")
                response.raise_for_status()
                payload = response.json()
                rows = self._parse_results(payload)
                if not rows:
                    raise ExternalEvidenceRerankError("rerank returned no usable results")
                return rows
            except Exception as ex:
                last_error = ex
                if attempt < self._MAX_RETRIES - 1 and self._should_retry(ex):
                    time.sleep(self._RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                raise ExternalEvidenceRerankError(str(ex)) from ex

        raise ExternalEvidenceRerankError(str(last_error or "rerank failed"))

    def _build_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        for key, value in self.client.default_headers.items():
            if value is None:
                continue
            text = str(value)
            if text.startswith("<openai.Omit object"):
                continue
            headers[key] = text
        return headers

    def _parse_results(self, response: Any) -> List[Dict[str, float]]:
        payload = response if isinstance(response, dict) else {}
        data = payload.get("data")
        if not isinstance(data, list):
            data = payload.get("results")
        if not isinstance(data, list):
            output = payload.get("output")
            if isinstance(output, dict):
                data = output.get("results")
        if not isinstance(data, list):
            return []

        rows: List[Dict[str, float]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index"))
                score = float(
                    item.get("relevance_score", item.get("score", item.get("relevanceScore")))
                )
            except Exception:
                continue
            rows.append({"index": index, "relevance_score": score})

        rows.sort(key=lambda item: item["relevance_score"], reverse=True)
        return rows

    def _is_retryable_status(self, status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    def _should_retry(self, ex: Exception) -> bool:
        if isinstance(ex, ExternalEvidenceRerankError):
            message = str(ex).lower()
            return "http 429" in message or "http 5" in message or "no usable results" in message
        return isinstance(ex, requests.RequestException)
