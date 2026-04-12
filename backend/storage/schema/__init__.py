"""Shared storage schema definitions."""

from .ai_search_sql import AI_SEARCH_STORAGE_SQL
from .d1_schema import (
    D1_CREATE_TABLES_SQL,
    D1_EXTRA_INDEX_SQL,
    D1_SCHEMA_META_KEY,
    D1_SCHEMA_META_TABLE,
    D1_SCHEMA_META_TABLE_SQL,
)
from .shared_schema import REQUIRED_COLUMNS, SQLITE_CREATE_TABLES_SQL

__all__ = [
    "AI_SEARCH_STORAGE_SQL",
    "D1_CREATE_TABLES_SQL",
    "D1_EXTRA_INDEX_SQL",
    "D1_SCHEMA_META_KEY",
    "D1_SCHEMA_META_TABLE",
    "D1_SCHEMA_META_TABLE_SQL",
    "REQUIRED_COLUMNS",
    "SQLITE_CREATE_TABLES_SQL",
]
