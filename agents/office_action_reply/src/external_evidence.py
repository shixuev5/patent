"""
外部证据聚合器
统一接入 OpenAlex（学术）、智慧芽（专利）和 Tavily（网页）检索能力。
"""

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from loguru import logger

from agents.common.search_clients.factory import SearchClientFactory


class ExternalEvidenceAggregator:
    """统一外部检索聚合器。"""

    def __init__(self):
        self.openalex_api_key = os.getenv("OPENALEX_API_KEY", "").strip()
        self.openalex_base_url = os.getenv("OPENALEX_BASE_URL", "https://api.openalex.org/works").strip()
        self.openalex_email = os.getenv("OPENALEX_EMAIL", "").strip()

        self.tavily_api_key = os.getenv("TAVILY_API_KEY", "").strip()
        self.tavily_base_url = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com/search").strip()

        self.zhihuiya_enabled = bool(
            os.getenv("ZHIHUIYA_USERNAME", "").strip() and os.getenv("ZHIHUIYA_PASSWORD", "").strip()
        )
        self.zhihuiya_min_similarity_score = self._safe_float(
            os.getenv("ZHIHUIYA_MIN_SIMILARITY_SCORE", "0"),
            default=0.0,
        )
        self.zhihuiya_client = None
        if self.zhihuiya_enabled:
            try:
                self.zhihuiya_client = SearchClientFactory.get_client("zhihuiya")
            except Exception as ex:
                logger.warning(f"初始化智慧芽客户端失败，后续将跳过专利证据检索: {ex}")
                self.zhihuiya_client = None

    def search_evidence(
        self,
        queries: Dict[str, List[str]],
        priority_date: Optional[str],
        limit: int = 8,
    ) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
        """
        聚合外部证据并统一编号。

        Returns:
            (evidence_list, retrieval_engines, retrieval_meta)
        """
        queries_by_engine = self._normalize_engine_queries(queries)
        if not any(queries_by_engine.values()):
            return [], [], {"retrieval": {}}

        candidates: List[Dict[str, Any]] = []
        engines: List[str] = []

        openalex_queries = queries_by_engine.get("openalex", [])
        openalex_hits = self._search_openalex(openalex_queries, priority_date, per_query=4)
        if openalex_hits:
            candidates.extend(openalex_hits)
            engines.append("openalex")

        zhihuiya_queries = queries_by_engine.get("zhihuiya", [])
        zhihuiya_hits = self._search_zhihuiya(
            zhihuiya_queries,
            priority_date,
            per_query=4,
            min_similarity_score=self.zhihuiya_min_similarity_score,
        )
        if zhihuiya_hits:
            candidates.extend(zhihuiya_hits)
            engines.append("zhihuiya")

        tavily_queries = queries_by_engine.get("tavily", [])
        tavily_hits = self._search_tavily(tavily_queries, priority_date, per_query=4)
        if tavily_hits:
            candidates.extend(tavily_hits)
            engines.append("tavily")

        merged = self._interleave_by_source(self._dedupe_results(candidates))
        final_results = merged[:limit]
        for index, item in enumerate(final_results, start=1):
            item["doc_id"] = f"EXT{index}"
        retrieval_meta = self._build_retrieval_meta(
            queries_by_engine=queries_by_engine,
            final_results=final_results,
            priority_date=priority_date,
        )
        return final_results, engines, retrieval_meta

    def _search_openalex(
        self,
        queries: List[str],
        priority_date: Optional[str],
        per_query: int,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        for query in queries:
            params: Dict[str, Any] = {
                "search": query,
                "per-page": per_query,
            }

            filters: List[str] = []
            if priority_date:
                filters.append(f"to_publication_date:{priority_date}")
            if filters:
                params["filter"] = ",".join(filters)
            if self.openalex_api_key:
                params["api_key"] = self.openalex_api_key
            if self.openalex_email:
                params["mailto"] = self.openalex_email

            try:
                response = requests.get(self.openalex_base_url, params=params, timeout=25)
                response.raise_for_status()
                data = response.json()
            except Exception as ex:
                logger.warning(f"OpenAlex 检索失败，query={query[:100]} error={ex}")
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
        queries: List[str],
        priority_date: Optional[str],
        per_query: int,
        min_similarity_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        if not self.zhihuiya_client:
            return []

        to_date = priority_date.replace("-", "") if priority_date else ""
        results: List[Dict[str, Any]] = []
        for query in queries:
            try:
                raw_docs = self.zhihuiya_client.search_semantic(query, to_date=to_date, limit=per_query) or []
            except Exception as ex:
                logger.warning(f"智慧芽语义检索失败，query={query[:100]} error={ex}")
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
                    "source_type": "zhihuiya_patent",
                    "title": title or pn,
                    "url": f"https://patents.google.com/patent/{pn}" if pn else "",
                    "snippet": abstract[:800],
                    "published": published,
                    "similarity_score": similarity_score,
                    "score": similarity_score,
                })

        return results

    def _search_tavily(
        self,
        queries: List[str],
        priority_date: Optional[str],
        per_query: int,
    ) -> List[Dict[str, Any]]:
        if not self.tavily_api_key:
            logger.warning("TAVILY_API_KEY 未配置，将跳过网页证据检索")
            return []

        results: List[Dict[str, Any]] = []
        for query in queries:
            payload = {
                "api_key": self.tavily_api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": per_query,
                "include_answer": False,
                "include_raw_content": False,
            }
            try:
                response = requests.post(self.tavily_base_url, json=payload, timeout=25)
                response.raise_for_status()
                data = response.json()
            except Exception as ex:
                logger.warning(f"Tavily 检索失败，query={query[:100]} error={ex}")
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
                    "source_type": "tavily_web",
                    "title": title,
                    "url": url,
                    "snippet": snippet[:800],
                    "published": published,
                })

        return results

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

    def _interleave_by_source(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for item in results:
            source_type = str(item.get("source_type", "")).strip() or "unknown"
            buckets.setdefault(source_type, []).append(item)

        ordered_source_types = [key for key in ["openalex", "zhihuiya_patent", "tavily_web"] if key in buckets]
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

    def _normalize_engine_queries(self, queries: Dict[str, List[str]]) -> Dict[str, List[str]]:
        engine_queries: Dict[str, List[str]] = {
            "openalex": [],
            "zhihuiya": [],
            "tavily": [],
        }

        if not isinstance(queries, dict):
            return engine_queries

        alias_map = {
            "openalex": "openalex",
            "scholar": "openalex",
            "academic": "openalex",
            "zhihuiya": "zhihuiya",
            "patent": "zhihuiya",
            "zhihuiya_patent": "zhihuiya",
            "tavily": "tavily",
            "web": "tavily",
            "tavily_web": "tavily",
        }
        for raw_key, raw_queries in queries.items():
            key = alias_map.get(str(raw_key).strip().lower())
            if not key:
                continue
            normalized = self._normalize_query_list(raw_queries if isinstance(raw_queries, list) else [], limit=4)
            if normalized:
                engine_queries[key] = normalized
        return engine_queries

    def _normalize_query_list(self, queries: List[Any], limit: int = 4) -> List[str]:
        normalized: List[str] = []
        for query in queries or []:
            value = " ".join(str(query).split())
            if value and value not in normalized:
                normalized.append(value)
            if len(normalized) >= limit:
                break
        return normalized

    def _build_retrieval_meta(
        self,
        queries_by_engine: Dict[str, List[str]],
        final_results: List[Dict[str, Any]],
        priority_date: Optional[str],
    ) -> Dict[str, Any]:
        retrieval: Dict[str, Dict[str, Any]] = {}
        for engine, engine_queries in queries_by_engine.items():
            if not engine_queries:
                continue
            filters: Dict[str, Any] = {}
            if priority_date:
                filters["priority_date_lte"] = priority_date
            if engine == "zhihuiya":
                filters["min_similarity_score"] = self.zhihuiya_min_similarity_score
            retrieval[engine] = {
                "queries": engine_queries,
                "filters": filters,
                "result_count": 0,
                "results": [],
            }

        for item in final_results:
            source_type = str(item.get("source_type", "")).strip()
            engine = self._source_to_engine(source_type)
            if not engine:
                continue
            stat = retrieval.setdefault(engine, {
                "queries": [],
                "filters": {},
                "result_count": 0,
                "results": [],
            })
            stat["result_count"] += 1
            stat["results"].append({
                "doc_id": str(item.get("doc_id", "")).strip() or None,
                "source_type": source_type,
                "title": str(item.get("title", "")).strip(),
                "url": str(item.get("url", "")).strip() or None,
                "published": str(item.get("published", "")).strip() or None,
                "similarity_score": self._safe_float(item.get("similarity_score"), default=0.0)
                if item.get("similarity_score") is not None else None,
            })
        return {"retrieval": retrieval}

    def _source_to_engine(self, source_type: str) -> str:
        if source_type == "openalex":
            return "openalex"
        if source_type == "zhihuiya_patent":
            return "zhihuiya"
        if source_type == "tavily_web":
            return "tavily"
        return ""

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
