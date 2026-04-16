"""Cloudflare D1 storage executor and schema bootstrap."""

from __future__ import annotations

import hashlib
import json
from time import perf_counter
from typing import Any, Dict, List, Optional

import requests
from loguru import logger

from backend.time_utils import utc_now_z
from backend.storage.errors import StorageError, StorageRateLimitedError, StorageUnavailableError
from ..models import TaskType
from ..schema.d1_schema import (
    D1_CREATE_TABLES_SQL,
    D1_EXTRA_INDEX_SQL,
    D1_SCHEMA_META_KEY,
    D1_SCHEMA_META_TABLE,
    D1_SCHEMA_META_TABLE_SQL,
)
from ..schema.ddl_utils import relax_column_ddl_for_add_column
from ..schema.shared_schema import (
    REQUIRED_COLUMNS,
)


class D1Backend:
    SCHEMA_META_TABLE = D1_SCHEMA_META_TABLE
    SCHEMA_META_KEY = D1_SCHEMA_META_KEY
    SCHEMA_META_TABLE_SQL = D1_SCHEMA_META_TABLE_SQL
    REQUIRED_COLUMNS = REQUIRED_COLUMNS
    EXTRA_INDEX_SQL = D1_EXTRA_INDEX_SQL
    CREATE_TABLES_SQL = D1_CREATE_TABLES_SQL

    def __init__(
        self,
        account_id: str,
        database_id: str,
        api_token: str,
        api_base_url: str = "https://api.cloudflare.com/client/v4",
        timeout_seconds: int = 20,
    ):
        if not account_id:
            raise ValueError("D1_ACCOUNT_ID is required when TASK_STORAGE_BACKEND=d1")
        if not database_id:
            raise ValueError("D1_DATABASE_ID is required when TASK_STORAGE_BACKEND=d1")
        if not api_token:
            raise ValueError("D1_API_TOKEN is required when TASK_STORAGE_BACKEND=d1")

        self.endpoint = f"{api_base_url.rstrip('/')}/accounts/{account_id}/d1/database/{database_id}/query"
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        self.timeout_seconds = timeout_seconds

        self._init_database()
        logger.info("D1 任务存储初始化完成")

    @staticmethod
    def _parse_retry_after_seconds(response: Optional[requests.Response]) -> Optional[int]:
        if response is None:
            return None
        raw_value = str(response.headers.get("Retry-After") or "").strip()
        if not raw_value:
            return None
        try:
            return max(1, int(raw_value))
        except ValueError:
            return None

    def _request(self, sql: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"sql": sql}
        if params:
            payload["params"] = [self._normalize_update_value(self._encode_metadata(v)) for v in params]

        try:
            response = requests.post(
                self.endpoint,
                headers=self.headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            raise StorageUnavailableError("D1 request timed out") from exc
        except requests.exceptions.HTTPError as exc:
            response = exc.response
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code == 429:
                raise StorageRateLimitedError(
                    "D1 request rate limited",
                    retry_after_seconds=self._parse_retry_after_seconds(response),
                ) from exc
            if status_code in {408, 425} or status_code >= 500:
                raise StorageUnavailableError(
                    f"D1 request failed with status {status_code or 'unknown'}",
                    retry_after_seconds=self._parse_retry_after_seconds(response),
                ) from exc
            raise StorageError(f"D1 request failed with status {status_code or 'unknown'}") from exc
        except requests.exceptions.RequestException as exc:
            raise StorageUnavailableError("D1 request failed") from exc

        data = response.json()

        if not data.get("success", False):
            raise StorageError(f"D1 API error: {data.get('errors') or data}")

        result = data.get("result") or []
        if not result:
            return {}

        statement_result = result[0]
        if statement_result.get("success") is False:
            raise StorageError(f"D1 SQL execution failed: {statement_result}")
        return statement_result

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

    @classmethod
    def _split_statements(cls, sql_blob: str) -> List[str]:
        return [statement.strip() for statement in sql_blob.split(";") if statement.strip()]

    @classmethod
    def _schema_bootstrap_version(cls) -> str:
        payload = {
            "required_columns": cls.REQUIRED_COLUMNS,
            "create_statements": [" ".join(sql.split()) for sql in cls._split_statements(cls.CREATE_TABLES_SQL)],
            "extra_indexes": list(cls.EXTRA_INDEX_SQL),
            "backfill": "tasks.task_type.default.v1",
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_create_index_statement(sql: str) -> bool:
        normalized = str(sql or "").strip().upper()
        return normalized.startswith("CREATE INDEX") or normalized.startswith("CREATE UNIQUE INDEX")

    @staticmethod
    def _is_create_table_statement(sql: str) -> bool:
        return str(sql or "").strip().upper().startswith("CREATE TABLE")

    def _ensure_schema_meta_table(self) -> None:
        self._request(self.SCHEMA_META_TABLE_SQL)

    def _get_schema_meta_value(self, key: str) -> Optional[str]:
        row = self._fetchone(
            f"SELECT value FROM {self.SCHEMA_META_TABLE} WHERE key = ?",
            [str(key or "").strip()],
        )
        if not row:
            return None
        value = row.get("value")
        text = str(value).strip() if value is not None else ""
        return text or None

    def _set_schema_meta_value(self, key: str, value: str) -> None:
        now = utc_now_z()
        self._request(
            f"""
            INSERT INTO {self.SCHEMA_META_TABLE} (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            [str(key or "").strip(), str(value or "").strip(), now],
        )

    @staticmethod
    def _short_schema_version(value: Optional[str]) -> str:
        text = str(value or "").strip()
        if not text:
            return "-"
        if len(text) <= 12:
            return text
        return text[:12]

    def _init_database(self):
        started_at = perf_counter()
        self._ensure_schema_meta_table()
        target_version = self._schema_bootstrap_version()
        current_version = self._get_schema_meta_value(self.SCHEMA_META_KEY)
        if current_version == target_version:
            elapsed_ms = round((perf_counter() - started_at) * 1000, 1)
            logger.info(
                "D1 schema bootstrap fast-path: version={} duration_ms={}",
                self._short_schema_version(target_version),
                elapsed_ms,
            )
            return

        statements = self._split_statements(self.CREATE_TABLES_SQL)
        table_statements = [sql for sql in statements if self._is_create_table_statement(sql)]
        index_statements = [sql for sql in statements if self._is_create_index_statement(sql)]
        other_statements = [sql for sql in statements if sql not in table_statements and sql not in index_statements]
        added_columns = 0
        touched_tables: List[str] = []

        for sql in table_statements:
            self._request(sql)
        for sql in other_statements:
            self._request(sql)
        for table_name, required_columns in self.REQUIRED_COLUMNS.items():
            existing_columns = self._get_existing_columns(table_name)
            for column_name, ddl in required_columns:
                if column_name not in existing_columns:
                    add_column_ddl = relax_column_ddl_for_add_column(ddl)
                    if add_column_ddl != " ".join(str(ddl).split()).strip():
                        logger.warning(
                            "Relaxing D1 ADD COLUMN DDL for {}.{}: {} -> {}",
                            table_name,
                            column_name,
                            ddl,
                            add_column_ddl,
                        )
                    self._request(f"ALTER TABLE {table_name} ADD COLUMN {add_column_ddl}")
                    added_columns += 1
                    touched_tables.append(table_name)
        for sql in index_statements:
            self._request(sql)
        for sql in self.EXTRA_INDEX_SQL:
            self._request(sql)
        self._request(
            "UPDATE tasks SET task_type = ? WHERE task_type IS NULL OR task_type = ''",
            [TaskType.PATENT_ANALYSIS.value],
        )
        self._set_schema_meta_value(self.SCHEMA_META_KEY, target_version)
        elapsed_ms = round((perf_counter() - started_at) * 1000, 1)
        logger.info(
            "D1 schema bootstrap upgraded: from_version={} to_version={} tables={} columns_added={} create_tables={} create_indexes={} duration_ms={}",
            self._short_schema_version(current_version),
            self._short_schema_version(target_version),
            len(set(touched_tables)),
            added_columns,
            len(table_statements),
            len(index_statements) + len(self.EXTRA_INDEX_SQL),
            elapsed_ms,
        )

    def _get_existing_columns(self, table_name: str) -> set[str]:
        rows = self._fetchall(f"PRAGMA table_info({table_name})")
        columns = set()
        for row in rows:
            name = row.get("name")
            if name:
                columns.add(str(name))
        return columns
