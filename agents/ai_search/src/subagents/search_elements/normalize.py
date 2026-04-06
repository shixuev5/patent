"""Normalization helpers for structured search elements."""

from __future__ import annotations

import re
from typing import Any, Dict, List


DATE_TEXT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COMPACT_DATE_TEXT_RE = re.compile(r"^\d{8}$")


def _normalize_string_list(values: Any) -> List[str]:
    outputs: List[str] = []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return outputs
    for item in values:
        text = str(item or "").strip()
        if text and text not in outputs:
            outputs.append(text)
    return outputs


def normalize_date_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if DATE_TEXT_RE.fullmatch(text):
        return text
    if COMPACT_DATE_TEXT_RE.fullmatch(text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return None


def normalize_search_elements_payload(payload: Any) -> Dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    objective = str(source.get("objective") or "").strip()
    applicants = _normalize_string_list(source.get("applicants") or [])
    filing_date = normalize_date_text(source.get("filing_date"))
    priority_date = normalize_date_text(source.get("priority_date"))
    missing_items = _normalize_string_list(source.get("missing_items") or [])

    raw_elements = source.get("search_elements")
    search_elements = raw_elements if isinstance(raw_elements, list) else []
    normalized_elements: List[Dict[str, Any]] = []
    for item in search_elements:
        if not isinstance(item, dict):
            continue
        element_name = str(
            item.get("element_name")
            or item.get("feature")
            or item.get("name")
            or ""
        ).strip()
        base_item = {
            key: value
            for key, value in item.items()
            if key != "synonyms"
        }
        normalized_elements.append(
            {
                **base_item,
                "element_name": element_name,
                "keywords_zh": _normalize_string_list(item.get("keywords_zh") or []),
                "keywords_en": _normalize_string_list(item.get("keywords_en") or []),
                "notes": str(item.get("notes") or "").strip(),
            }
        )

    if not filing_date and not priority_date and "申请日或优先权日" not in missing_items:
        missing_items.append("申请日或优先权日")

    clarification_parts: List[str] = []
    raw_summary = str(source.get("clarification_summary") or "").strip()
    if raw_summary:
        clarification_parts.append(raw_summary)
    if not applicants:
        clarification_parts.append("未提供申请人，无法执行申请人追溯检索。")
    clarification_summary = " ".join(part for part in clarification_parts if part).strip()

    status = str(source.get("status") or "").strip().lower()
    if not objective or not any(str(item.get("element_name") or "").strip() for item in normalized_elements):
        status = "needs_answer"
    elif status not in {"complete", "needs_answer"}:
        status = "complete"

    return {
        "status": status,
        "objective": objective,
        "applicants": applicants,
        "filing_date": filing_date,
        "priority_date": priority_date,
        "search_elements": normalized_elements,
        "missing_items": missing_items,
        "clarification_summary": clarification_summary,
    }
