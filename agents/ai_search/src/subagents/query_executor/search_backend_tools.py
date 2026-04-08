"""Search backend tools used by the query-executor specialist."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from langchain.tools import ToolRuntime

from agents.ai_search.src.query_constraints import build_search_constraints, build_query_text, build_semantic_text
from agents.common.search_clients.factory import SearchClientFactory
from backend.storage.ai_search_support import stable_ai_search_document_id


def _json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _load_patent_details(pn: str) -> Dict[str, Any]:
    client = SearchClientFactory.get_client("zhihuiya")
    detail = client.get_patent_details(pn)
    detail = detail if isinstance(detail, dict) else {}
    return {
        "pn": pn,
        "title": str(detail.get("title") or detail.get("basic_info", {}).get("title") or "").strip(),
        "abstract": str(detail.get("abstract") or detail.get("basic_info", {}).get("abstract") or "").strip(),
        "claims": str(detail.get("claims") or detail.get("claims_info") or "").strip(),
        "description": str(detail.get("description") or detail.get("description_info") or "").strip(),
        "raw": detail,
    }


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


def build_search_tools(context: Any) -> List[Any]:
    storage = context.storage
    task_id = context.task_id

    def _existing_documents(plan_version: int) -> Dict[str, Dict[str, Any]]:
        documents = storage.list_ai_search_documents(task_id, int(plan_version))
        mapping: Dict[str, Dict[str, Any]] = {}
        for item in documents:
            pn = str(item.get("pn") or "").strip().upper()
            if pn:
                mapping[pn] = item
        return mapping

    def _candidate_record(
        existing: Optional[Dict[str, Any]],
        *,
        plan_version: int,
        batch_id: str,
        lane_type: str,
        sub_plan_id: str,
        step_id: str,
        raw_item: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        pn = str(raw_item.get("pn") or "").strip().upper()
        if not pn:
            return None
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
        return {
            "document_id": stable_ai_search_document_id(task_id, int(plan_version), pn),
            "task_id": task_id,
            "plan_version": int(plan_version),
            "pn": pn,
            "title": str(raw_item.get("title") or existing.get("title") or "").strip() if existing else str(raw_item.get("title") or "").strip(),
            "abstract": str(raw_item.get("abstract") or existing.get("abstract") or "").strip() if existing else str(raw_item.get("abstract") or "").strip(),
            "ipc_cpc_json": raw_item.get("cpc") or raw_item.get("ipc_cpc_json") or (existing.get("ipc_cpc_json") if existing else []) or [],
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
        existing_by_pn = _existing_documents(plan_version)
        records: List[Dict[str, Any]] = []
        new_unique_candidates = 0
        deduped_hits = 0
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            pn = str(item.get("pn") or "").strip().upper()
            if not pn:
                continue
            existing = existing_by_pn.get(pn)
            if existing:
                deduped_hits += 1
            else:
                new_unique_candidates += 1
            record = _candidate_record(
                existing,
                plan_version=int(plan_version),
                batch_id=batch_id,
                lane_type=lane_type,
                sub_plan_id=sub_plan_id,
                step_id=step_id,
                raw_item=item,
            )
            if record:
                records.append(record)
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
        count_boolean,
        fetch_patent_details,
        prepare_lane_queries,
    ]
