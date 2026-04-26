"""Common academic retrieval client for OpenAlex / Semantic Scholar / Crossref."""

from __future__ import annotations

import os
import random
import re
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from loguru import logger

from agents.common.retrieval.academic_query_utils import normalize_academic_query
from config import settings


def load_api_keys(*env_names: str) -> List[str]:
    keys: List[str] = []
    for env_name in env_names:
        raw_value = os.getenv(env_name, "").strip()
        if not raw_value:
            continue
        for key in re.split(r"[,\n;]+", raw_value):
            value = key.strip()
            if value and value not in keys:
                keys.append(value)
    return keys


def safe_json(response: requests.Response) -> Dict[str, Any]:
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except Exception:
        return default


def recover_inverted_index_text(inverted_index: Dict[str, Any]) -> str:
    positions: Dict[int, str] = {}
    for token, token_positions in inverted_index.items():
        if not isinstance(token_positions, list):
            continue
        for position in token_positions:
            try:
                idx = int(position)
            except Exception:
                continue
            positions[idx] = str(token or "")
    if not positions:
        return ""
    max_index = max(positions.keys())
    words = [positions.get(i, "") for i in range(max_index + 1)]
    return " ".join(word for word in words if word)


def extract_openalex_abstract(item: Dict[str, Any]) -> str:
    abstract_index = item.get("abstract_inverted_index")
    if not isinstance(abstract_index, dict):
        return ""
    return normalize_academic_query(recover_inverted_index_text(abstract_index))


def first_text(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            text = normalize_academic_query(item)
            if text:
                return text
        return ""
    return normalize_academic_query(value)


def normalize_crossref_abstract(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"</?(jats:)?[^>]+>", " ", text)
    text = unescape(text)
    return normalize_academic_query(text)


def extract_crossref_published(item: Dict[str, Any]) -> str:
    for key in ("published-print", "published-online", "issued"):
        row = item.get(key)
        if not isinstance(row, dict):
            continue
        date_parts = row.get("date-parts")
        if not isinstance(date_parts, list) or not date_parts or not isinstance(date_parts[0], list):
            continue
        first = date_parts[0]
        if not first:
            continue
        try:
            year = str(int(first[0])).zfill(4)
        except Exception:
            continue
        month = "01"
        day = "01"
        if len(first) >= 2:
            try:
                month = str(int(first[1])).zfill(2)
            except Exception:
                month = "01"
        if len(first) >= 3:
            try:
                day = str(int(first[2])).zfill(2)
            except Exception:
                day = "01"
        return f"{year}-{month}-{day}"
    return ""


def external_id_from_url(url: Any) -> str:
    parsed = urlparse(str(url or "").strip())
    if not parsed.path:
        return ""
    return parsed.path.rstrip("/").split("/")[-1].strip()


def extract_publication_year(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    matched = re.match(r"^(\d{4})", text)
    if not matched:
        return ""
    return matched.group(1)


class AcademicSearchClient:
    _CROSSREF_MAX_RETRIES = 3
    _RETRY_BACKOFF_SECONDS = 0.2
    _ACADEMIC_MAX_RETRIES = 3
    _PROVIDER_COOLDOWN_SKIP_LOG_INTERVAL_SECONDS = 30.0
    _provider_cooldowns: Dict[str, float] = {}
    _provider_cooldown_log_deadlines: Dict[str, float] = {}
    _provider_cooldown_lock = threading.Lock()
    _semanticscholar_request_lock = threading.Lock()
    _provider_pacing_lock = threading.Lock()
    _provider_next_request_deadlines: Dict[str, float] = {}
    _response_cache_lock = threading.Lock()
    _response_cache: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], Tuple[float, Dict[str, Any]]] = {}

    def __init__(
        self,
        *,
        request_get: Callable[..., requests.Response] | None = None,
    ) -> None:
        self._request_get = request_get or requests.get
        self.openalex_api_keys = load_api_keys("OPENALEX_API_KEYS")
        self._openalex_key_cursor = 0
        self.openalex_base_url = os.getenv("OPENALEX_BASE_URL", "https://api.openalex.org/works").strip()

        self.semanticscholar_api_keys = load_api_keys("SEMANTIC_SCHOLAR_API_KEYS")
        self._semanticscholar_key_cursor = 0
        self.semanticscholar_base_url = os.getenv(
            "SEMANTIC_SCHOLAR_BASE_URL",
            "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
        ).strip()
        self.semanticscholar_fields = ",".join(
            [
                "paperId",
                "externalIds",
                "title",
                "abstract",
                "url",
                "venue",
                "language",
                "year",
                "publicationDate",
                "citationCount",
                "influentialCitationCount",
                "openAccessPdf",
            ]
        )

        self.crossref_base_url = os.getenv("CROSSREF_BASE_URL", "https://api.crossref.org/works").strip()
        self.crossref_mailto = os.getenv("CROSSREF_MAILTO", "").strip()
        self.crossref_select = ",".join(
            [
                "DOI",
                "URL",
                "title",
                "abstract",
                "container-title",
                "published-print",
                "published-online",
                "issued",
                "score",
            ]
        )

    @classmethod
    def _cleanup_expired_response_cache(cls, now: float | None = None) -> None:
        now_ts = now if now is not None else time.monotonic()
        expired_keys = [
            cache_key
            for cache_key, (expires_at, _payload) in cls._response_cache.items()
            if expires_at <= now_ts
        ]
        for cache_key in expired_keys:
            cls._response_cache.pop(cache_key, None)

    @classmethod
    def _cleanup_expired_provider_cooldowns(cls, now: float | None = None) -> None:
        now_ts = now if now is not None else time.monotonic()
        expired_providers = [
            provider
            for provider, cooldown_until in cls._provider_cooldowns.items()
            if cooldown_until <= now_ts
        ]
        for provider in expired_providers:
            cls._provider_cooldowns.pop(provider, None)
            cls._provider_cooldown_log_deadlines.pop(provider, None)

    @staticmethod
    def _format_cooldown_remaining(remaining_seconds: float) -> str:
        seconds = max(0, int(round(remaining_seconds)))
        minutes, seconds = divmod(seconds, 60)
        if minutes <= 0:
            return f"{seconds}s"
        if seconds == 0:
            return f"{minutes}m"
        return f"{minutes}m{seconds}s"

    def _provider_in_rate_limit_cooldown(
        self,
        *,
        provider: str,
        provider_label: str,
        query: str,
    ) -> bool:
        now_ts = time.monotonic()
        with self._provider_cooldown_lock:
            self._cleanup_expired_provider_cooldowns(now_ts)
            cooldown_until = self._provider_cooldowns.get(provider, 0.0)
            if cooldown_until <= now_ts:
                return False
            should_log = self._provider_cooldown_log_deadlines.get(provider, 0.0) <= now_ts
            if should_log:
                self._provider_cooldown_log_deadlines[provider] = (
                    now_ts + self._PROVIDER_COOLDOWN_SKIP_LOG_INTERVAL_SECONDS
                )
        if should_log:
            logger.info(
                f"{provider_label} 仍在限流冷却中，跳过本次检索，"
                f"remaining={self._format_cooldown_remaining(cooldown_until - now_ts)} "
                f"query={query[:100]}"
            )
        return True

    def _mark_provider_rate_limit_cooldown(
        self,
        *,
        provider: str,
        provider_label: str,
        reason: str,
    ) -> None:
        cooldown_seconds = max(int(settings.RETRIEVAL_RATE_LIMIT_COOLDOWN_SECONDS or 0), 1)
        cooldown_until = time.monotonic() + cooldown_seconds
        with self._provider_cooldown_lock:
            self._cleanup_expired_provider_cooldowns()
            previous = self._provider_cooldowns.get(provider, 0.0)
            self._provider_cooldowns[provider] = max(previous, cooldown_until)
            self._provider_cooldown_log_deadlines.pop(provider, None)
            effective_until = self._provider_cooldowns[provider]
        logger.warning(
            f"{provider_label} 触发限流，进入冷却，"
            f"remaining={self._format_cooldown_remaining(effective_until - time.monotonic())} "
            f"reason={reason}"
        )

    def _pace_provider_request(self, provider: str) -> None:
        min_interval = 0.0
        if provider == "semanticscholar":
            min_interval = float(settings.SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS or 0.0)
        if min_interval <= 0:
            return

        with self._provider_pacing_lock:
            now_ts = time.monotonic()
            wait_seconds = max(
                0.0,
                float(self._provider_next_request_deadlines.get(provider, 0.0) - now_ts),
            )
            scheduled_at = now_ts + wait_seconds
            self._provider_next_request_deadlines[provider] = scheduled_at + min_interval
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def _build_response_cache_key(self, provider: str, params: Dict[str, Any]) -> Tuple[str, Tuple[Tuple[str, str], ...]]:
        normalized_items = tuple(
            sorted(
                (
                    str(key),
                    " ".join(str(value or "").split()),
                )
                for key, value in (params or {}).items()
            )
        )
        return provider, normalized_items

    def _get_cached_response(self, provider: str, params: Dict[str, Any]) -> Dict[str, Any]:
        ttl_seconds = max(int(settings.ACADEMIC_RETRIEVAL_CACHE_TTL_SECONDS or 0), 0)
        if ttl_seconds <= 0:
            return {}
        cache_key = self._build_response_cache_key(provider, params)
        now_ts = time.monotonic()
        with self._response_cache_lock:
            self._cleanup_expired_response_cache(now_ts)
            cached = self._response_cache.get(cache_key)
            if not cached:
                return {}
            expires_at, payload = cached
            if expires_at <= now_ts:
                self._response_cache.pop(cache_key, None)
                return {}
            return dict(payload)

    def _store_cached_response(self, provider: str, params: Dict[str, Any], payload: Dict[str, Any]) -> None:
        ttl_seconds = max(int(settings.ACADEMIC_RETRIEVAL_CACHE_TTL_SECONDS or 0), 0)
        if ttl_seconds <= 0 or not self._is_cacheable_payload(provider, payload):
            return
        cache_key = self._build_response_cache_key(provider, params)
        expires_at = time.monotonic() + ttl_seconds
        with self._response_cache_lock:
            self._cleanup_expired_response_cache()
            self._response_cache[cache_key] = (expires_at, dict(payload))

    def _is_cacheable_payload(self, provider: str, payload: Dict[str, Any]) -> bool:
        if not isinstance(payload, dict) or not payload:
            return False
        if provider == "openalex":
            return isinstance(payload.get("results"), list)
        if provider == "semanticscholar":
            return isinstance(payload.get("data"), list)
        return False

    def _parse_retry_after_seconds(self, response: requests.Response | Any) -> float:
        headers = getattr(response, "headers", None) or {}
        value = str(headers.get("Retry-After", "")).strip()
        if not value:
            return 0.0
        try:
            return max(0.0, float(value))
        except Exception:
            pass
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            delta = parsed.timestamp() - datetime.now(timezone.utc).timestamp()
            return max(0.0, delta)
        except Exception:
            return 0.0

    def _retry_delay_seconds(self, attempt_index: int, response: requests.Response | Any = None) -> float:
        retry_after_seconds = self._parse_retry_after_seconds(response)
        if retry_after_seconds > 0:
            return retry_after_seconds
        base_delay = self._RETRY_BACKOFF_SECONDS * (2 ** max(attempt_index - 1, 0))
        return base_delay + random.uniform(0.0, min(0.5, base_delay * 0.25))

    def _request_with_retries(
        self,
        *,
        provider: str,
        provider_label: str,
        request_fn: Callable[[], requests.Response],
        is_limit_error: Callable[[int, Dict[str, Any], str], bool],
        serialize: bool = False,
    ) -> Tuple[str, Dict[str, Any], int, str]:
        last_status_code = 0
        last_response_text = ""
        last_data: Dict[str, Any] = {}
        last_exception: Exception | None = None

        for attempt in range(1, self._ACADEMIC_MAX_RETRIES + 1):
            try:
                if serialize:
                    with self._semanticscholar_request_lock:
                        self._pace_provider_request(provider)
                        response = request_fn()
                else:
                    self._pace_provider_request(provider)
                    response = request_fn()
                last_status_code = int(response.status_code)
                last_response_text = str(response.text or "")
                last_data = safe_json(response)
            except Exception as ex:
                last_exception = ex
                if attempt < self._ACADEMIC_MAX_RETRIES:
                    time.sleep(self._retry_delay_seconds(attempt))
                    continue
                logger.warning(
                    f"{provider_label} 请求失败，重试耗尽，attempts={self._ACADEMIC_MAX_RETRIES} error={ex}"
                )
                return "exception", {}, 0, str(ex)

            if is_limit_error(
                status_code=last_status_code,
                data=last_data,
                response_text=last_response_text,
            ) or last_status_code >= 500:
                if attempt < self._ACADEMIC_MAX_RETRIES:
                    time.sleep(self._retry_delay_seconds(attempt, response))
                    continue
                if is_limit_error(
                    status_code=last_status_code,
                    data=last_data,
                    response_text=last_response_text,
                ):
                    return "limit", last_data, last_status_code, last_response_text
                return "retryable_error", last_data, last_status_code, last_response_text

            if last_status_code >= 400:
                return "http_error", last_data, last_status_code, last_response_text
            return "ok", last_data, last_status_code, last_response_text

        if last_exception is not None:
            return "exception", {}, 0, str(last_exception)
        return "retryable_error", last_data, last_status_code, last_response_text

    def search_openalex(self, query: str, priority_date: Optional[str], per_query: int) -> List[Dict[str, Any]]:
        data = self.openalex_search_raw(query, priority_date, per_query)
        results: List[Dict[str, Any]] = []
        for item in data.get("results", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            primary_location = item.get("primary_location") if isinstance(item.get("primary_location"), dict) else {}
            source = primary_location.get("source") if isinstance(primary_location.get("source"), dict) else {}
            doi = normalize_academic_query(item.get("doi"))
            normalized_doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
            abstract = extract_openalex_abstract(item)
            published = normalize_academic_query(item.get("publication_date") or item.get("publication_year"))
            results.append(
                {
                    "source_type": "openalex",
                    "external_id": external_id_from_url(item.get("id")),
                    "doi": normalized_doi,
                    "url": normalize_academic_query(primary_location.get("landing_page_url")) or doi,
                    "title": normalize_academic_query(item.get("display_name")),
                    "abstract": abstract,
                    "snippet": abstract[:800],
                    "venue": normalize_academic_query(source.get("display_name")),
                    "publication_date": published,
                    "published": published,
                    "language": normalize_academic_query(item.get("language")),
                }
            )
        return results

    def search_semanticscholar(self, query: str, priority_date: Optional[str], per_query: int) -> List[Dict[str, Any]]:
        data = self.semanticscholar_search_raw(query, priority_date, per_query)
        results: List[Dict[str, Any]] = []
        for item in data.get("data", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            open_access_pdf = item.get("openAccessPdf") if isinstance(item.get("openAccessPdf"), dict) else {}
            external_ids = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
            abstract = normalize_academic_query(item.get("abstract"))
            published = normalize_academic_query(item.get("publicationDate") or item.get("year"))
            results.append(
                {
                    "source_type": "semanticscholar",
                    "external_id": normalize_academic_query(item.get("paperId"))
                    or normalize_academic_query(external_ids.get("CorpusId"))
                    or external_id_from_url(item.get("url")),
                    "doi": normalize_academic_query(external_ids.get("DOI")),
                    "url": normalize_academic_query(item.get("url")) or normalize_academic_query(open_access_pdf.get("url")),
                    "title": normalize_academic_query(item.get("title")),
                    "abstract": abstract,
                    "snippet": abstract[:800],
                    "venue": normalize_academic_query(item.get("venue")),
                    "publication_date": published,
                    "published": published,
                    "language": normalize_academic_query(item.get("language")),
                    "citation_count": safe_int(item.get("citationCount")),
                    "influential_citation_count": safe_int(item.get("influentialCitationCount")),
                }
            )
        return results

    def search_crossref(self, query: str, priority_date: Optional[str], per_query: int) -> List[Dict[str, Any]]:
        data = self.crossref_search_raw(query, priority_date, per_query)
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        results: List[Dict[str, Any]] = []
        for item in message.get("items", []) if isinstance(message, dict) else []:
            if not isinstance(item, dict):
                continue
            doi = normalize_academic_query(item.get("DOI"))
            abstract = normalize_crossref_abstract(item.get("abstract"))
            published = extract_crossref_published(item)
            results.append(
                {
                    "source_type": "crossref",
                    "external_id": doi or external_id_from_url(item.get("URL")),
                    "doi": doi,
                    "url": normalize_academic_query(item.get("URL")) or (f"https://doi.org/{doi}" if doi else ""),
                    "title": first_text(item.get("title")),
                    "abstract": abstract,
                    "snippet": abstract[:800],
                    "venue": first_text(item.get("container-title")),
                    "publication_date": published,
                    "published": published,
                    "language": normalize_academic_query(item.get("language")),
                    "score": item.get("score"),
                }
            )
        return results

    def openalex_search_raw(self, query: str, priority_date: Optional[str], per_query: int) -> Dict[str, Any]:
        if self._provider_in_rate_limit_cooldown(
            provider="openalex",
            provider_label="OpenAlex",
            query=query,
        ):
            return {}
        normalized_query = normalize_academic_query(query)
        base_params: Dict[str, Any] = {"per-page": per_query}
        filters: List[str] = []
        if normalized_query:
            filters.append(f"title_and_abstract.search:{normalized_query}")
        filters.extend(["language:en", "has_abstract:true"])
        if priority_date:
            filters.append(f"to_publication_date:{priority_date}")
        if filters:
            base_params["filter"] = ",".join(filters)

        cached_data = self._get_cached_response("openalex", base_params)
        if cached_data:
            return cached_data

        if not self.openalex_api_keys:
            outcome, data, status_code, response_text = self._request_with_retries(
                provider="openalex",
                provider_label="OpenAlex",
                request_fn=lambda: self._request_get(
                    self.openalex_base_url,
                    params=base_params,
                    timeout=settings.RETRIEVAL_REQUEST_TIMEOUT_SECONDS,
                ),
                is_limit_error=self._is_openalex_limit_error,
            )
            if outcome == "ok":
                self._store_cached_response("openalex", base_params, data)
                return data
            if outcome == "limit":
                self._mark_provider_rate_limit_cooldown(
                    provider="openalex",
                    provider_label="OpenAlex",
                    reason=f"status={status_code} anonymous",
                )
                return {}
            logger.warning(
                f"OpenAlex 检索失败，query={query[:100]} outcome={outcome} "
                f"status={status_code} body={response_text[:200]}"
            )
            return {}

        total_keys = len(self.openalex_api_keys)
        start_index = self._openalex_key_cursor
        limit_failures = 0
        for offset in range(total_keys):
            index = (start_index + offset) % total_keys
            params = dict(base_params)
            params["api_key"] = self.openalex_api_keys[index]
            outcome, data, status_code, response_text = self._request_with_retries(
                provider="openalex",
                provider_label="OpenAlex",
                request_fn=lambda current_params=params: self._request_get(
                    self.openalex_base_url,
                    params=current_params,
                    timeout=settings.RETRIEVAL_REQUEST_TIMEOUT_SECONDS,
                ),
                is_limit_error=self._is_openalex_limit_error,
            )
            if outcome == "ok":
                self._openalex_key_cursor = index
                self._store_cached_response("openalex", base_params, data)
                return data
            if outcome == "limit":
                limit_failures += 1
                logger.warning(f"OpenAlex key 触发限额/限流，切换下一个 key，status={status_code} query={query[:100]}")
                self._openalex_key_cursor = (index + 1) % total_keys
                continue
            if outcome == "retryable_error":
                logger.warning(
                    f"OpenAlex 请求重试耗尽，切换下一个 key，status={status_code} query={query[:100]}"
                )
                self._openalex_key_cursor = (index + 1) % total_keys
                continue
            if outcome == "exception":
                logger.warning(f"OpenAlex 请求失败，尝试下一个 key，query={query[:100]} error={response_text}")
                self._openalex_key_cursor = (index + 1) % total_keys
                continue
            if status_code >= 400:
                logger.warning(
                    f"OpenAlex 检索失败（非限额类错误），status={status_code} query={query[:100]} body={response_text[:200]}"
                )
                return {}

        if total_keys > 0 and limit_failures == total_keys:
            self._mark_provider_rate_limit_cooldown(
                provider="openalex",
                provider_label="OpenAlex",
                reason=f"all_keys_rate_limited total_keys={total_keys}",
            )
        logger.warning(f"OpenAlex 所有 key 均不可用，query={query[:100]}")
        return {}

    def semanticscholar_search_raw(self, query: str, priority_date: Optional[str], per_query: int) -> Dict[str, Any]:
        if self._provider_in_rate_limit_cooldown(
            provider="semanticscholar",
            provider_label="Semantic Scholar",
            query=query,
        ):
            return {}
        params: Dict[str, Any] = {
            "query": normalize_academic_query(query),
            "limit": per_query,
            "fields": self.semanticscholar_fields,
        }
        year_ceiling = extract_publication_year(priority_date)
        if year_ceiling:
            # Search endpoint accepts coarse year filtering; exact cutoff is enforced client-side.
            params["year"] = f"-{year_ceiling}"

        cached_data = self._get_cached_response("semanticscholar", params)
        if cached_data:
            return cached_data

        if not self.semanticscholar_api_keys:
            outcome, data, status_code, response_text = self._request_with_retries(
                provider="semanticscholar",
                provider_label="Semantic Scholar",
                request_fn=lambda: self._request_get(
                    self.semanticscholar_base_url,
                    params=params,
                    timeout=settings.RETRIEVAL_REQUEST_TIMEOUT_SECONDS,
                ),
                is_limit_error=self._is_semanticscholar_limit_error,
                serialize=True,
            )
            if outcome == "ok":
                self._store_cached_response("semanticscholar", params, data)
                return data
            if outcome == "limit":
                self._mark_provider_rate_limit_cooldown(
                    provider="semanticscholar",
                    provider_label="Semantic Scholar",
                    reason=f"status={status_code} anonymous",
                )
                return {}
            logger.warning(
                f"Semantic Scholar 检索失败，query={query[:100]} outcome={outcome} "
                f"status={status_code} body={response_text[:200]}"
            )
            return {}

        total_keys = len(self.semanticscholar_api_keys)
        start_index = self._semanticscholar_key_cursor
        limit_failures = 0
        for offset in range(total_keys):
            index = (start_index + offset) % total_keys
            headers = {"x-api-key": self.semanticscholar_api_keys[index]}
            outcome, data, status_code, response_text = self._request_with_retries(
                provider="semanticscholar",
                provider_label="Semantic Scholar",
                request_fn=lambda current_headers=headers: self._request_get(
                    self.semanticscholar_base_url,
                    params=params,
                    headers=current_headers,
                    timeout=settings.RETRIEVAL_REQUEST_TIMEOUT_SECONDS,
                ),
                is_limit_error=self._is_semanticscholar_limit_error,
                serialize=True,
            )
            if outcome == "ok":
                self._semanticscholar_key_cursor = index
                self._store_cached_response("semanticscholar", params, data)
                return data
            if outcome == "limit":
                limit_failures += 1
                logger.warning(
                    f"Semantic Scholar key 触发限额/限流，切换下一个 key，status={status_code} query={query[:100]}"
                )
                self._semanticscholar_key_cursor = (index + 1) % total_keys
                continue
            if outcome == "retryable_error":
                logger.warning(
                    f"Semantic Scholar 请求重试耗尽，切换下一个 key，status={status_code} query={query[:100]}"
                )
                self._semanticscholar_key_cursor = (index + 1) % total_keys
                continue
            if outcome == "exception":
                logger.warning(
                    f"Semantic Scholar 请求失败，尝试下一个 key，query={query[:100]} error={response_text}"
                )
                self._semanticscholar_key_cursor = (index + 1) % total_keys
                continue
            if status_code >= 400:
                logger.warning(
                    f"Semantic Scholar 检索失败（非限额类错误），status={status_code} query={query[:100]} body={response_text[:200]}"
                )
                return {}

        if total_keys > 0 and limit_failures == total_keys:
            self._mark_provider_rate_limit_cooldown(
                provider="semanticscholar",
                provider_label="Semantic Scholar",
                reason=f"all_keys_rate_limited total_keys={total_keys}",
            )
        logger.warning(f"Semantic Scholar 所有 key 均不可用，query={query[:100]}")
        return {}

    def crossref_search_raw(self, query: str, priority_date: Optional[str], per_query: int) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "query.bibliographic": normalize_academic_query(query),
            "rows": per_query,
            "select": self.crossref_select,
        }
        filters: List[str] = []
        if priority_date:
            filters.append(f"until-pub-date:{priority_date}")
        if filters:
            params["filter"] = ",".join(filters)
        if self.crossref_mailto:
            params["mailto"] = self.crossref_mailto
        last_error: Exception | None = None
        for attempt in range(self._CROSSREF_MAX_RETRIES):
            try:
                response = self._request_get(
                    self.crossref_base_url,
                    params=params,
                    timeout=settings.RETRIEVAL_REQUEST_TIMEOUT_SECONDS,
                )
                status_code = int(response.status_code)
                response_text = str(response.text or "")
                data = safe_json(response)
            except Exception as ex:
                last_error = ex
                if attempt < self._CROSSREF_MAX_RETRIES - 1:
                    logger.warning(
                        f"Crossref 请求失败，准备重试 ({attempt + 1}/{self._CROSSREF_MAX_RETRIES})，"
                        f"query={query[:100]} error={ex}"
                    )
                    time.sleep(self._RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                logger.warning(f"Crossref 检索失败，query={query[:100]} error={ex}")
                return {}

            if self._is_crossref_retryable_error(status_code=status_code, data=data, response_text=response_text):
                if attempt < self._CROSSREF_MAX_RETRIES - 1:
                    logger.warning(
                        f"Crossref 瞬时失败，准备重试 ({attempt + 1}/{self._CROSSREF_MAX_RETRIES})，"
                        f"status={status_code} query={query[:100]}"
                    )
                    time.sleep(self._RETRY_BACKOFF_SECONDS * (attempt + 1))
                    continue
                logger.warning(
                    f"Crossref 检索失败（重试耗尽），status={status_code} query={query[:100]} body={response_text[:200]}"
                )
                return {}

            if status_code >= 400:
                logger.warning(
                    f"Crossref 检索失败（非重试类错误），status={status_code} query={query[:100]} body={response_text[:200]}"
                )
                return {}

            return data

        if last_error is not None:
            logger.warning(f"Crossref 检索失败，query={query[:100]} error={last_error}")
        return {}

    def _is_openalex_limit_error(self, status_code: int, data: Dict[str, Any], response_text: str) -> bool:
        if status_code == 429:
            return True
        message = " ".join(
            part.lower()
            for part in [response_text, str(data.get("error", "")), str(data.get("message", "")), str(data.get("detail", ""))]
            if part
        )
        return any(keyword in message for keyword in ["rate limit", "quota", "exceed", "limit reached", "too many requests"])

    def _is_semanticscholar_limit_error(self, status_code: int, data: Dict[str, Any], response_text: str) -> bool:
        if status_code == 429:
            return True
        message = " ".join(
            part.lower()
            for part in [response_text, str(data.get("error", "")), str(data.get("message", "")), str(data.get("detail", ""))]
            if part
        )
        return any(keyword in message for keyword in ["rate limit", "quota", "exceed", "too many requests"])

    def _is_crossref_retryable_error(self, status_code: int, data: Dict[str, Any], response_text: str) -> bool:
        if status_code == 429 or status_code >= 500:
            return True
        message = " ".join(
            part.lower()
            for part in [response_text, str(data.get("error", "")), str(data.get("message", "")), str(data.get("detail", ""))]
            if part
        )
        return any(keyword in message for keyword in ["rate limit", "quota", "too many requests", "temporarily unavailable"])
