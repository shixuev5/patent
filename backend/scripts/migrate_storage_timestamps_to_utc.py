"""
Normalize legacy storage timestamps to UTC Z strings.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional

from dotenv import load_dotenv

from backend.storage import D1TaskStorage
from backend.storage import SQLiteTaskStorage
from backend.time_utils import NaiveStrategy, to_utc_z


BackendType = Literal["d1", "sqlite"]


@dataclass(frozen=True)
class TableConfig:
    table: str
    primary_keys: tuple[str, ...]
    time_columns: tuple[str, ...]
    naive_strategy: NaiveStrategy


TABLES: tuple[TableConfig, ...] = (
    TableConfig("tasks", ("id",), ("created_at", "updated_at", "completed_at", "deleted_at"), "local"),
    TableConfig("users", ("owner_id",), ("created_at", "updated_at", "last_login_at"), "local"),
    TableConfig("system_logs", ("log_id",), ("timestamp", "created_at"), "local"),
    TableConfig("account_month_targets", ("owner_id", "year", "month"), ("created_at", "updated_at"), "local"),
    TableConfig("patent_analyses", ("pn",), ("first_completed_at",), "local"),
    TableConfig("task_llm_usage", ("task_id",), ("first_usage_at", "last_usage_at", "created_at", "updated_at"), "utc"),
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


def _fetch_count(storage: Any, backend: BackendType, table: str) -> int:
    sql = f"SELECT COUNT(*) AS c FROM {table}"
    if backend == "sqlite":
        with storage._get_connection() as conn:
            row = conn.execute(sql).fetchone()
            return int((row["c"] if row else 0) or 0)
    row = storage._fetchone(sql) or {}
    return int(row.get("c") or 0)


def _fetch_batch(storage: Any, backend: BackendType, config: TableConfig, limit: int, offset: int) -> List[Dict[str, Any]]:
    fields = ", ".join([*config.primary_keys, *config.time_columns])
    order_by = ", ".join(config.primary_keys)
    sql = f"SELECT {fields} FROM {config.table} ORDER BY {order_by} LIMIT ? OFFSET ?"
    if backend == "sqlite":
        with storage._get_connection() as conn:
            rows = conn.execute(sql, (limit, offset)).fetchall()
            return [dict(row) for row in rows]
    return list(storage._fetchall(sql, [limit, offset]) or [])


def _update_row(
    storage: Any,
    backend: BackendType,
    config: TableConfig,
    updated_columns: Dict[str, str],
    row: Dict[str, Any],
) -> None:
    set_clause = ", ".join([f"{column} = ?" for column in updated_columns.keys()])
    where_clause = " AND ".join([f"{column} = ?" for column in config.primary_keys])
    params = [*updated_columns.values(), *[row[column] for column in config.primary_keys]]
    sql = f"UPDATE {config.table} SET {set_clause} WHERE {where_clause}"
    if backend == "sqlite":
        with storage._get_connection() as conn:
            conn.execute(sql, params)
            conn.commit()
        return
    storage._request(sql, params)


def _checkpoint_path(raw: Optional[str]) -> Optional[Path]:
    text = str(raw or "").strip()
    return Path(text) if text else None


def _load_checkpoint(path: Optional[Path]) -> Dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_checkpoint(path: Optional[Path], checkpoint: Dict[str, Any]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")


def _row_identity(config: TableConfig, row: Dict[str, Any]) -> Dict[str, Any]:
    return {column: row.get(column) for column in config.primary_keys}


def _process_table(
    storage: Any,
    backend: BackendType,
    config: TableConfig,
    *,
    batch_size: int,
    apply: bool,
    checkpoint: Dict[str, Any],
    checkpoint_file: Optional[Path],
) -> Dict[str, Any]:
    table_checkpoint = checkpoint.setdefault(config.table, {})
    offset = int(table_checkpoint.get("offset") or 0)
    total_rows = _fetch_count(storage, backend, config.table)
    report = {
        "table": config.table,
        "totalRows": total_rows,
        "processedRows": 0,
        "updatedRows": 0,
        "changedValues": 0,
        "alreadyStandardized": 0,
        "invalidValues": 0,
        "samples": [],
    }

    while offset < total_rows:
        rows = _fetch_batch(storage, backend, config, batch_size, offset)
        if not rows:
            break
        for row in rows:
            report["processedRows"] += 1
            updated_columns: Dict[str, str] = {}
            for column in config.time_columns:
                raw_value = row.get(column)
                if raw_value in {None, ""}:
                    continue
                normalized = to_utc_z(raw_value, naive_strategy=config.naive_strategy)
                if normalized is None:
                    report["invalidValues"] += 1
                    continue
                if str(raw_value).strip() == normalized:
                    report["alreadyStandardized"] += 1
                    continue
                updated_columns[column] = normalized
                report["changedValues"] += 1
                if len(report["samples"]) < 5:
                    report["samples"].append(
                        {
                            "pk": _row_identity(config, row),
                            "column": column,
                            "before": raw_value,
                            "after": normalized,
                        }
                    )
            if updated_columns:
                report["updatedRows"] += 1
                if apply:
                    _update_row(storage, backend, config, updated_columns, row)
        offset += len(rows)
        table_checkpoint["offset"] = offset
        _save_checkpoint(checkpoint_file, checkpoint)
    return report


def _parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize storage timestamps to UTC Z strings.")
    parser.add_argument("--backend", choices=("d1", "sqlite"), required=True)
    parser.add_argument("--sqlite-path", default=None)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--checkpoint-file", default=None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Iterable[str]] = None) -> int:
    load_dotenv()
    args = _parse_args(argv)
    checkpoint_file = _checkpoint_path(args.checkpoint_file)
    checkpoint = _load_checkpoint(checkpoint_file)
    storage = _make_storage(args.backend, args.sqlite_path)

    reports = [
        _process_table(
            storage,
            args.backend,
            config,
            batch_size=max(1, int(args.batch_size or 200)),
            apply=bool(args.apply),
            checkpoint=checkpoint,
            checkpoint_file=checkpoint_file,
        )
        for config in TABLES
    ]
    summary = {
        "backend": args.backend,
        "mode": "apply" if args.apply else "dry-run",
        "tables": reports,
        "invalidValues": sum(int(item["invalidValues"]) for item in reports),
        "updatedRows": sum(int(item["updatedRows"]) for item in reports),
        "changedValues": sum(int(item["changedValues"]) for item in reports),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["invalidValues"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
