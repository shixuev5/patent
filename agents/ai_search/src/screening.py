"""
AI 检索粗筛、精读与特征对比上下文构造。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from agents.ai_search.main import build_close_reader_agent, build_coarse_screener_agent, extract_structured_response
from agents.ai_search.src.query_constraints import build_search_constraints
from agents.common.retrieval.local_evidence_retriever import LocalEvidenceRetriever
from agents.common.search_clients.factory import SearchClientFactory
from backend.time_utils import utc_now_z


DEFAULT_COARSE_CHUNK_SIZE = 20
DEFAULT_SHORTLIST_LIMIT = 20
DEFAULT_SELECTED_LIMIT = 10
DEFAULT_KEY_PASSAGES_LIMIT = 6
DEFAULT_PASSAGE_PREVIEW_CHARS = 400


def detail_to_text(detail: Dict[str, Any]) -> str:
    parts = [
        str(detail.get("title") or ""),
        str(detail.get("abstract") or ""),
        str(detail.get("claims") or ""),
        str(detail.get("description") or ""),
    ]
    return "\n\n".join(part.strip() for part in parts if str(part).strip())


def _detail_fingerprint(detail: Dict[str, Any]) -> str:
    text = detail_to_text(detail)
    return hashlib.sha1(text.encode("utf-8")).hexdigest() if text else ""


def load_document_details(pn: str) -> Dict[str, Any]:
    client = SearchClientFactory.get_client("zhihuiya")
    detail = client.get_patent_details(pn)
    detail = detail if isinstance(detail, dict) else {}
    normalized = {
        "pn": pn,
        "title": str(detail.get("title") or detail.get("basic_info", {}).get("title") or "").strip(),
        "abstract": str(detail.get("abstract") or detail.get("basic_info", {}).get("abstract") or "").strip(),
        "claims": str(detail.get("claims") or detail.get("claims_info") or "").strip(),
        "description": str(detail.get("description") or detail.get("description_info") or "").strip(),
        "raw": detail,
    }
    normalized["detail_fingerprint"] = _detail_fingerprint(normalized)
    return normalized


def fallback_passages(text: str, terms: List[str]) -> List[Dict[str, Any]]:
    if not text:
        return []
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", text) if item.strip()]
    scored: List[tuple[int, str]] = []
    for paragraph in paragraphs:
        lowered = paragraph.lower()
        score = sum(1 for term in terms if term.lower() in lowered)
        if score > 0:
            scored.append((score, paragraph))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "document_id": "",
            "passage": paragraph[:DEFAULT_PASSAGE_PREVIEW_CHARS],
            "reason": "关键词命中",
            "location": f"paragraph_{index + 1}",
        }
        for index, (_, paragraph) in enumerate(scored[:DEFAULT_KEY_PASSAGES_LIMIT])
    ]


def collect_key_terms(search_elements: Dict[str, Any]) -> List[str]:
    terms: List[str] = []
    for element in search_elements.get("search_elements") or []:
        if not isinstance(element, dict):
            continue
        for key in ("keywords_zh", "keywords_en"):
            for value in element.get(key) or []:
                text = str(value or "").strip()
                if text and text not in terms:
                    terms.append(text)
    return terms[:24]


def build_local_evidence(db_path: str, details: List[Dict[str, Any]], search_elements: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    terms = collect_key_terms(search_elements)
    documents = [
        {
            "doc_id": item["pn"],
            "title": item.get("title") or item["pn"],
            "content": detail_to_text(item),
            "source_type": "comparison_document",
        }
        for item in details
    ]
    index_path = Path(db_path)
    evidence_map: Dict[str, List[Dict[str, Any]]] = {}
    try:
        retriever = LocalEvidenceRetriever(str(index_path))
        retriever.build_index(documents)
        for item in details:
            candidates = retriever.search(" ".join(terms), intent="evidence", doc_filters=[item["pn"]], top_k=DEFAULT_KEY_PASSAGES_LIMIT)
            cards = retriever.build_evidence_cards(
                candidates,
                context_k=DEFAULT_KEY_PASSAGES_LIMIT,
                max_context_chars=DEFAULT_PASSAGE_PREVIEW_CHARS * DEFAULT_KEY_PASSAGES_LIMIT,
                max_quote_chars=DEFAULT_PASSAGE_PREVIEW_CHARS,
            )
            evidence_map[item["pn"]] = [
                {
                    "document_id": item["pn"],
                    "passage": card.get("quote", ""),
                    "reason": card.get("analysis", ""),
                    "location": card.get("location"),
                }
                for card in cards.get("cards") or []
            ]
    except Exception:
        for item in details:
            passages = fallback_passages(detail_to_text(item), terms)
            for passage in passages:
                passage["document_id"] = item["pn"]
            evidence_map[item["pn"]] = passages
    return evidence_map


def build_coarse_prompt(search_elements: Dict[str, Any], documents: List[Dict[str, Any]]) -> str:
    constraints = build_search_constraints(search_elements)
    return (
        "根据检索要素对下面候选文献做粗筛，只能基于标题、摘要、分类号和来源批次判断。\n"
        f"检索边界:\n{json.dumps(constraints, ensure_ascii=False)}\n"
        f"检索要素:\n{json.dumps(search_elements, ensure_ascii=False)}\n"
        f"候选文献:\n{json.dumps(documents, ensure_ascii=False)}"
    )


def build_close_reader_prompt(search_elements: Dict[str, Any], documents: List[Dict[str, Any]], evidence_map: Dict[str, List[Dict[str, Any]]]) -> str:
    constraints = build_search_constraints(search_elements)
    payload = []
    for item in documents:
        payload.append(
            {
                "document_id": item["document_id"],
                "pn": item["pn"],
                "title": item["title"],
                "abstract": item["abstract"],
                "claims": item.get("claims", ""),
                "description_excerpt": item.get("description", "")[:4000],
                "evidence": evidence_map.get(item["pn"], []),
            }
        )
    return (
        "请根据检索要素与证据段落，对 shortlisted 文献进行精读，判断是否纳入对比文件。\n"
        f"检索边界:\n{json.dumps(constraints, ensure_ascii=False)}\n"
        f"检索要素:\n{json.dumps(search_elements, ensure_ascii=False)}\n"
        f"shortlist 文献:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def build_feature_prompt(search_elements: Dict[str, Any], selected_documents: List[Dict[str, Any]]) -> str:
    constraints = build_search_constraints(search_elements)
    payload = []
    for item in selected_documents:
        payload.append(
            {
                "document_id": item["document_id"],
                "pn": item["pn"],
                "title": item["title"],
                "abstract": item["abstract"],
                "key_passages": item.get("key_passages_json") or [],
            }
        )
    return (
        "请基于检索要素和已选对比文件，输出特征对比表。\n"
        f"检索边界:\n{json.dumps(constraints, ensure_ascii=False)}\n"
        f"检索要素:\n{json.dumps(search_elements, ensure_ascii=False)}\n"
        f"已选对比文件:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def run_screening_pipeline(storage: Any, task_id: str, plan_version: int) -> Dict[str, Any]:
    plan = storage.get_ai_search_plan(task_id, int(plan_version)) or {}
    search_elements = plan.get("search_elements_json") if isinstance(plan.get("search_elements_json"), dict) else {}
    candidate_records = storage.list_ai_search_documents(task_id, int(plan_version))
    if not candidate_records:
        return {"candidate_records": [], "shortlist_records": [], "selected_ids": [], "selected_count": 0}

    pending_coarse = [
        item
        for item in candidate_records
        if str(item.get("coarse_status") or "pending") == "pending" and str(item.get("stage") or "") in {"candidate", ""}
    ]

    coarse_agent = build_coarse_screener_agent()
    shortlisted_ids: List[str] = []
    rejected_ids: List[str] = []
    if pending_coarse:
        for start in range(0, len(pending_coarse), DEFAULT_COARSE_CHUNK_SIZE):
            chunk = pending_coarse[start : start + DEFAULT_COARSE_CHUNK_SIZE]
            result = coarse_agent.invoke({"messages": [{"role": "user", "content": build_coarse_prompt(search_elements, chunk)}]})
            structured = extract_structured_response(result)
            shortlisted_ids.extend([item for item in structured.get("keep") or [] if item not in shortlisted_ids])
            rejected_ids.extend([item for item in structured.get("discard") or [] if item not in rejected_ids])
            if len(shortlisted_ids) >= DEFAULT_SHORTLIST_LIMIT:
                shortlisted_ids = shortlisted_ids[:DEFAULT_SHORTLIST_LIMIT]
                break

        shortlist_id_set = set(shortlisted_ids[:DEFAULT_SHORTLIST_LIMIT])
        for item in pending_coarse:
            document_id = str(item.get("document_id") or "")
            if document_id in shortlist_id_set:
                storage.update_ai_search_document(
                    task_id,
                    int(plan_version),
                    document_id,
                    stage="shortlisted",
                    coarse_status="kept",
                    coarse_reason="粗筛保留",
                    coarse_screened_at=utc_now_z(),
                )
            elif document_id in rejected_ids:
                storage.update_ai_search_document(
                    task_id,
                    int(plan_version),
                    document_id,
                    stage="rejected",
                    coarse_status="discarded",
                    coarse_reason="粗筛排除",
                    coarse_screened_at=utc_now_z(),
                )

    current_records = storage.list_ai_search_documents(task_id, int(plan_version))
    pending_close = [
        item
        for item in current_records
        if str(item.get("coarse_status") or "") == "kept" and str(item.get("close_read_status") or "pending") == "pending"
    ][:DEFAULT_SHORTLIST_LIMIT]

    selected_ids: List[str] = []
    if pending_close:
        detail_records: List[Dict[str, Any]] = []
        for item in pending_close:
            detail = load_document_details(str(item.get("pn") or "").strip().upper())
            detail_records.append({**item, **detail})
            storage.update_ai_search_document(
                task_id,
                int(plan_version),
                    item["document_id"],
                    detail_fingerprint=detail.get("detail_fingerprint"),
                )
        db_path = Path(getattr(storage, "db_path", Path.cwd()))
        index_path = db_path.parent / f"ai_search_{task_id}_{plan_version}.sqlite"
        evidence_map = build_local_evidence(str(index_path), detail_records, search_elements)
        close_agent = build_close_reader_agent()
        close_result = close_agent.invoke(
            {"messages": [{"role": "user", "content": build_close_reader_prompt(search_elements, detail_records, evidence_map)}]}
        )
        close_structured = extract_structured_response(close_result)
        selected_ids = [item for item in close_structured.get("selected") or []][:DEFAULT_SELECTED_LIMIT]
        rejected_detail_ids = [item for item in close_structured.get("rejected") or []]
        passages_by_doc: Dict[str, List[Dict[str, Any]]] = {}
        for item in close_structured.get("key_passages") or []:
            if not isinstance(item, dict):
                continue
            document_id = str(item.get("document_id") or "").strip()
            if not document_id:
                continue
            passages_by_doc.setdefault(document_id, []).append(
                {
                    "passage": str(item.get("passage") or "")[:DEFAULT_PASSAGE_PREVIEW_CHARS],
                    "reason": str(item.get("reason") or "").strip(),
                    "location": item.get("location"),
                }
            )
        for item in detail_records:
            document_id = item["document_id"]
            if document_id in selected_ids:
                storage.update_ai_search_document(
                    task_id,
                    int(plan_version),
                    document_id,
                    stage="selected",
                    key_passages_json=passages_by_doc.get(document_id) or evidence_map.get(item["pn"], [])[:DEFAULT_KEY_PASSAGES_LIMIT],
                    agent_reason="纳入对比文件",
                    close_read_status="selected",
                    close_read_reason="精读后纳入对比文件",
                    close_read_at=utc_now_z(),
                )
            elif document_id in rejected_detail_ids:
                storage.update_ai_search_document(
                    task_id,
                    int(plan_version),
                    document_id,
                    stage="rejected",
                    key_passages_json=passages_by_doc.get(document_id) or [],
                    agent_reason="精读后排除",
                    close_read_status="rejected",
                    close_read_reason="精读后排除",
                    close_read_at=utc_now_z(),
                )

    final_records = storage.list_ai_search_documents(task_id, int(plan_version))
    shortlist_records = [item for item in final_records if str(item.get("coarse_status") or "") == "kept"]
    selected_count = len([item for item in final_records if str(item.get("stage") or "") == "selected"])
    return {
        "candidate_records": final_records,
        "shortlist_records": shortlist_records,
        "selected_ids": selected_ids,
        "selected_count": selected_count,
    }
