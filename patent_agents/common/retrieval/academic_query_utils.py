"""Common academic query derivation helpers."""

from __future__ import annotations

import re
from typing import Any, List


def normalize_academic_query(query: Any) -> str:
    text = " ".join(str(query or "").split())
    text = text.replace(",", " ").replace("，", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_academic_dispatch_query(query: Any) -> str:
    text = normalize_academic_query(query)
    if not text:
        return ""
    text = re.sub(r"[;；:：()\[\]{}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("\"'`“”")
    text = re.sub(
        r"\b(review|tutorial|survey|background|fundamentals)\b$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    return re.sub(r"\s+", " ", text).strip().lower()


def extract_academic_query_terms(text: Any) -> List[str]:
    query = normalize_academic_query(text)
    if not query:
        return []

    phrases: List[str] = []
    seen = set()

    def _add(value: Any) -> None:
        token = normalize_academic_query(value).strip("\"'`")
        if not token:
            return
        key = token.lower()
        if key in seen:
            return
        seen.add(key)
        phrases.append(token)

    for match in re.findall(r'"([^"]+)"|“([^”]+)”', query):
        _add(match[0] or match[1])

    cleaned = re.sub(r"[()]", " ", query)
    parts = re.split(r"\bAND\b|\bOR\b|\bNOT\b|[,;/]+", cleaned, flags=re.IGNORECASE)
    for part in parts:
        token = normalize_academic_query(part)
        if token:
            _add(token)

    return phrases


def strip_trailing_academic_modifiers(text: Any, keep_terminal_modifier: bool = False) -> str:
    token = normalize_academic_query(text)
    if not token:
        return ""
    modifier_pattern = r"\b(review|tutorial|survey|background|standard|fundamentals)\b$"
    if keep_terminal_modifier:
        return token
    return re.sub(modifier_pattern, "", token, flags=re.IGNORECASE).strip()


def to_semantic_academic_query(text: Any) -> str:
    tokens = extract_academic_query_terms(text)
    expanded: List[str] = []
    for token in tokens:
        token = strip_trailing_academic_modifiers(token)
        lowered = token.lower()
        if lowered in {"review", "tutorial", "survey", "background", "standard"}:
            continue
        expanded.append(token)
    if not expanded:
        expanded = tokens
    return " ".join(expanded[:8]).strip()


def to_crossref_bibliographic_query(text: Any) -> str:
    tokens = extract_academic_query_terms(text)
    primary: List[str] = []
    modifiers: List[str] = []
    for token in tokens:
        token = strip_trailing_academic_modifiers(token, keep_terminal_modifier=True)
        lowered = token.lower()
        if lowered in {"review", "tutorial", "survey"}:
            modifiers.append(token)
            continue
        if lowered in {"background", "standard"}:
            continue
        primary.append(token)
    selected = primary[:5]
    if modifiers and len(selected) < 6:
        selected.append(modifiers[0])
    return " ".join(selected).strip()
