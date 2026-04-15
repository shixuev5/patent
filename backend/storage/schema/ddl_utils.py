"""Helpers for adapting schema DDL to online column migration statements."""

from __future__ import annotations

import re


def relax_column_ddl_for_add_column(ddl: str) -> str:
    """Convert CREATE TABLE column DDL into a safer ALTER TABLE ADD COLUMN form."""
    normalized = " ".join(str(ddl or "").split()).strip()
    if not normalized:
        return normalized

    relaxed = re.sub(r"\bPRIMARY\s+KEY\b", "", normalized, flags=re.IGNORECASE)
    relaxed = re.sub(r"\bUNIQUE\b", "", relaxed, flags=re.IGNORECASE)

    has_not_null = re.search(r"\bNOT\s+NULL\b", relaxed, flags=re.IGNORECASE)
    has_default = re.search(r"\bDEFAULT\b", relaxed, flags=re.IGNORECASE)
    if has_not_null and not has_default:
        relaxed = re.sub(r"\bNOT\s+NULL\b", "", relaxed, flags=re.IGNORECASE)

    return re.sub(r"\s+", " ", relaxed).strip()
