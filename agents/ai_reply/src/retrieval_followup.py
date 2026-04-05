"""
检索跟进与证据卡公共工具
复用查询去重、证据合并、证据卡压缩与 follow-up 触发逻辑。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

from agents.ai_reply.src.retrieval_utils import (
    QuerySpec,
    normalize_query_list,
)


def merge_evidence_items(
    primary_items: List[Dict[str, Any]],
    followup_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str]] = set()
    for item in (primary_items or []) + (followup_items or []):
        doc_id = str(item.get("doc_id", "")).strip()
        quote = str(item.get("quote", "")).strip()
        key = (doc_id, quote)
        if not doc_id or not quote or key in seen:
            continue
        seen.add(key)
        merged.append(dict(item))
    return merged


def build_evidence_cards_from_items(
    local_evidence: List[Dict[str, Any]],
    external_evidence: List[Dict[str, Any]],
    *,
    context_k: int,
    max_context_chars: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for index, item in enumerate(local_evidence or []):
        doc_id = str(item.get("doc_id", "")).strip()
        quote = re.sub(r"\s+", " ", str(item.get("quote", "")).strip())
        if not doc_id or not quote:
            continue
        candidates.append(
            {
                "candidate_id": f"local:{doc_id}:{index}",
                "doc_id": doc_id,
                "quote": quote,
                "location": str(item.get("location", "")).strip(),
                "analysis": str(item.get("analysis", "")).strip(),
                "source_url": str(item.get("source_url", "")).strip() or None,
                "source_title": str(item.get("source_title", "")).strip() or None,
                "source_type": str(item.get("source_type", "")).strip() or "comparison_document",
                "relevance_score": 0.9 - index * 0.01,
            }
        )
    for index, item in enumerate(external_evidence or []):
        doc_id = str(item.get("doc_id", "")).strip()
        quote = re.sub(r"\s+", " ", str(item.get("quote", "")).strip())
        if not doc_id or not quote:
            continue
        candidates.append(
            {
                "candidate_id": f"external:{doc_id}:{index}",
                "doc_id": doc_id,
                "quote": quote,
                "location": str(item.get("location", "")).strip(),
                "analysis": str(item.get("analysis", "")).strip(),
                "source_url": str(item.get("source_url", "")).strip() or None,
                "source_title": str(item.get("source_title", "")).strip() or None,
                "source_type": str(item.get("source_type", "")).strip() or "external_document",
                "relevance_score": 0.6 - index * 0.01,
            }
        )
    cards: List[Dict[str, Any]] = []
    selected: List[str] = []
    dropped: List[str] = []
    total_chars = 0
    seen_quotes: Set[Tuple[str, str]] = set()
    for item in sorted(candidates, key=lambda x: float(x.get("relevance_score", 0.0)), reverse=True):
        doc_id = str(item.get("doc_id", "")).strip()
        quote = str(item.get("quote", "")).strip()
        dedupe_key = (doc_id, quote)
        if dedupe_key in seen_quotes:
            continue
        if cards and total_chars + len(quote) > max_context_chars:
            dropped.append(str(item.get("candidate_id", "")).strip())
            continue
        seen_quotes.add(dedupe_key)
        cards.append(
            {
                "candidate_id": str(item.get("candidate_id", "")).strip(),
                "doc_id": doc_id,
                "quote": quote,
                "location": str(item.get("location", "")).strip(),
                "analysis": str(item.get("analysis", "")).strip(),
                "source_url": item.get("source_url"),
                "source_title": item.get("source_title"),
                "source_type": item.get("source_type"),
                "relevance_score": float(item.get("relevance_score", 0.0)),
            }
        )
        selected.append(str(item.get("candidate_id", "")).strip())
        total_chars += len(quote)
        if len(cards) >= context_k:
            break
    return cards, {
        "selected_cards": selected,
        "dropped_cards": dropped,
        "context_chars": total_chars,
        "context_k": context_k,
        "max_context_chars": max_context_chars,
    }


def build_compact_cards(
    candidates: List[Dict[str, Any]],
    local_retriever: Any,
    *,
    context_k: int,
    max_context_chars: int,
    max_quote_chars: int,
    read_window: int = 1,
) -> Dict[str, Any]:
    if local_retriever:
        return local_retriever.build_evidence_cards(
            candidates=candidates,
            context_k=context_k,
            max_context_chars=max_context_chars,
            max_quote_chars=max_quote_chars,
            read_window=read_window,
        )

    cards: List[Dict[str, Any]] = []
    selected: List[str] = []
    dropped: List[str] = []
    total_chars = 0
    for item in sorted(candidates or [], key=lambda x: float(x.get("relevance_score", 0.0)), reverse=True):
        candidate_id = str(item.get("candidate_id", "")).strip() or str(item.get("doc_id", "")).strip()
        quote = re.sub(r"\s+", " ", str(item.get("text", "")).strip())
        if len(quote) > max_quote_chars:
            quote = quote[: max_quote_chars - 3].rstrip() + "..."
        if not quote:
            continue
        if cards and total_chars + len(quote) > max_context_chars:
            dropped.append(candidate_id)
            continue
        cards.append(
            {
                "candidate_id": candidate_id,
                "chunk_id": None,
                "doc_id": str(item.get("doc_id", "")).strip(),
                "quote": quote,
                "location": str(item.get("location", "")).strip(),
                "analysis": f"命中关键词：{', '.join(item.get('match_terms', [])[:3])}" if item.get("match_terms") else "",
                "source_url": str(item.get("source_url", "")).strip() or None,
                "source_title": str(item.get("source_title", "")).strip() or None,
                "source_type": str(item.get("source_type", "")).strip() or None,
                "relevance_score": float(item.get("relevance_score", 0.0)),
            }
        )
        selected.append(candidate_id)
        total_chars += len(quote)
        if len(cards) >= context_k:
            break
    return {
        "cards": cards,
        "trace": {
            "selected_candidates": selected,
            "dropped_candidates": dropped,
            "context_chars": total_chars,
            "context_k": context_k,
            "max_context_chars": max_context_chars,
            "max_quote_chars": max_quote_chars,
        },
    }


def has_new_evidence_cards(
    primary_cards: List[Dict[str, Any]],
    expanded_cards: List[Dict[str, Any]],
) -> bool:
    primary_keys = {
        (str(item.get("doc_id", "")).strip(), str(item.get("quote", "")).strip())
        for item in primary_cards
    }
    expanded_keys = {
        (str(item.get("doc_id", "")).strip(), str(item.get("quote", "")).strip())
        for item in expanded_cards
    }
    return len(expanded_keys - primary_keys) > 0


def should_run_followup_by_assessment(
    assessment: Dict[str, Any],
    *,
    used_doc_ids: List[str] | None = None,
    evidence_cards: List[Dict[str, Any]] | None = None,
    confidence_threshold: float = 0.6,
) -> bool:
    verdict = str(assessment.get("verdict", "")).strip()
    try:
        confidence = float(assessment.get("confidence", 0.0) or 0.0)
    except Exception:
        confidence = 0.0
    if verdict == "INCONCLUSIVE" or confidence < confidence_threshold:
        return True
    return not (used_doc_ids or []) and len(evidence_cards or []) <= 2


def merge_local_retrieval_trace(
    *,
    local_trace: Dict[str, Any],
    card_trace: Dict[str, Any],
    queries: List[str],
    doc_filters: List[str],
) -> Dict[str, Any]:
    return {
        "enabled": bool(local_trace.get("enabled", False)),
        "fallback": str(local_trace.get("fallback", "")).strip(),
        "queries": queries,
        "queries_by_language": local_trace.get("queries_by_language", {}),
        "doc_filters": doc_filters,
        "hit_chunks": local_trace.get("hit_chunks", []),
        "lexical_hits": local_trace.get("lexical_hits", []),
        "dense_hits": local_trace.get("dense_hits", []),
        "fusion_hits": local_trace.get("fusion_hits", []),
        "selected_cards": card_trace.get("selected_candidates", card_trace.get("selected_cards", [])),
        "dropped_cards": card_trace.get("dropped_candidates", card_trace.get("dropped_cards", [])),
        "context_chars": int(card_trace.get("context_chars", 0) or 0),
    }


def merge_local_retrieval_traces(
    primary_trace: Dict[str, Any],
    followup_trace: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(primary_trace or {})
    if not followup_trace:
        return merged
    merged["queries"] = normalize_query_list(
        list(primary_trace.get("queries", [])) + list(followup_trace.get("queries", [])),
        limit=6,
    )
    for key in ["hit_chunks", "lexical_hits", "dense_hits", "fusion_hits", "selected_cards", "dropped_cards"]:
        merged[key] = list(dict.fromkeys(list(primary_trace.get(key, [])) + list(followup_trace.get(key, []))))
    merged["context_chars"] = max(
        int(primary_trace.get("context_chars", 0) or 0),
        int(followup_trace.get("context_chars", 0) or 0),
    )
    return merged
