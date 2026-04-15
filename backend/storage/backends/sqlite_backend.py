"""SQLite storage executor and schema bootstrap."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from loguru import logger

from config import settings
from ..schema.ddl_utils import relax_column_ddl_for_add_column
from ..models import TaskType
from ..schema.shared_schema import REQUIRED_COLUMNS, SQLITE_CREATE_TABLES_SQL


class SQLiteBackend:
    REQUIRED_COLUMNS = REQUIRED_COLUMNS
    CREATE_TABLES_SQL = SQLITE_CREATE_TABLES_SQL

    def __init__(self, db_path: Union[str, Path, None] = None):
        if db_path is None:
            db_path = settings.DATA_DIR / "tasks.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_database()
        logger.info(f"SQLite 任务存储初始化完成：{self.db_path}")

    def _init_database(self):
        conn = self._get_connection()
        deferred_statements: List[str] = []
        for statement in self.CREATE_TABLES_SQL.split(";"):
            sql = statement.strip()
            if not sql:
                continue
            try:
                conn.execute(sql)
            except sqlite3.OperationalError as exc:
                if sql.upper().startswith("CREATE INDEX") and "no such column" in str(exc).lower():
                    deferred_statements.append(sql)
                    continue
                raise
        for table_name, required_columns in self.REQUIRED_COLUMNS.items():
            existing_columns = self._get_existing_columns(table_name)
            for column_name, ddl in required_columns:
                if column_name not in existing_columns:
                    add_column_ddl = relax_column_ddl_for_add_column(ddl)
                    if add_column_ddl != " ".join(str(ddl).split()).strip():
                        logger.warning(
                            "Relaxing SQLite ADD COLUMN DDL for {}.{}: {} -> {}",
                            table_name,
                            column_name,
                            ddl,
                            add_column_ddl,
                        )
                    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {add_column_ddl}")
        for sql in deferred_statements:
            conn.execute(sql)
        patent_analysis_columns = self._get_existing_columns("patent_analyses")
        if "sha256" in patent_analysis_columns:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_patent_analyses_sha256 ON patent_analyses(sha256)")
        user_columns = self._get_existing_columns("users")
        if "role" in user_columns:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
        conn.execute(
            "UPDATE tasks SET task_type = ? WHERE task_type IS NULL OR task_type = ''",
            (TaskType.PATENT_ANALYSIS.value,),
        )
        conn.commit()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "connection") or self._local.connection is None:
            conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                isolation_level=None,
            )
            conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = sqlite3.Row
            self._local.connection = conn
        return self._local.connection

    def _get_existing_columns(self, table_name: str) -> set[str]:
        rows = self._get_connection().execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    def _request(self, sql: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
        conn = self._get_connection()
        normalized_params = [
            self._normalize_update_value(self._encode_metadata(value))
            for value in (params or [])
        ]
        cursor = conn.execute(sql, tuple(normalized_params))
        result: Dict[str, Any] = {"meta": {"changes": max(cursor.rowcount, 0)}}
        if cursor.description:
            rows = cursor.fetchall()
            result["results"] = [dict(row) for row in rows]
        conn.commit()
        return result

    def _fetchall(self, sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        result = self._request(sql, params)
        rows = result.get("results") or []
        return rows if isinstance(rows, list) else []

    def _fetchone(self, sql: str, params: Optional[List[Any]] = None) -> Optional[Dict[str, Any]]:
        rows = self._fetchall(sql, params)
        return rows[0] if rows else None

    @staticmethod
    def _changed_rows(result: Dict[str, Any]) -> int:
        meta = result.get("meta") or {}
        return int(meta.get("changes") or 0)
