"""
Destructively reset AI search storage for the V4 schema.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Iterable, Literal, Optional

from dotenv import load_dotenv

from backend.storage import TaskType
from backend.storage.ai_search_support import AI_SEARCH_STORAGE_SQL
from backend.storage.d1_storage import D1TaskStorage
from backend.storage.sqlite_storage import SQLiteTaskStorage


BackendType = Literal["sqlite", "d1"]

AI_SEARCH_TABLES = (
    "ai_search_messages",
    "ai_search_plans",
    "ai_search_documents",
    "ai_search_feature_tables",
    "ai_search_checkpoints",
    "ai_search_checkpoint_writes",
    "ai_search_checkpoint_blobs",
)


def _make_storage(backend: BackendType, sqlite_path: Optional[str]):
    if backend == "sqlite":
        return SQLiteTaskStorage(sqlite_path)
    return D1TaskStorage(
        account_id=os.getenv("D1_ACCOUNT_ID", "").strip(),
        database_id=os.getenv("D1_DATABASE_ID", "").strip(),
        api_token=os.getenv("D1_API_TOKEN", "").strip(),
        api_base_url=os.getenv("D1_API_BASE_URL", "https://api.cloudflare.com/client/v4").strip(),
    )


def _fetch_task_ids(storage: Any, backend: BackendType) -> list[str]:
    sql = "SELECT id FROM tasks WHERE task_type = ?"
    if backend == "sqlite":
        with storage._get_connection() as conn:
            rows = conn.execute(sql, (TaskType.AI_SEARCH.value,)).fetchall()
            return [str(row["id"] or "").strip() for row in rows if str(row["id"] or "").strip()]
    rows = storage._fetchall(sql, [TaskType.AI_SEARCH.value]) or []
    return [str(row.get("id") or "").strip() for row in rows if str(row.get("id") or "").strip()]


def _execute(storage: Any, backend: BackendType, sql: str, params: Optional[list[Any]] = None) -> None:
    if backend == "sqlite":
        with storage._get_connection() as conn:
            conn.execute(sql, params or [])
            conn.commit()
        return
    storage._request(sql, params or [])


def _reset(storage: Any, backend: BackendType) -> dict[str, Any]:
    task_ids = _fetch_task_ids(storage, backend)
    if task_ids:
        placeholders = ", ".join("?" for _ in task_ids)
        _execute(storage, backend, f"DELETE FROM task_llm_usage WHERE task_id IN ({placeholders})", task_ids)
        _execute(storage, backend, f"DELETE FROM tasks WHERE id IN ({placeholders})", task_ids)
    for table in AI_SEARCH_TABLES:
        _execute(storage, backend, f"DROP TABLE IF EXISTS {table}")
    if backend == "sqlite":
        with storage._get_connection() as conn:
            conn.executescript(AI_SEARCH_STORAGE_SQL)
            conn.commit()
    else:
        for statement in [chunk.strip() for chunk in AI_SEARCH_STORAGE_SQL.split(";") if chunk.strip()]:
            storage._request(statement)
    return {
        "backend": backend,
        "deletedTaskCount": len(task_ids),
        "resetTables": list(AI_SEARCH_TABLES),
    }


def _parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset AI search tables and sessions for V4 schema.")
    parser.add_argument("--backend", choices=("sqlite", "d1"), required=True)
    parser.add_argument("--sqlite-path", default=None)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Iterable[str]] = None) -> int:
    load_dotenv()
    args = _parse_args(argv)
    storage = _make_storage(args.backend, args.sqlite_path)
    report = _reset(storage, args.backend)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
