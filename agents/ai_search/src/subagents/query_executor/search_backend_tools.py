"""检索执行子代理使用的检索后端工具。"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from html import unescape
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from langchain.tools import ToolRuntime

from agents.ai_search.src.query_constraints import build_search_constraints, build_query_text, build_semantic_text
from agents.common.retrieval.academic_query_utils import (
    to_crossref_bibliographic_query,
    to_semantic_academic_query,
)
from agents.common.retrieval.academic_search import AcademicSearchClient
from agents.common.search_clients.factory import SearchClientFactory
from backend.storage.ai_search_support import (
    build_ai_search_canonical_id,
    stable_ai_search_document_id,
)


def _json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _load_patent_details(pn: str) -> Dict[str, Any]:
    client = SearchClientFactory.get_client("zhihuiya")
    detail = client.get_patent_detail(pn)
    detail = detail if isinstance(detail, dict) else {}
    return {
        "pn": pn,
        "title": str(detail.get("title") or detail.get("basic_info", {}).get("title") or "").strip(),
        "abstract": str(detail.get("abstract") or detail.get("basic_info", {}).get("abstract") or "").strip(),
        "claims": str(detail.get("claims") or detail.get("claims_info") or "").strip(),
        "description": str(detail.get("description") or detail.get("description_info") or "").strip(),
        "publication_date": str(detail.get("publication_date") or "").strip(),
        "application_date": str(detail.get("application_date") or "").strip(),
        "ipc": detail.get("ipc") if isinstance(detail.get("ipc"), list) else [],
        "cpc": detail.get("cpc") if isinstance(detail.get("cpc"), list) else [],
        "raw": detail,
    }


@lru_cache(maxsize=1)
def _academic_aggregator() -> AcademicSearchClient:
    return AcademicSearchClient()


def _detail_fingerprint(detail: Dict[str, Any]) -> Optional[str]:
    text = "\n".join(
        [
            str(detail.get("title") or "").strip(),
            str(detail.get("abstract") or "").strip(),
            str(detail.get("claims") or "").strip(),
            str(detail.get("description") or "").strip(),
        ]
    ).strip()
    if not text:
        return None
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _normalized_ipc_list(raw_item: Dict[str, Any], existing: Optional[Dict[str, Any]]) -> List[str]:
    values: List[str] = []
    for source in (
        raw_item.get("ipc"),
        raw_item.get("cpc"),
        raw_item.get("ipc_cpc_json"),
        existing.get("ipc_cpc_json") if existing else [],
    ):
        if not isinstance(source, list):
            continue
        for value in source:
            text = str(value or "").strip()
            if text and text not in values:
                values.append(text)
    return values


def _primary_ipc(raw_item: Dict[str, Any], existing: Optional[Dict[str, Any]]) -> str:
    for source in (
        raw_item.get("ipc"),
        existing.get("primary_ipc") if existing else "",
        raw_item.get("cpc"),
        raw_item.get("ipc_cpc_json"),
        existing.get("ipc_cpc_json") if existing else [],
    ):
        if isinstance(source, list):
            for value in source:
                text = str(value or "").strip()
                if text:
                    return text
        else:
            text = str(source or "").strip()
            if text:
                return text
    return ""


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _extract_openalex_abstract(item: Dict[str, Any]) -> str:
    inverted = item.get("abstract_inverted_index")
    if not isinstance(inverted, dict):
        return ""
    tokens_by_pos: Dict[int, str] = {}
    for token, positions in inverted.items():
        if not isinstance(positions, list):
            continue
        for raw_pos in positions:
            try:
                pos = int(raw_pos)
            except Exception:
                continue
            tokens_by_pos[pos] = str(token or "")
    if not tokens_by_pos:
        return ""
    return _normalized_text(" ".join(tokens_by_pos[index] for index in sorted(tokens_by_pos.keys())))


def _first_text(values: Any) -> str:
    if isinstance(values, list):
        for item in values:
            text = _normalized_text(item)
            if text:
                return text
        return ""
    return _normalized_text(values)


def _strip_html_tags(text: str) -> str:
    in_tag = False
    output: List[str] = []
    for char in str(text or ""):
        if char == "<":
            in_tag = True
            continue
        if char == ">":
            in_tag = False
            output.append(" ")
            continue
        if not in_tag:
            output.append(char)
    return _normalized_text(unescape("".join(output)))


def _extract_crossref_abstract(item: Dict[str, Any]) -> str:
    return _strip_html_tags(str(item.get("abstract") or ""))


def _extract_crossref_date(item: Dict[str, Any]) -> str:
    for key in ("published-print", "published-online", "issued"):
        value = item.get(key)
        if not isinstance(value, dict):
            continue
        parts = value.get("date-parts")
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
            continue
        date_parts = parts[0]
        year = str(date_parts[0]).strip() if len(date_parts) >= 1 else ""
        month = str(date_parts[1]).strip().zfill(2) if len(date_parts) >= 2 else ""
        day = str(date_parts[2]).strip().zfill(2) if len(date_parts) >= 3 else ""
        if year and month and day:
            return f"{year}-{month}-{day}"
        if year and month:
            return f"{year}-{month}"
        if year:
            return year
    return ""


def _external_id_from_url(url: Any) -> str:
    parsed = urlparse(str(url or "").strip())
    if not parsed.path:
        return ""
    return parsed.path.rstrip("/").split("/")[-1].strip()


def _normalize_patent_result(raw_item: Dict[str, Any]) -> Dict[str, Any]:
    pn = _normalized_text(raw_item.get("pn")).upper()
    url = _normalized_text(raw_item.get("url")) or (f"https://patents.google.com/patent/{pn}" if pn else "")
    source_type = str(raw_item.get("source_type") or "patent").strip().lower() or "patent"
    if source_type == "zhihuiya":
        source_type = "patent"
    canonical_id = build_ai_search_canonical_id(
        source_type=source_type,
        external_id=pn,
        pn=pn,
    )
    return {
        "source_type": source_type,
        "external_id": pn,
        "canonical_id": canonical_id,
        "pn": pn,
        "doi": "",
        "url": url,
        "title": _normalized_text(raw_item.get("title")),
        "abstract": _normalized_text(raw_item.get("abstract")),
        "venue": "",
        "language": _normalized_text(raw_item.get("language")),
        "publication_date": _normalized_text(raw_item.get("publication_date")),
        "application_date": _normalized_text(raw_item.get("application_date")),
        "primary_ipc": _normalized_text(raw_item.get("primary_ipc")),
        "ipc": raw_item.get("ipc") if isinstance(raw_item.get("ipc"), list) else [],
        "cpc": raw_item.get("cpc") if isinstance(raw_item.get("cpc"), list) else [],
        "ipc_cpc_json": raw_item.get("ipc_cpc_json") if isinstance(raw_item.get("ipc_cpc_json"), list) else [],
        "detail_source": _normalized_text(raw_item.get("detail_source")),
        "score": raw_item.get("score"),
    }


def _normalize_academic_result(raw_item: Dict[str, Any]) -> Dict[str, Any]:
    source_type = str(raw_item.get("source_type") or "").strip().lower()
    doi = _normalized_text(raw_item.get("doi"))
    external_id = _normalized_text(raw_item.get("external_id"))
    url = _normalized_text(raw_item.get("url"))
    if not external_id:
        external_id = _external_id_from_url(raw_item.get("id") or url)
    canonical_id = build_ai_search_canonical_id(
        source_type=source_type,
        external_id=external_id,
        doi=doi,
    )
    return {
        "source_type": source_type,
        "external_id": external_id,
        "canonical_id": canonical_id,
        "pn": "",
        "doi": doi,
        "url": url,
        "title": _normalized_text(raw_item.get("title")),
        "abstract": _normalized_text(raw_item.get("abstract")),
        "venue": _normalized_text(raw_item.get("venue")),
        "language": _normalized_text(raw_item.get("language")),
        "publication_date": _normalized_text(raw_item.get("publication_date")),
        "application_date": "",
        "primary_ipc": "",
        "ipc_cpc_json": [],
        "detail_source": _normalized_text(raw_item.get("detail_source")) or "abstract_only",
        "score": raw_item.get("score"),
    }


def _normalize_result_item(raw_item: Dict[str, Any]) -> Dict[str, Any]:
    source_type = str(raw_item.get("source_type") or "").strip().lower()
    if source_type in {"openalex", "semanticscholar", "crossref"}:
        return _normalize_academic_result(raw_item)
    return _normalize_patent_result(raw_item)


def build_search_tools(context: Any) -> List[Any]:
    storage = context.storage
    task_id = context.task_id

    def _existing_documents(plan_version: int) -> Dict[str, Dict[str, Any]]:
        documents = storage.list_ai_search_documents(task_id, int(plan_version))
        mapping: Dict[str, Dict[str, Any]] = {}
        for item in documents:
            canonical_id = _normalized_text(item.get("canonical_id")) or build_ai_search_canonical_id(
                source_type=item.get("source_type"),
                external_id=item.get("external_id") or item.get("pn"),
                doi=item.get("doi"),
                pn=item.get("pn"),
            )
            if canonical_id:
                mapping[canonical_id] = item
        return mapping

    def _candidate_record(
        existing: Optional[Dict[str, Any]],
        *,
        run_id: str,
        plan_version: int,
        batch_id: str,
        lane_type: str,
        sub_plan_id: str,
        step_id: str,
        raw_item: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        normalized = _normalize_result_item(raw_item)
        canonical_id = _normalized_text(normalized.get("canonical_id"))
        if not canonical_id:
            return None
        pn = _normalized_text(normalized.get("pn")).upper()
        source_batches = list(existing.get("source_batches_json") or []) if existing else []
        if batch_id and batch_id not in source_batches:
            source_batches.append(batch_id)
        source_lanes = list(existing.get("source_lanes_json") or []) if existing else []
        if lane_type and lane_type not in source_lanes:
            source_lanes.append(lane_type)
        source_sub_plans = list(existing.get("source_sub_plans_json") or []) if existing else []
        if sub_plan_id and sub_plan_id not in source_sub_plans:
            source_sub_plans.append(sub_plan_id)
        source_steps = list(existing.get("source_steps_json") or []) if existing else []
        if step_id and step_id not in source_steps:
            source_steps.append(step_id)
        document_id = str(existing.get("document_id") or "").strip() if existing else ""
        if not document_id:
            document_id = stable_ai_search_document_id(
                task_id,
                int(plan_version),
                canonical_id,
                fallback_seed=pn or _normalized_text(normalized.get("external_id")) or _normalized_text(normalized.get("title")),
            )
        resolved_source_type = _normalized_text(normalized.get("source_type")) or (
            str(existing.get("source_type") or "patent").strip() if existing else "patent"
        )
        resolved_external_id = _normalized_text(normalized.get("external_id")) or (
            str(existing.get("external_id") or "").strip() if existing else ""
        )
        resolved_doi = _normalized_text(normalized.get("doi")) or (str(existing.get("doi") or "").strip() if existing else "")
        resolved_url = _normalized_text(normalized.get("url")) or (str(existing.get("url") or "").strip() if existing else "")
        resolved_title = _normalized_text(normalized.get("title")) or (str(existing.get("title") or "").strip() if existing else "")
        resolved_abstract = _normalized_text(normalized.get("abstract")) or (
            str(existing.get("abstract") or "").strip() if existing else ""
        )
        resolved_venue = _normalized_text(normalized.get("venue")) or (str(existing.get("venue") or "").strip() if existing else "")
        resolved_language = _normalized_text(normalized.get("language")) or (
            str(existing.get("language") or "").strip() if existing else ""
        )
        resolved_publication_date = _normalized_text(normalized.get("publication_date")) or (
            str(existing.get("publication_date") or "").strip() if existing else ""
        )
        resolved_application_date = _normalized_text(normalized.get("application_date")) or (
            str(existing.get("application_date") or "").strip() if existing else ""
        )
        resolved_detail_source = _normalized_text(normalized.get("detail_source")) or (
            str(existing.get("detail_source") or "").strip() if existing else ""
        )
        return {
            "run_id": run_id,
            "document_id": document_id,
            "task_id": task_id,
            "plan_version": int(plan_version),
            "source_type": resolved_source_type,
            "external_id": resolved_external_id,
            "canonical_id": canonical_id,
            "pn": pn,
            "doi": resolved_doi,
            "url": resolved_url,
            "title": resolved_title,
            "abstract": resolved_abstract,
            "venue": resolved_venue,
            "language": resolved_language,
            "publication_date": resolved_publication_date,
            "application_date": resolved_application_date,
            "primary_ipc": _primary_ipc(normalized, existing),
            "ipc_cpc_json": _normalized_ipc_list(normalized, existing),
            "source_batches_json": source_batches,
            "source_lanes_json": source_lanes,
            "source_sub_plans_json": source_sub_plans,
            "source_steps_json": source_steps,
            "stage": str(existing.get("stage") or "candidate") if existing else "candidate",
            "score": raw_item.get("score") if raw_item.get("score") is not None else (existing.get("score") if existing else None),
            "agent_reason": str(existing.get("agent_reason") or "") if existing else "",
            "key_passages_json": list(existing.get("key_passages_json") or []) if existing else [],
            "user_pinned": bool(existing.get("user_pinned")) if existing else False,
            "user_removed": bool(existing.get("user_removed")) if existing else False,
            "coarse_status": str(existing.get("coarse_status") or "pending") if existing else "pending",
            "coarse_reason": str(existing.get("coarse_reason") or "") if existing else "",
            "coarse_screened_at": existing.get("coarse_screened_at") if existing else None,
            "close_read_status": str(existing.get("close_read_status") or "pending") if existing else "pending",
            "close_read_reason": str(existing.get("close_read_reason") or "") if existing else "",
            "close_read_at": existing.get("close_read_at") if existing else None,
            "detail_fingerprint": existing.get("detail_fingerprint") if existing else None,
            "detail_source": resolved_detail_source,
        }

    def _persist_search_results(
        raw_result: Any,
        *,
        plan_version: int,
        batch_id: str,
        lane_type: str,
        sub_plan_id: str,
        step_id: str,
        executed_tool: str,
    ) -> str:
        payload = raw_result if isinstance(raw_result, dict) else {}
        raw_items = payload.get("results") if isinstance(payload.get("results"), list) else []
        existing_by_canonical = _existing_documents(plan_version)
        run_id = context.active_run_id(plan_version)
        records: List[Dict[str, Any]] = []
        new_unique_candidates = 0
        deduped_hits = 0
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            normalized = _normalize_result_item(item)
            canonical_id = _normalized_text(normalized.get("canonical_id"))
            if not canonical_id:
                continue
            existing = existing_by_canonical.get(canonical_id)
            if existing:
                deduped_hits += 1
            else:
                new_unique_candidates += 1
            record = _candidate_record(
                existing,
                run_id=run_id,
                plan_version=int(plan_version),
                batch_id=batch_id,
                lane_type=lane_type,
                sub_plan_id=sub_plan_id,
                step_id=step_id,
                raw_item=normalized,
            )
            if record:
                records.append(record)
                existing_by_canonical[canonical_id] = record
        if records:
            storage.upsert_ai_search_documents(records)
        candidate_pool_size = len(storage.list_ai_search_documents(task_id, int(plan_version)))
        return _json_dumps(
            {
                "lane_type": lane_type,
                "batch_id": batch_id,
                "executed_tool": executed_tool,
                "new_unique_candidates": new_unique_candidates,
                "deduped_hits": deduped_hits,
                "candidate_pool_size": candidate_pool_size,
                "result_count": len(raw_items),
                "stop_signal": "no_progress" if len(raw_items) == 0 and candidate_pool_size == 0 else "",
            }
        )

    def search_trace(
        plan_version: int,
        batch_id: str,
        seed_pn: str,
        limit: int = 20,
        cutoff_date: str = "",
        applicant_terms: Optional[List[str]] = None,
        sub_plan_id: str = "",
        step_id: str = "",
        runtime: ToolRuntime = None,
    ) -> str:
        """
        调用智慧芽相似/追踪检索，并把命中文献写入候选池。
        """
        _ = cutoff_date, applicant_terms
        client = SearchClientFactory.get_client("zhihuiya")
        if not hasattr(client, "get_similar_patents"):
            return _json_dumps(
                {
                    "lane_type": "trace",
                    "batch_id": batch_id,
                    "executed_tool": "search_trace",
                    "new_unique_candidates": 0,
                    "deduped_hits": 0,
                    "candidate_pool_size": len(storage.list_ai_search_documents(task_id, int(plan_version))),
                    "result_count": 0,
                    "stop_signal": "trace_unavailable",
                }
            )
        resolved_step_id = str(step_id or (context.current_todo() or {}).get("step_id") or "").strip()
        result = client.get_similar_patents(str(seed_pn or "").strip().upper(), limit=int(limit or 20))
        output = _persist_search_results(
            result,
            plan_version=int(plan_version),
            batch_id=batch_id,
            lane_type="trace",
            sub_plan_id=str(sub_plan_id or "").strip(),
            step_id=resolved_step_id,
            executed_tool="search_trace",
        )
        context.notify_snapshot_changed(runtime, reason="documents")
        return output

    def search_semantic(
        plan_version: int,
        batch_id: str,
        query_text: str,
        limit: int = 50,
        cutoff_date: str = "",
        applicant_terms: Optional[List[str]] = None,
        sub_plan_id: str = "",
        step_id: str = "",
        runtime: ToolRuntime = None,
    ) -> str:
        """
        调用智慧芽语义检索，并把命中文献写入候选池。
        """
        _ = applicant_terms
        resolved_step_id = str(step_id or (context.current_todo() or {}).get("step_id") or "").strip()
        client = SearchClientFactory.get_client("zhihuiya")
        result = client.search_semantic(
            str(query_text or "").strip(),
            to_date=str(cutoff_date or "").strip(),
            limit=int(limit or 50),
        )
        output = _persist_search_results(
            result,
            plan_version=int(plan_version),
            batch_id=batch_id,
            lane_type="semantic",
            sub_plan_id=str(sub_plan_id or "").strip(),
            step_id=resolved_step_id,
            executed_tool="search_semantic",
        )
        context.notify_snapshot_changed(runtime, reason="documents")
        return output

    def search_boolean(
        plan_version: int,
        batch_id: str,
        query_text: str,
        limit: int = 50,
        sub_plan_id: str = "",
        step_id: str = "",
        runtime: ToolRuntime = None,
    ) -> str:
        """
        调用智慧芽布尔检索，并把命中文献写入候选池。
        """
        resolved_step_id = str(step_id or (context.current_todo() or {}).get("step_id") or "").strip()
        client = SearchClientFactory.get_client("zhihuiya")
        result = client.search(str(query_text or "").strip(), limit=int(limit or 50))
        if not isinstance(result, dict):
            result = {"total": 0, "results": []}
        output = _persist_search_results(
            result,
            plan_version=int(plan_version),
            batch_id=batch_id,
            lane_type="boolean",
            sub_plan_id=str(sub_plan_id or "").strip(),
            step_id=resolved_step_id,
            executed_tool="search_boolean",
        )
        context.notify_snapshot_changed(runtime, reason="documents")
        return output

    def search_academic_openalex(
        plan_version: int,
        batch_id: str,
        query_text: str,
        limit: int = 20,
        cutoff_date: str = "",
        sub_plan_id: str = "",
        step_id: str = "",
        runtime: ToolRuntime = None,
    ) -> str:
        """调用 OpenAlex 检索非专文献，并把命中文献写入候选池。"""
        resolved_step_id = str(step_id or (context.current_todo() or {}).get("step_id") or "").strip()
        aggregator = _academic_aggregator()
        results = [
            {**item, "detail_source": "abstract_only"}
            for item in aggregator.search_openalex(
                query=_normalized_text(query_text),
                priority_date=_normalized_text(cutoff_date) or None,
                per_query=max(int(limit or 20), 1),
            )
        ]
        output = _persist_search_results(
            {"results": results},
            plan_version=int(plan_version),
            batch_id=batch_id,
            lane_type="openalex",
            sub_plan_id=str(sub_plan_id or "").strip(),
            step_id=resolved_step_id,
            executed_tool="search_academic_openalex",
        )
        context.notify_snapshot_changed(runtime, reason="documents")
        return output

    def search_academic_semanticscholar(
        plan_version: int,
        batch_id: str,
        query_text: str,
        limit: int = 20,
        cutoff_date: str = "",
        sub_plan_id: str = "",
        step_id: str = "",
        runtime: ToolRuntime = None,
    ) -> str:
        """调用 Semantic Scholar 检索非专文献，并把命中文献写入候选池。"""
        resolved_step_id = str(step_id or (context.current_todo() or {}).get("step_id") or "").strip()
        aggregator = _academic_aggregator()
        results = [
            {
                **item,
                "detail_source": "abstract_only",
                "score": item.get("citation_count"),
            }
            for item in aggregator.search_semanticscholar(
                query=_normalized_text(query_text),
                priority_date=_normalized_text(cutoff_date) or None,
                per_query=max(int(limit or 20), 1),
            )
        ]
        output = _persist_search_results(
            {"results": results},
            plan_version=int(plan_version),
            batch_id=batch_id,
            lane_type="semanticscholar",
            sub_plan_id=str(sub_plan_id or "").strip(),
            step_id=resolved_step_id,
            executed_tool="search_academic_semanticscholar",
        )
        context.notify_snapshot_changed(runtime, reason="documents")
        return output

    def search_academic_crossref(
        plan_version: int,
        batch_id: str,
        query_text: str,
        limit: int = 20,
        cutoff_date: str = "",
        sub_plan_id: str = "",
        step_id: str = "",
        runtime: ToolRuntime = None,
    ) -> str:
        """调用 Crossref 检索非专文献，并把命中文献写入候选池。"""
        resolved_step_id = str(step_id or (context.current_todo() or {}).get("step_id") or "").strip()
        aggregator = _academic_aggregator()
        results = [
            {**item, "detail_source": "abstract_only"}
            for item in aggregator.search_crossref(
                query=_normalized_text(query_text),
                priority_date=_normalized_text(cutoff_date) or None,
                per_query=max(int(limit or 20), 1),
            )
        ]
        output = _persist_search_results(
            {"results": results},
            plan_version=int(plan_version),
            batch_id=batch_id,
            lane_type="crossref",
            sub_plan_id=str(sub_plan_id or "").strip(),
            step_id=resolved_step_id,
            executed_tool="search_academic_crossref",
        )
        context.notify_snapshot_changed(runtime, reason="documents")
        return output

    def count_boolean(query_text: str) -> str:
        """
        低成本估算布尔检索结果规模。
        """
        client = SearchClientFactory.get_client("zhihuiya")
        count = 0
        if hasattr(client, "_query_patent_info_by_count"):
            info = client._query_patent_info_by_count(str(query_text or "").strip())  # type: ignore[attr-defined]
            if isinstance(info, dict):
                count = int(info.get("TOTAL") or info.get("total") or 0)
        return _json_dumps({"query_text": str(query_text or "").strip(), "count": count})

    def fetch_patent_details(pn: str) -> str:
        """
        拉取单篇专利详情，并回写详情指纹，供精读缓存判断。
        """
        detail = _load_patent_details(str(pn or "").strip().upper())
        fingerprint = _detail_fingerprint(detail)
        return _json_dumps({**detail, "detail_fingerprint": fingerprint})

    def prepare_lane_queries(plan_version: int, batch_payload_json: str, search_elements_json: str, lane_type: str) -> str:
        """
        根据 batch 与检索要素生成对应 lane 的执行文本。
        """
        try:
            batch = json.loads(batch_payload_json) if batch_payload_json else {}
        except Exception:
            batch = {}
        try:
            search_elements = json.loads(search_elements_json) if search_elements_json else {}
        except Exception:
            search_elements = {}
        batch = {
            **batch,
            "lane_type": str(lane_type or "").strip(),
        }
        constraints = build_search_constraints(search_elements)
        academic_query_text = build_query_text(batch, {})
        payload: Dict[str, Any] = {
            "plan_version": int(plan_version),
            "lane_type": str(lane_type or "").strip(),
            "batch_id": str(batch.get("batch_id") or "").strip(),
            "sub_plan_id": str(batch.get("sub_plan_id") or "").strip(),
            "gap_type": str(batch.get("gap_type") or "").strip(),
            "claim_id": str(batch.get("claim_id") or "").strip(),
            "limitation_id": str(batch.get("limitation_id") or "").strip(),
            "seed_terms": batch.get("seed_terms") or [],
            "pivot_terms": batch.get("pivot_terms") or [],
            "query_text": build_query_text(batch, search_elements),
            "semantic_text": build_semantic_text(batch, search_elements),
            "academic_query_text": academic_query_text,
            "academic_semantic_text": to_semantic_academic_query(academic_query_text),
            "crossref_query_text": to_crossref_bibliographic_query(academic_query_text),
            "cutoff_date": str(constraints.get("cutoff_date_yyyymmdd") or ""),
            "applicant_terms": constraints.get("applicant_terms") or [],
            "result_limit": int(batch.get("result_limit") or 50),
            "seed_pn": str(batch.get("seed_pn") or batch.get("seed_publication_number") or "").strip().upper(),
        }
        return _json_dumps(payload)

    return [
        search_trace,
        search_semantic,
        search_boolean,
        search_academic_openalex,
        search_academic_semanticscholar,
        search_academic_crossref,
        count_boolean,
        fetch_patent_details,
        prepare_lane_queries,
    ]
