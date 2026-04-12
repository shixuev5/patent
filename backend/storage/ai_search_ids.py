"""AI search document identity helpers."""

from __future__ import annotations

import hashlib
from typing import Optional


def build_ai_search_canonical_id(
    *,
    source_type: Optional[str],
    external_id: Optional[str],
    doi: Optional[str] = None,
    pn: Optional[str] = None,
) -> str:
    normalized_doi = str(doi or "").strip().lower()
    if normalized_doi:
        return f"doi:{normalized_doi}"
    normalized_pn = str(pn or "").strip().upper()
    if normalized_pn:
        return f"patent:{normalized_pn}"
    normalized_source = str(source_type or "").strip().lower() or "document"
    normalized_external_id = str(external_id or "").strip()
    if normalized_external_id:
        return f"{normalized_source}:{normalized_external_id}"
    return normalized_source


def stable_ai_search_document_id(
    task_id: str,
    plan_version: int,
    canonical_id: Optional[str],
    *,
    fallback_seed: Optional[str] = None,
) -> str:
    seed = f"{task_id}:{plan_version}:{(canonical_id or fallback_seed or '').strip()}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()
