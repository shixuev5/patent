"""
外部证据聚合器
统一接入 OpenAlex（学术）、智慧芽（专利）和 Tavily（网页）检索能力。
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from loguru import logger

from agents.ai_reply.src.retrieval_utils import (
    ENGINE_ALIASES,
    QuerySpec,
    flatten_query_texts,
    normalize_query_specs,
)
from agents.common.retrieval.external_rerank_service import (
    ExternalEvidenceRerankError,
    ExternalEvidenceRerankService,
)
from agents.common.utils.concurrency import submit_with_current_context
from config import settings


class ExternalEvidenceAggregator:
    """统一外部检索聚合器。"""

    _EXTERNAL_PER_QUERY = 8
    _MAX_RERANK_CANDIDATES = 24

    def __init__(self):
        self.openalex_api_keys = self._load_api_keys("OPENALEX_API_KEYS")
        self._openalex_key_cursor = 0
        self.openalex_base_url = os.getenv("OPENALEX_BASE_URL", "https://api.openalex.org/works").strip()
        self.openalex_email = os.getenv("OPENALEX_EMAIL", "").strip()

        self.tavily_api_keys = self._load_api_keys("TAVILY_API_KEYS")
        self._tavily_key_cursor = 0
        self.tavily_base_url = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com/search").strip()

        self.zhihuiya_enabled = bool(settings.ZHIHUIYA_ACCOUNTS)
        self.zhihuiya_min_similarity_score = self._safe_float(
            os.getenv("ZHIHUIYA_MIN_SIMILARITY_SCORE", "0"),
            default=0.0,
        )
        self.zhihuiya_client = None
        self._rerank_service: ExternalEvidenceRerankService | None = None
        if self.zhihuiya_enabled:
            try:
                from agents.common.search_clients.factory import SearchClientFactory

                self.zhihuiya_client = SearchClientFactory.get_client("zhihuiya")
            except Exception as ex:
                logger.warning(f"初始化智慧芽客户端失败，后续将跳过专利证据检索: {ex}")
                self.zhihuiya_client = None

    def search_evidence(
        self,
        queries: Dict[str, List[QuerySpec]],
        priority_date: Optional[str],
        limit: int = 8,
    ) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
        """
        聚合外部证据并统一编号。

        Returns:
            (evidence_list, retrieval_engines, retrieval_meta)
        """
        queries_by_engine: Dict[str, List[QuerySpec]] = {"openalex": [], "zhihuiya": [], "tavily": []}
        for key, value in (queries or {}).items():
            engine = ENGINE_ALIASES.get(str(key).strip().lower())
            if engine and isinstance(value, list):
                queries_by_engine[engine] = normalize_query_specs(value, engine=engine, limit=4)
        if not any(queries_by_engine.values()):
            return [], [], {"retrieval": {}}

        engine_hits: Dict[str, List[Dict[str, Any]]] = {}
        engines: List[str] = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_map = {}
            if queries_by_engine.get("openalex"):
                future_map["openalex"] = submit_with_current_context(
                    executor,
                    self._search_openalex,
                    queries_by_engine["openalex"],
                    priority_date,
                    self._EXTERNAL_PER_QUERY,
                )
            if queries_by_engine.get("zhihuiya"):
                future_map["zhihuiya"] = submit_with_current_context(
                    executor,
                    self._search_zhihuiya,
                    queries_by_engine["zhihuiya"],
                    priority_date,
                    self._EXTERNAL_PER_QUERY,
                    self.zhihuiya_min_similarity_score,
                )
            if queries_by_engine.get("tavily"):
                future_map["tavily"] = submit_with_current_context(
                    executor,
                    self._search_tavily,
                    queries_by_engine["tavily"],
                    priority_date,
                    self._EXTERNAL_PER_QUERY,
                )

            for engine_name in ["openalex", "zhihuiya", "tavily"]:
                future = future_map.get(engine_name)
                if not future:
                    continue
                try:
                    hits = future.result()
                except Exception as ex:
                    logger.warning(f"{engine_name} 检索执行失败: {ex}")
                    hits = []
                if hits:
                    engine_hits[engine_name] = hits
                    engines.append(engine_name)

        raw_candidates = [item for hits in engine_hits.values() for item in hits]
        deduped_candidates = self._interleave_by_source(
            self._dedupe_results(self._clean_results(raw_candidates))
        )[: self._MAX_RERANK_CANDIDATES]

        rerank_enabled = False
        rerank_fallback_reason = ""
        if deduped_candidates:
            try:
                ranked_candidates = self._rerank_results(deduped_candidates, queries_by_engine)
                rerank_enabled = True
            except ValueError:
                raise
            except ExternalEvidenceRerankError as ex:
                rerank_fallback_reason = str(ex)
                logger.warning(f"外部证据 rerank 失败，切换到本地排序兜底: {rerank_fallback_reason}")
                ranked_candidates = self._fallback_rank_results(deduped_candidates, queries_by_engine)
        else:
            ranked_candidates = []

        ranked_candidates = self._collapse_ranked_duplicates(ranked_candidates)
        merged = self._interleave_by_source(ranked_candidates)
        final_results = [self._finalize_result(item) for item in merged[:limit]]
        for index, item in enumerate(final_results, start=1):
            item["doc_id"] = f"EXT{index}"

        retrieval_meta = self._build_retrieval_meta(
            queries_by_engine=queries_by_engine,
            final_results=final_results,
            priority_date=priority_date,
            engine_hits=engine_hits,
            rerank_enabled=rerank_enabled,
            rerank_fallback_reason=rerank_fallback_reason,
        )
        return final_results, engines, retrieval_meta

    def _search_openalex(
        self,
        queries: List[QuerySpec],
        priority_date: Optional[str],
        per_query: int,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        for query in queries:
            query_text = " ".join(str((query or {}).get("text", "")).split())
            if not query_text:
                continue
            data = self._openalex_search_with_key_rotation(
                query=query_text,
                priority_date=priority_date,
                per_query=per_query,
            )
            if not data:
                continue

            for item in data.get("results", []) or []:
                title = str(item.get("display_name", "")).strip()
                primary_location = self._to_dict(item.get("primary_location", {}))
                source = self._to_dict(primary_location.get("source", {}))
                doi = str(item.get("doi", "")).strip()
                url = str(primary_location.get("landing_page_url", "")).strip() or doi
                snippet = self._extract_openalex_snippet(item)
                published = str(item.get("publication_date", "")).strip() or str(item.get("publication_year", "")).strip()

                if not any([title, url, snippet]):
                    continue
                if priority_date and published and not self._is_not_later_than(published, priority_date):
                    continue

                results.append({
                    "source_type": "openalex",
                    "title": title or str(source.get("display_name", "")).strip(),
                    "url": url,
                    "snippet": snippet,
                    "published": published,
                })

        return results

    def _search_zhihuiya(
        self,
        queries: List[QuerySpec],
        priority_date: Optional[str],
        per_query: int,
        min_similarity_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        if not self.zhihuiya_client:
            return []

        to_date = priority_date.replace("-", "") if priority_date else ""
        results: List[Dict[str, Any]] = []
        for query in queries:
            query_text = " ".join(str((query or {}).get("text", "")).split())
            query_mode = str((query or {}).get("mode", "")).strip().lower()
            if not query_text:
                continue
            try:
                if query_mode == "lexical":
                    raw_docs = self.zhihuiya_client.search(query_text, limit=per_query) or []
                else:
                    raw_docs = self.zhihuiya_client.search_semantic(query_text, to_date=to_date, limit=per_query) or []
            except Exception as ex:
                logger.warning(f"智慧芽检索失败，mode={query_mode} query={query_text[:100]} error={ex}")
                continue

            docs: List[Dict[str, Any]] = []
            if isinstance(raw_docs, dict):
                docs_raw_list = raw_docs.get("results", [])
                if isinstance(docs_raw_list, list):
                    docs = docs_raw_list[:per_query]
            elif isinstance(raw_docs, list):
                docs = raw_docs[:per_query]

            for item in docs:
                doc = self._to_dict(item)
                title = str(doc.get("title", "")).strip()
                pn = str(doc.get("pn", "")).strip()
                abstract = str(doc.get("abstract", "")).strip()
                published = str(doc.get("publication_date", "")).strip()
                similarity_score = self._safe_float(doc.get("score"), default=0.0)

                if priority_date and published and not self._is_not_later_than(published, priority_date):
                    continue
                if similarity_score < min_similarity_score:
                    continue
                if not any([title, pn, abstract]):
                    continue

                results.append({
                    "source_type": "zhihuiya",
                    "title": title or pn,
                    "url": f"https://patents.google.com/patent/{pn}" if pn else "",
                    "snippet": abstract[:800],
                    "published": published,
                    "pn": pn,
                })

        return results

    def _search_tavily(
        self,
        queries: List[QuerySpec],
        priority_date: Optional[str],
        per_query: int,
    ) -> List[Dict[str, Any]]:
        if not self.tavily_api_keys:
            logger.warning("TAVILY_API_KEYS 未配置，将跳过网页证据检索")
            return []

        results: List[Dict[str, Any]] = []
        for query in queries:
            query_text = " ".join(str((query or {}).get("text", "")).split())
            if not query_text:
                continue
            data = self._tavily_search_with_key_rotation(
                query=query_text,
                priority_date=priority_date,
                per_query=per_query,
            )
            if not data:
                continue

            for item in data.get("results", []) or []:
                item_dict = self._to_dict(item)
                title = str(item_dict.get("title", "")).strip()
                url = str(item_dict.get("url", "")).strip()
                snippet = str(item_dict.get("content", "")).strip()
                published = str(item_dict.get("published_date", "")).strip() or str(item_dict.get("published", "")).strip()

                if priority_date and published and not self._is_not_later_than(published, priority_date):
                    continue
                if not any([title, url, snippet]):
                    continue

                results.append({
                    "source_type": "tavily",
                    "title": title,
                    "url": url,
                    "snippet": snippet[:800],
                    "published": published,
                })

        return results

    def _clean_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cleaned: List[Dict[str, Any]] = []
        for item in results:
            row = self._to_dict(item)
            title = str(row.get("title", "")).strip()
            url = str(row.get("url", "")).strip()
            snippet = str(row.get("snippet", "")).strip()
            if not title or not url or not snippet:
                continue
            cleaned.append({
                "source_type": str(row.get("source_type", "")).strip(),
                "title": title,
                "url": url,
                "snippet": snippet[:800],
                "published": str(row.get("published", "")).strip(),
                "pn": str(row.get("pn", "")).strip(),
            })
        return cleaned

    def _rerank_results(
        self,
        candidates: List[Dict[str, Any]],
        queries_by_engine: Dict[str, List[QuerySpec]],
    ) -> List[Dict[str, Any]]:
        flat_queries = flatten_query_texts(queries_by_engine)
        if not flat_queries:
            return candidates

        documents = [self._build_rerank_document(item) for item in candidates]
        best_scores = [0.0] * len(candidates)
        rerank_service = self._get_rerank_service()
        for query in flat_queries:
            rows = rerank_service.rerank(query=query, documents=documents)
            for item in rows:
                index = int(item.get("index", -1))
                if 0 <= index < len(best_scores):
                    best_scores[index] = max(best_scores[index], self._safe_float(item.get("relevance_score"), 0.0))

        ranked: List[Dict[str, Any]] = []
        for index, item in enumerate(candidates):
            row = dict(item)
            row["relevance_score"] = round(best_scores[index], 6)
            row["_original_rank"] = index
            ranked.append(row)
        ranked.sort(
            key=lambda item: (
                float(item.get("relevance_score", 0.0)),
                self._normalize_date(item.get("published")) or "",
                -int(item.get("_original_rank", 0)),
            ),
            reverse=True,
        )
        return ranked

    def _fallback_rank_results(
        self,
        candidates: List[Dict[str, Any]],
        queries_by_engine: Dict[str, List[QuerySpec]],
    ) -> List[Dict[str, Any]]:
        flat_queries = flatten_query_texts(queries_by_engine)
        ranked: List[Dict[str, Any]] = []
        for index, item in enumerate(candidates):
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            title_norm = self._normalize_search_text(title)
            body_norm = self._normalize_search_text(f"{title}\n{snippet}")
            title_tokens = self._tokenize_search_text(title_norm)
            body_tokens = self._tokenize_search_text(body_norm)

            best_phrase = 0
            best_title_hits = 0
            best_coverage = 0.0
            for query in flat_queries:
                query_norm = self._normalize_search_text(query)
                if not query_norm:
                    continue
                if query_norm in title_norm:
                    best_phrase = 1
                query_tokens = self._tokenize_search_text(query_norm)
                if not query_tokens:
                    continue
                title_hits = len(set(query_tokens) & set(title_tokens))
                body_hits = len(set(query_tokens) & set(body_tokens))
                coverage = body_hits / float(len(set(query_tokens)))
                best_title_hits = max(best_title_hits, title_hits)
                best_coverage = max(best_coverage, coverage)

            row = dict(item)
            row["relevance_score"] = round(best_phrase * 1.0 + best_title_hits * 0.2 + best_coverage * 0.1, 6)
            row["_original_rank"] = index
            ranked.append(row)

        ranked.sort(
            key=lambda item: (
                float(item.get("relevance_score", 0.0)),
                self._normalize_date(item.get("published")) or "",
                -int(item.get("_original_rank", 0)),
            ),
            reverse=True,
        )
        return ranked

    def _build_rerank_document(self, item: Dict[str, Any]) -> str:
        title = str(item.get("title", "")).strip()
        snippet = str(item.get("snippet", "")).strip()
        if snippet:
            return f"{title}\n\n{snippet}".strip()
        return title

    def _get_rerank_service(self) -> ExternalEvidenceRerankService:
        if self._rerank_service is None:
            self._rerank_service = ExternalEvidenceRerankService()
        return self._rerank_service

    def _finalize_result(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "source_type": str(item.get("source_type", "")).strip(),
            "title": str(item.get("title", "")).strip(),
            "url": str(item.get("url", "")).strip(),
            "snippet": str(item.get("snippet", "")).strip(),
            "published": str(item.get("published", "")).strip(),
            "relevance_score": round(self._safe_float(item.get("relevance_score"), 0.0), 6),
        }

    def _load_api_keys(self, *env_names: str) -> List[str]:
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

    def _openalex_search_with_key_rotation(
        self,
        query: str,
        priority_date: Optional[str],
        per_query: int,
    ) -> Dict[str, Any]:
        normalized_query = self._normalize_openalex_query(query)
        base_params: Dict[str, Any] = {"per-page": per_query}
        filters: List[str] = []
        if normalized_query:
            filters.append(f"title_and_abstract.search:{normalized_query}")
        filters.extend(["language:en", "has_abstract:true"])
        if priority_date:
            filters.append(f"to_publication_date:{priority_date}")
        if filters:
            base_params["filter"] = ",".join(filters)
        if self.openalex_email:
            base_params["mailto"] = self.openalex_email

        if not self.openalex_api_keys:
            try:
                response = requests.get(
                    self.openalex_base_url,
                    params=base_params,
                    timeout=settings.RETRIEVAL_REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                return self._safe_json(response)
            except Exception as ex:
                logger.warning(f"OpenAlex 检索失败，query={query[:100]} error={ex}")
                return {}

        total_keys = len(self.openalex_api_keys)
        start_index = self._openalex_key_cursor
        for offset in range(total_keys):
            index = (start_index + offset) % total_keys
            api_key = self.openalex_api_keys[index]
            params = dict(base_params)
            params["api_key"] = api_key
            try:
                response = requests.get(
                    self.openalex_base_url,
                    params=params,
                    timeout=settings.RETRIEVAL_REQUEST_TIMEOUT_SECONDS,
                )
                status_code = int(response.status_code)
                response_text = str(response.text or "")
                data = self._safe_json(response)
            except Exception as ex:
                logger.warning(f"OpenAlex 请求失败，尝试下一个 key，query={query[:100]} error={ex}")
                self._openalex_key_cursor = (index + 1) % total_keys
                continue

            if self._is_openalex_limit_error(status_code=status_code, data=data, response_text=response_text):
                logger.warning(
                    f"OpenAlex key 触发限额/限流，切换下一个 key，status={status_code} query={query[:100]}"
                )
                self._openalex_key_cursor = (index + 1) % total_keys
                continue

            if status_code >= 400:
                logger.warning(
                    f"OpenAlex 检索失败（非限额类错误），status={status_code} query={query[:100]} body={response_text[:200]}"
                )
                return {}

            self._openalex_key_cursor = index
            return data

        logger.warning(f"OpenAlex 所有 key 均不可用，query={query[:100]}")
        return {}

    def _tavily_search_with_key_rotation(
        self,
        query: str,
        priority_date: Optional[str],
        per_query: int,
    ) -> Dict[str, Any]:
        total_keys = len(self.tavily_api_keys)
        if total_keys == 0:
            return {}

        for offset in range(total_keys):
            index = (self._tavily_key_cursor + offset) % total_keys
            api_key = self.tavily_api_keys[index]
            payload = {
                "api_key": api_key,
                "query": query,
                "search_depth": "advanced",
                "topic": "general",
                "chunks_per_source": 3,
                "max_results": per_query,
                "include_answer": False,
                "include_raw_content": False,
            }
            if priority_date:
                payload["end_date"] = priority_date
            try:
                response = requests.post(
                    self.tavily_base_url,
                    json=payload,
                    timeout=settings.RETRIEVAL_REQUEST_TIMEOUT_SECONDS,
                )
                status_code = int(response.status_code)
                response_text = str(response.text or "")
                data = self._safe_json(response)
            except Exception as ex:
                logger.warning(f"Tavily 请求失败，尝试下一个 key，query={query[:80]} error={ex}")
                self._tavily_key_cursor = (index + 1) % total_keys
                continue

            if self._is_tavily_limit_error(status_code=status_code, data=data, response_text=response_text):
                logger.warning(
                    f"Tavily key 触发限额/限流，切换下一个 key，status={status_code} query={query[:80]}"
                )
                self._tavily_key_cursor = (index + 1) % total_keys
                continue

            if status_code >= 400:
                logger.warning(
                    f"Tavily 检索失败（非限额类错误），status={status_code} query={query[:80]} body={response_text[:200]}"
                )
                return {}

            self._tavily_key_cursor = index
            return data

        logger.warning(f"Tavily 所有 key 均不可用，query={query[:80]}")
        return {}

    def _is_tavily_limit_error(self, status_code: int, data: Dict[str, Any], response_text: str) -> bool:
        if status_code == 429:
            return True
        message_parts = [
            response_text,
            str(data.get("error", "")),
            str(data.get("message", "")),
            str(data.get("detail", "")),
        ]
        message = " ".join(part.lower() for part in message_parts if part)
        limit_keywords = [
            "rate limit",
            "quota",
            "credit",
            "exceed",
            "limit reached",
            "insufficient",
            "too many requests",
        ]
        return any(keyword in message for keyword in limit_keywords)

    def _is_openalex_limit_error(self, status_code: int, data: Dict[str, Any], response_text: str) -> bool:
        if status_code == 429:
            return True
        message_parts = [
            response_text,
            str(data.get("error", "")),
            str(data.get("message", "")),
            str(data.get("detail", "")),
        ]
        message = " ".join(part.lower() for part in message_parts if part)
        limit_keywords = [
            "rate limit",
            "quota",
            "exceed",
            "limit reached",
            "too many requests",
        ]
        return any(keyword in message for keyword in limit_keywords)

    def _safe_json(self, response: requests.Response) -> Dict[str, Any]:
        try:
            data = response.json()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _extract_openalex_snippet(self, item: Dict[str, Any]) -> str:
        abstract_index = self._to_dict(item.get("abstract_inverted_index", {}))
        if not abstract_index:
            return ""
        return self._recover_inverted_index_text(abstract_index)[:800]

    def _recover_inverted_index_text(self, inverted_index: Dict[str, Any]) -> str:
        positions: Dict[int, str] = {}
        for token, token_positions in inverted_index.items():
            if not isinstance(token_positions, list):
                continue
            for position in token_positions:
                try:
                    idx = int(position)
                except Exception:
                    continue
                positions[idx] = token
        if not positions:
            return ""
        max_index = max(positions.keys())
        words = [positions.get(i, "") for i in range(max_index + 1)]
        return " ".join(word for word in words if word)

    def _dedupe_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for item in results:
            url = str(item.get("url", "")).strip()
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            key = url or f"{title}::{snippet[:120]}"
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _collapse_ranked_duplicates(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        collapsed: List[Dict[str, Any]] = []
        seen_general: Set[str] = set()
        seen_zhihuiya: Set[str] = set()
        for item in results:
            row = self._to_dict(item)
            source_type = str(row.get("source_type", "")).strip()
            title = self._normalize_search_text(row.get("title"))
            snippet = self._normalize_search_text(str(row.get("snippet", ""))[:180])
            url = str(row.get("url", "")).strip()
            general_key = url or f"{title}::{snippet[:120]}"
            if general_key in seen_general:
                continue
            if source_type == "zhihuiya":
                pn = str(row.get("pn", "")).strip().upper()
                duplicate_keys = [key for key in [pn, title, f"{title}::{snippet}"] if key]
                if any(key in seen_zhihuiya for key in duplicate_keys):
                    continue
                seen_zhihuiya.update(duplicate_keys)
            seen_general.add(general_key)
            collapsed.append(item)
        return collapsed

    def _interleave_by_source(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for item in results:
            source_type = str(item.get("source_type", "")).strip() or "unknown"
            buckets.setdefault(source_type, []).append(item)

        ordered_source_types = [key for key in ["openalex", "zhihuiya", "tavily"] if key in buckets]
        for source_type in buckets:
            if source_type not in ordered_source_types:
                ordered_source_types.append(source_type)

        merged: List[Dict[str, Any]] = []
        while True:
            added = False
            for source_type in ordered_source_types:
                bucket = buckets.get(source_type, [])
                if not bucket:
                    continue
                merged.append(bucket.pop(0))
                added = True
            if not added:
                break
        return merged

    def _build_retrieval_meta(
        self,
        queries_by_engine: Dict[str, List[QuerySpec]],
        final_results: List[Dict[str, Any]],
        priority_date: Optional[str],
        engine_hits: Dict[str, List[Dict[str, Any]]],
        rerank_enabled: bool,
        rerank_fallback_reason: str,
    ) -> Dict[str, Any]:
        retrieval: Dict[str, Dict[str, Any]] = {}
        for engine, engine_queries in queries_by_engine.items():
            if not engine_queries:
                continue
            filters: Dict[str, Any] = {}
            if priority_date:
                filters["priority_date_lte"] = priority_date
            if engine == "openalex":
                filters["language"] = "en"
                filters["has_abstract"] = True
                filters["search_field"] = "title_and_abstract.search"
            if engine == "zhihuiya":
                filters["min_similarity_score"] = self.zhihuiya_min_similarity_score
            if engine == "tavily":
                filters["search_depth"] = "advanced"
                filters["topic"] = "general"
                filters["chunks_per_source"] = 3
                if priority_date:
                    filters["end_date"] = priority_date
            retrieval[engine] = {
                "queries": engine_queries,
                "filters": filters,
                "raw_result_count": len(engine_hits.get(engine, [])),
                "result_count": 0,
                "rerank_enabled": rerank_enabled,
                "rerank_model": str(settings.RETRIEVAL_RERANK_MODEL or "").strip() or "qwen3-rerank",
                "rerank_fallback_reason": rerank_fallback_reason,
                "results": [],
            }

        for item in final_results:
            engine = self._source_to_engine(str(item.get("source_type", "")).strip())
            if not engine:
                continue
            stat = retrieval.setdefault(engine, {
                "queries": [],
                "filters": {},
                "raw_result_count": 0,
                "result_count": 0,
                "rerank_enabled": rerank_enabled,
                "rerank_model": str(settings.RETRIEVAL_RERANK_MODEL or "").strip() or "qwen3-rerank",
                "rerank_fallback_reason": rerank_fallback_reason,
                "results": [],
            })
            stat["result_count"] += 1
            stat["results"].append({
                "doc_id": str(item.get("doc_id", "")).strip() or None,
                "title": str(item.get("title", "")).strip(),
                "url": str(item.get("url", "")).strip() or None,
                "published": str(item.get("published", "")).strip() or None,
                "relevance_score": round(self._safe_float(item.get("relevance_score"), 0.0), 6),
            })
        return {"retrieval": retrieval}

    def _source_to_engine(self, source_type: str) -> str:
        if source_type in {"openalex", "zhihuiya", "tavily"}:
            return source_type
        return ""

    def _normalize_search_text(self, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    def _tokenize_search_text(self, value: str) -> List[str]:
        return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", value)

    def _normalize_openalex_query(self, query: Any) -> str:
        text = " ".join(str(query or "").split())
        text = text.replace(",", " ").replace("，", " ")
        return re.sub(r"\s+", " ", text).strip()

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            if isinstance(value, str):
                value = value.strip().replace("%", "")
            return float(value)
        except Exception:
            return float(default)

    def _normalize_date(self, value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None

        english_date_formats = [
            "%d %b %Y",
            "%d %B %Y",
            "%b %d, %Y",
            "%B %d, %Y",
            "%Y-%m-%d",
        ]
        for date_format in english_date_formats:
            try:
                parsed = datetime.strptime(text, date_format)
                return parsed.strftime("%Y-%m-%d")
            except Exception:
                continue

        patterns = [
            r"(\d{4})(\d{2})(\d{2})",
            r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})",
            r"(\d{4})年(\d{1,2})月(\d{1,2})日?",
            r"(\d{4})-(\d{2})",
            r"(\d{4})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            if len(match.groups()) == 3:
                year = match.group(1).zfill(4)
                month = match.group(2).zfill(2)
                day = match.group(3).zfill(2)
            elif len(match.groups()) == 2:
                year = match.group(1).zfill(4)
                month = match.group(2).zfill(2)
                day = "01"
            else:
                year = match.group(1).zfill(4)
                month = "01"
                day = "01"
            try:
                month_i = int(month)
                day_i = int(day)
                if 1 <= month_i <= 12 and 1 <= day_i <= 31:
                    return f"{year}-{month}-{day}"
            except Exception:
                continue
        return None

    def _is_not_later_than(self, candidate_date: Any, boundary_date: str) -> bool:
        normalized_candidate = self._normalize_date(candidate_date)
        normalized_boundary = self._normalize_date(boundary_date)
        if not normalized_boundary:
            return True
        if not normalized_candidate:
            return True
        return normalized_candidate <= normalized_boundary

    def _to_dict(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "dict"):
            return value.dict()
        return {}
