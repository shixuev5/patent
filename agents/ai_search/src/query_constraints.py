"""
AI 检索查询约束与表达式构造。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence


def normalize_query_terms(values: Sequence[Any]) -> List[str]:
    outputs: List[str] = []
    for item in values or []:
        value = str(item or "").strip()
        if value and value not in outputs:
            outputs.append(value)
    return outputs


def _gap_seed_terms(batch: Dict[str, Any]) -> List[str]:
    outputs: List[str] = []
    for key in ("seed_terms", "targeted_terms"):
        outputs.extend(normalize_query_terms(batch.get(key) or []))
    return normalize_query_terms(outputs)


def _gap_pivot_terms(batch: Dict[str, Any]) -> List[str]:
    outputs: List[str] = []
    for key in ("pivot_terms", "replan_focus"):
        outputs.extend(normalize_query_terms(batch.get(key) or []))
    return normalize_query_terms(outputs)


def normalize_date_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return None


def compact_date_text(value: Any) -> Optional[str]:
    normalized = normalize_date_text(value)
    if not normalized:
        return None
    return normalized.replace("-", "")


def resolve_cutoff_date(search_elements: Dict[str, Any]) -> Optional[str]:
    if not isinstance(search_elements, dict):
        return None
    return normalize_date_text(search_elements.get("priority_date")) or normalize_date_text(search_elements.get("filing_date"))


def normalize_applicants(search_elements: Dict[str, Any]) -> List[str]:
    if not isinstance(search_elements, dict):
        return []
    values = search_elements.get("applicants") or []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    outputs: List[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in outputs:
            outputs.append(text)
    return outputs


def build_search_constraints(search_elements: Dict[str, Any]) -> Dict[str, Any]:
    cutoff_date = resolve_cutoff_date(search_elements)
    return {
        "applicant_terms": normalize_applicants(search_elements),
        "filing_date": normalize_date_text(search_elements.get("filing_date") if isinstance(search_elements, dict) else None),
        "priority_date": normalize_date_text(search_elements.get("priority_date") if isinstance(search_elements, dict) else None),
        "effective_cutoff_date": cutoff_date,
        "cutoff_date_yyyymmdd": compact_date_text(cutoff_date),
    }


def escape_query_term(value: str) -> str:
    return str(value or "").replace("\\", " ").replace('"', " ").strip()


def build_constraint_clauses(search_elements: Dict[str, Any]) -> List[str]:
    constraints = build_search_constraints(search_elements)
    clauses: List[str] = []
    cutoff = str(constraints.get("cutoff_date_yyyymmdd") or "").strip()
    if cutoff:
        clauses.append(f"PBD:[* TO {cutoff}]")
    applicants = constraints.get("applicant_terms") or []
    applicant_clauses = [
        f'AN:("{escape_query_term(applicant)}")'
        for applicant in applicants[:4]
        if escape_query_term(applicant)
    ]
    if applicant_clauses:
        clauses.append(applicant_clauses[0] if len(applicant_clauses) == 1 else "(" + " OR ".join(applicant_clauses) + ")")
    return clauses


def build_query_text(batch: Dict[str, Any], search_elements: Optional[Dict[str, Any]] = None) -> str:
    must_terms = (
        normalize_query_terms(batch.get("must_terms_zh") or [])
        + normalize_query_terms(batch.get("must_terms_en") or [])
        + _gap_seed_terms(batch)
    )
    should_terms = (
        normalize_query_terms(batch.get("should_terms_zh") or [])
        + normalize_query_terms(batch.get("should_terms_en") or [])
        + _gap_pivot_terms(batch)
    )
    negative_terms = normalize_query_terms(batch.get("negative_terms") or [])
    parts: List[str] = []
    if must_terms:
        parts.append(" AND ".join(f'"{item}"' for item in must_terms[:8]))
    if should_terms:
        parts.append("(" + " OR ".join(f'"{item}"' for item in should_terms[:8]) + ")")
    if negative_terms:
        parts.append(" ".join(f'NOT "{item}"' for item in negative_terms[:6]))
    query = " ".join(parts).strip() or str(batch.get("goal") or "").strip()
    constraint_clauses = build_constraint_clauses(search_elements or {})
    if query and constraint_clauses:
        return f"({query}) AND " + " AND ".join(constraint_clauses)
    if constraint_clauses:
        return " AND ".join(constraint_clauses)
    return query


def build_semantic_text(batch: Dict[str, Any], search_elements: Optional[Dict[str, Any]] = None) -> str:
    constraints = build_search_constraints(search_elements or {})
    semantic_parts = (
        normalize_query_terms(batch.get("must_terms_zh") or [])
        + normalize_query_terms(batch.get("should_terms_zh") or [])
        + normalize_query_terms(batch.get("must_terms_en") or [])
        + normalize_query_terms(batch.get("should_terms_en") or [])
        + _gap_seed_terms(batch)
        + _gap_pivot_terms(batch)
        + [str(batch.get("goal") or "").strip()]
    )
    gap_type = str(batch.get("gap_type") or "").strip()
    limitation_id = str(batch.get("limitation_id") or "").strip()
    claim_id = str(batch.get("claim_id") or "").strip()
    if gap_type:
        semantic_parts.append(f"gap类型：{gap_type}")
    if claim_id or limitation_id:
        semantic_parts.append(f"目标限制：{claim_id} {limitation_id}".strip())
    applicant_terms = constraints.get("applicant_terms") or []
    if applicant_terms:
        semantic_parts.append("相关申请人：" + "、".join(applicant_terms))
    if constraints.get("effective_cutoff_date"):
        semantic_parts.append(f"检索截止日：{constraints['effective_cutoff_date']}")
    return " ".join([part for part in semantic_parts if str(part or "").strip()]).strip()
