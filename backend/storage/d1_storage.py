"""
Cloudflare D1 task storage layer.
"""

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

import requests
from loguru import logger

from backend.time_utils import (
    local_day_start_end_to_utc,
    local_recent_day_window_to_utc,
    parse_storage_ts,
    to_utc_z,
    utc_now_z,
    utc_to_local_day,
)
from .ai_search_support import AI_SEARCH_STORAGE_SQL
from .models import AccountMonthTarget, RefreshSession, Task, TaskStatus, TaskType, User


class D1TaskStorage:
    REQUIRED_COLUMNS = {
        "tasks": [
            ("owner_id", "owner_id TEXT"),
            ("task_type", f"task_type TEXT NOT NULL DEFAULT '{TaskType.PATENT_ANALYSIS.value}'"),
            ("pn", "pn TEXT"),
            ("title", "title TEXT"),
            ("status", "status TEXT NOT NULL DEFAULT 'pending'"),
            ("progress", "progress INTEGER DEFAULT 0"),
            ("current_step", "current_step TEXT"),
            ("output_dir", "output_dir TEXT"),
            ("error_message", "error_message TEXT"),
            ("created_at", "created_at TEXT NOT NULL"),
            ("updated_at", "updated_at TEXT NOT NULL"),
            ("completed_at", "completed_at TEXT"),
            ("deleted_at", "deleted_at TEXT"),
            ("metadata", "metadata TEXT"),
        ],
        "patent_analyses": [
            ("first_completed_at", "first_completed_at TEXT NOT NULL"),
            ("sha256", "sha256 TEXT"),
        ],
        "users": [
            ("owner_id", "owner_id TEXT PRIMARY KEY"),
            ("authing_sub", "authing_sub TEXT NOT NULL UNIQUE"),
            ("role", "role TEXT"),
            ("name", "name TEXT"),
            ("nickname", "nickname TEXT"),
            ("email", "email TEXT"),
            ("phone", "phone TEXT"),
            ("picture", "picture TEXT"),
            ("raw_profile", "raw_profile TEXT"),
            ("created_at", "created_at TEXT NOT NULL"),
            ("updated_at", "updated_at TEXT NOT NULL"),
            ("last_login_at", "last_login_at TEXT NOT NULL"),
        ],
        "account_month_targets": [
            ("owner_id", "owner_id TEXT NOT NULL"),
            ("year", "year INTEGER NOT NULL"),
            ("month", "month INTEGER NOT NULL"),
            ("target_count", "target_count INTEGER NOT NULL"),
            ("created_at", "created_at TEXT NOT NULL"),
            ("updated_at", "updated_at TEXT NOT NULL"),
        ],
        "task_llm_usage": [
            ("task_id", "task_id TEXT PRIMARY KEY"),
            ("owner_id", "owner_id TEXT NOT NULL"),
            ("task_type", "task_type TEXT NOT NULL"),
            ("task_status", "task_status TEXT"),
            ("prompt_tokens", "prompt_tokens INTEGER NOT NULL DEFAULT 0"),
            ("completion_tokens", "completion_tokens INTEGER NOT NULL DEFAULT 0"),
            ("total_tokens", "total_tokens INTEGER NOT NULL DEFAULT 0"),
            ("reasoning_tokens", "reasoning_tokens INTEGER NOT NULL DEFAULT 0"),
            ("llm_call_count", "llm_call_count INTEGER NOT NULL DEFAULT 0"),
            ("estimated_cost_cny", "estimated_cost_cny REAL NOT NULL DEFAULT 0"),
            ("price_missing", "price_missing INTEGER NOT NULL DEFAULT 0"),
            ("model_breakdown_json", "model_breakdown_json TEXT"),
            ("first_usage_at", "first_usage_at TEXT"),
            ("last_usage_at", "last_usage_at TEXT"),
            ("currency", "currency TEXT NOT NULL DEFAULT 'CNY'"),
            ("created_at", "created_at TEXT NOT NULL"),
            ("updated_at", "updated_at TEXT NOT NULL"),
        ],
        "system_logs": [
            ("log_id", "log_id TEXT PRIMARY KEY"),
            ("timestamp", "timestamp TEXT NOT NULL"),
            ("category", "category TEXT NOT NULL"),
            ("event_name", "event_name TEXT NOT NULL"),
            ("level", "level TEXT NOT NULL"),
            ("owner_id", "owner_id TEXT"),
            ("task_id", "task_id TEXT"),
            ("task_type", "task_type TEXT"),
            ("request_id", "request_id TEXT"),
            ("trace_id", "trace_id TEXT"),
            ("method", "method TEXT"),
            ("path", "path TEXT"),
            ("status_code", "status_code INTEGER"),
            ("duration_ms", "duration_ms INTEGER"),
            ("provider", "provider TEXT"),
            ("target_host", "target_host TEXT"),
            ("success", "success INTEGER NOT NULL DEFAULT 0"),
            ("message", "message TEXT"),
            ("payload_inline_json", "payload_inline_json TEXT"),
            ("payload_file_path", "payload_file_path TEXT"),
            ("payload_bytes", "payload_bytes INTEGER NOT NULL DEFAULT 0"),
            ("payload_overflow", "payload_overflow INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "created_at TEXT NOT NULL"),
        ],
        "refresh_sessions": [
            ("token_hash", "token_hash TEXT PRIMARY KEY"),
            ("owner_id", "owner_id TEXT NOT NULL"),
            ("expires_at", "expires_at TEXT NOT NULL"),
            ("created_at", "created_at TEXT NOT NULL"),
            ("updated_at", "updated_at TEXT NOT NULL"),
            ("revoked_at", "revoked_at TEXT"),
            ("replaced_by_token_hash", "replaced_by_token_hash TEXT"),
        ],
        "ai_search_documents": [
            ("run_id", "run_id TEXT"),
            ("publication_date", "publication_date TEXT"),
            ("application_date", "application_date TEXT"),
            ("primary_ipc", "primary_ipc TEXT"),
            ("document_type", "document_type TEXT"),
            ("claim_ids_json", "claim_ids_json TEXT"),
            ("evidence_locations_json", "evidence_locations_json TEXT"),
            ("evidence_summary", "evidence_summary TEXT"),
            ("report_row_order", "report_row_order INTEGER"),
            ("source_lanes_json", "source_lanes_json TEXT"),
            ("source_sub_plans_json", "source_sub_plans_json TEXT"),
            ("source_steps_json", "source_steps_json TEXT"),
            ("coarse_status", "coarse_status TEXT NOT NULL DEFAULT 'pending'"),
            ("coarse_reason", "coarse_reason TEXT"),
            ("coarse_screened_at", "coarse_screened_at TEXT"),
            ("close_read_status", "close_read_status TEXT NOT NULL DEFAULT 'pending'"),
            ("close_read_reason", "close_read_reason TEXT"),
            ("close_read_at", "close_read_at TEXT"),
            ("detail_fingerprint", "detail_fingerprint TEXT"),
        ],
    }

    CREATE_TABLES_SQL = """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        owner_id TEXT,
        task_type TEXT NOT NULL DEFAULT 'patent_analysis',
        pn TEXT,
        title TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        progress INTEGER DEFAULT 0,
        current_step TEXT,
        output_dir TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        deleted_at TEXT,
        metadata TEXT
    );

    CREATE TABLE IF NOT EXISTS patent_analyses (
        pn TEXT PRIMARY KEY,
        first_completed_at TEXT NOT NULL,
        sha256 TEXT
    );

    CREATE TABLE IF NOT EXISTS users (
        owner_id TEXT PRIMARY KEY,
        authing_sub TEXT NOT NULL UNIQUE,
        role TEXT,
        name TEXT,
        nickname TEXT,
        email TEXT,
        phone TEXT,
        picture TEXT,
        raw_profile TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_login_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS account_month_targets (
        owner_id TEXT NOT NULL,
        year INTEGER NOT NULL,
        month INTEGER NOT NULL,
        target_count INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (owner_id, year, month)
    );

    CREATE TABLE IF NOT EXISTS task_llm_usage (
        task_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        task_type TEXT NOT NULL,
        task_status TEXT,
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        llm_call_count INTEGER NOT NULL DEFAULT 0,
        estimated_cost_cny REAL NOT NULL DEFAULT 0,
        price_missing INTEGER NOT NULL DEFAULT 0,
        model_breakdown_json TEXT,
        first_usage_at TEXT,
        last_usage_at TEXT,
        currency TEXT NOT NULL DEFAULT 'CNY',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS system_logs (
        log_id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        category TEXT NOT NULL,
        event_name TEXT NOT NULL,
        level TEXT NOT NULL,
        owner_id TEXT,
        task_id TEXT,
        task_type TEXT,
        request_id TEXT,
        trace_id TEXT,
        method TEXT,
        path TEXT,
        status_code INTEGER,
        duration_ms INTEGER,
        provider TEXT,
        target_host TEXT,
        success INTEGER NOT NULL DEFAULT 0,
        message TEXT,
        payload_inline_json TEXT,
        payload_file_path TEXT,
        payload_bytes INTEGER NOT NULL DEFAULT 0,
        payload_overflow INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS refresh_sessions (
        token_hash TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        revoked_at TEXT,
        replaced_by_token_hash TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_owner_id ON tasks(owner_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_pn ON tasks(pn);
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
    CREATE INDEX IF NOT EXISTS idx_tasks_updated_at ON tasks(updated_at);
    CREATE INDEX IF NOT EXISTS idx_tasks_task_type ON tasks(task_type);
    CREATE INDEX IF NOT EXISTS idx_tasks_deleted_created_at ON tasks(deleted_at, created_at);
    CREATE INDEX IF NOT EXISTS idx_tasks_deleted_owner_id ON tasks(deleted_at, owner_id);
    CREATE INDEX IF NOT EXISTS idx_patent_analyses_completed_at ON patent_analyses(first_completed_at);
    CREATE INDEX IF NOT EXISTS idx_users_authing_sub ON users(authing_sub);
    CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
    CREATE INDEX IF NOT EXISTS idx_account_month_targets_owner_ym ON account_month_targets(owner_id, year, month);
    CREATE INDEX IF NOT EXISTS idx_task_llm_usage_owner_id ON task_llm_usage(owner_id);
    CREATE INDEX IF NOT EXISTS idx_task_llm_usage_last_usage_at ON task_llm_usage(last_usage_at);
    CREATE INDEX IF NOT EXISTS idx_task_llm_usage_task_type ON task_llm_usage(task_type);
    CREATE INDEX IF NOT EXISTS idx_task_llm_usage_task_status ON task_llm_usage(task_status);
    CREATE INDEX IF NOT EXISTS idx_system_logs_timestamp ON system_logs(timestamp);
    CREATE INDEX IF NOT EXISTS idx_system_logs_category ON system_logs(category);
    CREATE INDEX IF NOT EXISTS idx_system_logs_owner_id ON system_logs(owner_id);
    CREATE INDEX IF NOT EXISTS idx_system_logs_task_id ON system_logs(task_id);
    CREATE INDEX IF NOT EXISTS idx_system_logs_request_id ON system_logs(request_id);
    CREATE INDEX IF NOT EXISTS idx_system_logs_provider ON system_logs(provider);
    CREATE INDEX IF NOT EXISTS idx_system_logs_success ON system_logs(success);
    CREATE INDEX IF NOT EXISTS idx_refresh_sessions_owner_id ON refresh_sessions(owner_id);
    CREATE INDEX IF NOT EXISTS idx_refresh_sessions_expires_at ON refresh_sessions(expires_at);
    CREATE INDEX IF NOT EXISTS idx_refresh_sessions_revoked_at ON refresh_sessions(revoked_at);
    """ + AI_SEARCH_STORAGE_SQL

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

        self.endpoint = (
            f"{api_base_url.rstrip('/')}/accounts/{account_id}/d1/database/{database_id}/query"
        )
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        self.timeout_seconds = timeout_seconds

        self._init_database()
        logger.info("D1 任务存储初始化完成")

    def _request(self, sql: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"sql": sql}
        if params:
            payload["params"] = [self._normalize_update_value(self._encode_metadata(v)) for v in params]

        response = requests.post(
            self.endpoint,
            headers=self.headers,
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("success", False):
            raise RuntimeError(f"D1 API error: {data.get('errors') or data}")

        result = data.get("result") or []
        if not result:
            return {}

        statement_result = result[0]
        if statement_result.get("success") is False:
            raise RuntimeError(f"D1 SQL execution failed: {statement_result}")
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

    def _init_database(self):
        deferred_statements: List[str] = []
        for statement in self.CREATE_TABLES_SQL.split(";"):
            sql = statement.strip()
            if sql:
                try:
                    self._request(sql)
                except RuntimeError as exc:
                    if sql.upper().startswith("CREATE INDEX") and "no such column" in str(exc).lower():
                        deferred_statements.append(sql)
                        continue
                    raise
        for table_name, required_columns in self.REQUIRED_COLUMNS.items():
            existing_columns = self._get_existing_columns(table_name)
            for column_name, ddl in required_columns:
                if column_name not in existing_columns:
                    self._request(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")
        for sql in deferred_statements:
            self._request(sql)
        patent_analysis_columns = self._get_existing_columns("patent_analyses")
        if "sha256" in patent_analysis_columns:
            self._request("CREATE INDEX IF NOT EXISTS idx_patent_analyses_sha256 ON patent_analyses(sha256)")
        user_columns = self._get_existing_columns("users")
        if "role" in user_columns:
            self._request("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
        self._request(
            "UPDATE tasks SET task_type = ? WHERE task_type IS NULL OR task_type = ''",
            [TaskType.PATENT_ANALYSIS.value],
        )

    def _get_existing_columns(self, table_name: str) -> set[str]:
        rows = self._fetchall(f"PRAGMA table_info({table_name})")
        columns = set()
        for row in rows:
            name = row.get("name")
            if name:
                columns.add(str(name))
        return columns

    @staticmethod
    def _parse_metadata(raw: Any) -> Dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return {}

    @staticmethod
    def _encode_metadata(value: Any) -> Any:
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return value

    @staticmethod
    def _encode_json_value(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False)
        return value

    @staticmethod
    def _normalize_update_value(value: Any) -> Any:
        if isinstance(value, TaskStatus):
            return value.value
        if isinstance(value, datetime):
            return to_utc_z(value, naive_strategy="utc")
        return value

    @staticmethod
    def _normalize_usage_timestamp(value: Any, *, field: str) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, datetime) and str(value).strip() == "":
            return None
        normalized = to_utc_z(value, naive_strategy="utc")
        if normalized is None:
            raise ValueError(f"Invalid task_llm_usage timestamp for {field}: {value}")
        return normalized

    def _row_to_task(self, row: Dict[str, Any]) -> Task:
        return Task(
            id=row["id"],
            owner_id=row.get("owner_id"),
            task_type=row.get("task_type") or TaskType.PATENT_ANALYSIS.value,
            pn=row.get("pn"),
            title=row.get("title"),
            status=TaskStatus(row["status"]),
            progress=row.get("progress", 0),
            current_step=row.get("current_step"),
            output_dir=row.get("output_dir"),
            error_message=row.get("error_message"),
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
            completed_at=parse_storage_ts(row["completed_at"], naive_strategy="utc") if row.get("completed_at") else None,
            deleted_at=parse_storage_ts(row["deleted_at"], naive_strategy="utc") if row.get("deleted_at") else None,
            metadata=self._parse_metadata(row.get("metadata")),
        )

    def _row_to_user(self, row: Dict[str, Any]) -> User:
        return User(
            owner_id=row["owner_id"],
            authing_sub=row["authing_sub"],
            role=row.get("role"),
            name=row.get("name"),
            nickname=row.get("nickname"),
            email=row.get("email"),
            phone=row.get("phone"),
            picture=row.get("picture"),
            raw_profile=self._parse_metadata(row.get("raw_profile")),
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
            last_login_at=parse_storage_ts(row["last_login_at"], naive_strategy="utc"),
        )

    def _row_to_refresh_session(self, row: Dict[str, Any]) -> RefreshSession:
        return RefreshSession(
            token_hash=str(row["token_hash"]),
            owner_id=str(row["owner_id"]),
            expires_at=parse_storage_ts(row["expires_at"], naive_strategy="utc"),
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
            revoked_at=parse_storage_ts(row["revoked_at"], naive_strategy="utc") if row.get("revoked_at") else None,
            replaced_by_token_hash=str(row.get("replaced_by_token_hash") or "").strip() or None,
        )

    def _row_to_account_month_target(self, row: Dict[str, Any]) -> AccountMonthTarget:
        return AccountMonthTarget(
            owner_id=row["owner_id"],
            year=int(row["year"]),
            month=int(row["month"]),
            target_count=int(row["target_count"]),
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
        )

    def create_task(self, task: Task) -> Task:
        self._request(
            """
            INSERT INTO tasks (
                id, owner_id, task_type, pn, title, status, progress, current_step,
                output_dir, error_message, created_at, updated_at, completed_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                task.id,
                task.owner_id,
                task.task_type,
                task.pn,
                task.title,
                task.status.value,
                task.progress,
                task.current_step,
                task.output_dir,
                task.error_message,
                to_utc_z(task.created_at, naive_strategy="utc"),
                to_utc_z(task.updated_at, naive_strategy="utc"),
                to_utc_z(task.completed_at, naive_strategy="utc") if task.completed_at else None,
                json.dumps(task.metadata, ensure_ascii=False) if task.metadata else None,
            ],
        )
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        row = self._fetchone("SELECT * FROM tasks WHERE id = ?", [task_id])
        return self._row_to_task(row) if row else None

    def update_task(self, task_id: str, **kwargs) -> bool:
        allowed_fields = {
            "owner_id",
            "task_type",
            "pn",
            "title",
            "status",
            "progress",
            "current_step",
            "output_dir",
            "error_message",
            "completed_at",
            "deleted_at",
            "metadata",
            "updated_at",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return False

        updates["updated_at"] = utc_now_z()
        for key in list(updates.keys()):
            updates[key] = self._normalize_update_value(self._encode_metadata(updates[key]))

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [task_id]
        result = self._request(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        return self._changed_rows(result) > 0

    def delete_task(self, task_id: str) -> bool:
        result = self._request(
            "UPDATE tasks SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
            [utc_now_z(), utc_now_z(), task_id]
        )
        return self._changed_rows(result) > 0

    def _row_to_task_llm_usage(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_id": row.get("task_id"),
            "owner_id": row.get("owner_id"),
            "task_type": row.get("task_type"),
            "task_status": row.get("task_status") or "",
            "prompt_tokens": int(row.get("prompt_tokens") or 0),
            "completion_tokens": int(row.get("completion_tokens") or 0),
            "total_tokens": int(row.get("total_tokens") or 0),
            "reasoning_tokens": int(row.get("reasoning_tokens") or 0),
            "llm_call_count": int(row.get("llm_call_count") or 0),
            "estimated_cost_cny": float(row.get("estimated_cost_cny") or 0),
            "price_missing": bool(int(row.get("price_missing") or 0)),
            "model_breakdown_json": self._parse_metadata(row.get("model_breakdown_json")),
            "first_usage_at": row.get("first_usage_at"),
            "last_usage_at": row.get("last_usage_at"),
            "currency": row.get("currency") or "CNY",
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def upsert_task_llm_usage(self, usage: Dict[str, Any]) -> bool:
        payload = {
            "task_id": str(usage.get("task_id", "")).strip(),
            "owner_id": str(usage.get("owner_id", "")).strip(),
            "task_type": str(usage.get("task_type", "")).strip(),
            "task_status": str(usage.get("task_status", "")).strip(),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
            "llm_call_count": int(usage.get("llm_call_count") or 0),
            "estimated_cost_cny": float(usage.get("estimated_cost_cny") or 0),
            "price_missing": 1 if usage.get("price_missing") else 0,
            "model_breakdown_json": usage.get("model_breakdown_json") or {},
            "first_usage_at": self._normalize_usage_timestamp(usage.get("first_usage_at"), field="first_usage_at"),
            "last_usage_at": self._normalize_usage_timestamp(usage.get("last_usage_at"), field="last_usage_at"),
            "currency": str(usage.get("currency") or "CNY").strip() or "CNY",
            "created_at": self._normalize_usage_timestamp(usage.get("created_at") or utc_now_z(), field="created_at"),
            "updated_at": self._normalize_usage_timestamp(usage.get("updated_at") or utc_now_z(), field="updated_at"),
        }
        if not payload["task_id"] or not payload["owner_id"] or not payload["task_type"]:
            return False

        result = self._request(
            """
            INSERT INTO task_llm_usage (
                task_id, owner_id, task_type, task_status,
                prompt_tokens, completion_tokens, total_tokens, reasoning_tokens,
                llm_call_count, estimated_cost_cny, price_missing, model_breakdown_json,
                first_usage_at, last_usage_at, currency, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                owner_id = excluded.owner_id,
                task_type = excluded.task_type,
                task_status = excluded.task_status,
                prompt_tokens = excluded.prompt_tokens,
                completion_tokens = excluded.completion_tokens,
                total_tokens = excluded.total_tokens,
                reasoning_tokens = excluded.reasoning_tokens,
                llm_call_count = excluded.llm_call_count,
                estimated_cost_cny = excluded.estimated_cost_cny,
                price_missing = excluded.price_missing,
                model_breakdown_json = excluded.model_breakdown_json,
                first_usage_at = excluded.first_usage_at,
                last_usage_at = excluded.last_usage_at,
                currency = excluded.currency,
                created_at = COALESCE(task_llm_usage.created_at, excluded.created_at),
                updated_at = excluded.updated_at
            """,
            [
                payload["task_id"],
                payload["owner_id"],
                payload["task_type"],
                payload["task_status"],
                payload["prompt_tokens"],
                payload["completion_tokens"],
                payload["total_tokens"],
                payload["reasoning_tokens"],
                payload["llm_call_count"],
                payload["estimated_cost_cny"],
                payload["price_missing"],
                payload["model_breakdown_json"],
                payload["first_usage_at"],
                payload["last_usage_at"],
                payload["currency"],
                payload["created_at"],
                payload["updated_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def list_task_llm_usage_by_last_usage_range(
        self,
        start_iso: str,
        end_iso: str,
    ) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT * FROM task_llm_usage
            WHERE last_usage_at IS NOT NULL
              AND last_usage_at >= ?
              AND last_usage_at < ?
            ORDER BY last_usage_at DESC
            """,
            [start_iso, end_iso],
        )
        return [self._row_to_task_llm_usage(row) for row in rows]

    @staticmethod
    def _normalize_usage_scope(scope: str) -> Literal["task", "user", "all"]:
        text = str(scope or "task").strip().lower()
        if text in {"task", "user", "all"}:
            return text  # type: ignore[return-value]
        return "task"

    def list_admin_usage_table(
        self,
        *,
        start_iso: str,
        end_iso: str,
        scope: str = "task",
        q: Optional[str] = None,
        task_type: Optional[str] = None,
        task_status: Optional[str] = None,
        model: Optional[str] = None,
        page: int = 1,
        page_size: int = 10,
        sort_by: str = "lastUsageAt",
        sort_order: str = "desc",
    ) -> Dict[str, Any]:
        normalized_scope = self._normalize_usage_scope(scope)
        direction = "ASC" if str(sort_order or "").strip().lower() == "asc" else "DESC"
        owner_expr = "CASE WHEN tu.owner_id IS NULL OR TRIM(tu.owner_id) = '' THEN '-' ELSE tu.owner_id END"
        base_where = [
            "tu.last_usage_at IS NOT NULL",
            "tu.last_usage_at >= ?",
            "tu.last_usage_at < ?",
        ]
        base_params: List[Any] = [start_iso, end_iso]

        normalized_task_type = str(task_type or "").strip().lower()
        if normalized_task_type:
            base_where.append("LOWER(COALESCE(tu.task_type, '')) = ?")
            base_params.append(normalized_task_type)

        normalized_task_status = str(task_status or "").strip().lower()
        if normalized_task_status:
            base_where.append("LOWER(COALESCE(tu.task_status, '')) = ?")
            base_params.append(normalized_task_status)

        normalized_model = str(model or "").strip().lower()
        if normalized_model:
            base_where.append(
                "EXISTS (SELECT 1 FROM json_each(COALESCE(tu.model_breakdown_json, '{}')) jm WHERE LOWER(CAST(jm.key AS TEXT)) = ?)"
            )
            base_params.append(normalized_model)

        base_where_clause = " AND ".join(base_where)
        summary_row = self._fetchone(
            f"""
            SELECT
                COUNT(*) AS total_tasks,
                COUNT(DISTINCT {owner_expr}) AS total_users,
                COALESCE(SUM(tu.total_tokens), 0) AS total_tokens,
                COALESCE(SUM(tu.llm_call_count), 0) AS total_llm_call_count,
                COALESCE(SUM(tu.estimated_cost_cny), 0) AS total_estimated_cost_cny,
                MAX(CASE WHEN tu.price_missing = 1 THEN 1 ELSE 0 END) AS price_missing
            FROM task_llm_usage tu
            WHERE {base_where_clause}
            """,
            base_params,
        ) or {}

        total_tasks = int(summary_row.get("total_tasks") or 0)
        total_users = int(summary_row.get("total_users") or 0)
        total_tokens = int(summary_row.get("total_tokens") or 0)
        total_llm_call_count = int(summary_row.get("total_llm_call_count") or 0)
        total_estimated_cost_cny = float(summary_row.get("total_estimated_cost_cny") or 0.0)
        price_missing = bool(int(summary_row.get("price_missing") or 0))

        entity_count = total_users if normalized_scope == "user" else total_tasks
        summary = {
            "total_tasks": total_tasks,
            "total_users": total_users,
            "total_tokens": total_tokens,
            "total_llm_call_count": total_llm_call_count,
            "total_estimated_cost_cny": round(total_estimated_cost_cny, 6),
            "avg_tokens_per_entity": round((total_tokens / entity_count), 3) if entity_count else 0.0,
            "avg_cost_per_entity_cny": round((total_estimated_cost_cny / entity_count), 6) if entity_count else 0.0,
            "entity_type": normalized_scope,
            "price_missing": price_missing,
        }

        if normalized_scope == "all":
            items = []
            if total_tasks:
                items = [
                    {
                        "task_count": total_tasks,
                        "user_count": total_users,
                        "total_tokens": total_tokens,
                        "llm_call_count": total_llm_call_count,
                        "estimated_cost_cny": round(total_estimated_cost_cny, 6),
                        "price_missing": price_missing,
                    }
                ]
            return {
                "total": len(items),
                "price_missing": price_missing,
                "summary": {
                    **summary,
                    "entity_type": "all",
                },
                "items": items,
            }

        normalized_q = str(q or "").strip()
        q_where_clause = ""
        q_params: List[Any] = []
        if normalized_q:
            wildcard = f"%{normalized_q}%"
            if normalized_scope == "user":
                q_where_clause = "WHERE (g.owner_id LIKE ? OR COALESCE(g.user_name, '') LIKE ?)"
                q_params.extend([wildcard, wildcard])
            else:
                q_where_clause = (
                    f" AND (tu.task_id LIKE ? OR {owner_expr} LIKE ? OR COALESCE(u.name, '') LIKE ? "
                    "OR COALESCE(tu.task_type, '') LIKE ? OR COALESCE(tu.task_status, '') LIKE ? "
                    "OR COALESCE(tu.model_breakdown_json, '') LIKE ?)"
                )
                q_params.extend([wildcard, wildcard, wildcard, wildcard, wildcard, wildcard])

        offset = max(0, (page - 1) * page_size)
        if normalized_scope == "user":
            safe_sort_map = {
                "ownerId": "g.owner_id",
                "userName": "COALESCE(g.user_name, '')",
                "taskCount": "g.task_count",
                "totalTokens": "g.total_tokens",
                "llmCallCount": "g.llm_call_count",
                "estimatedCostCny": "g.estimated_cost_cny",
                "latestUsageAt": "COALESCE(g.latest_usage_at, '')",
            }
            safe_sort = safe_sort_map.get(sort_by, "g.total_tokens")
            total_row = self._fetchone(
                f"""
                WITH filtered AS (
                    SELECT
                        {owner_expr} AS owner_id,
                        u.name AS user_name,
                        tu.total_tokens AS total_tokens,
                        tu.llm_call_count AS llm_call_count,
                        tu.estimated_cost_cny AS estimated_cost_cny,
                        tu.price_missing AS price_missing,
                        tu.last_usage_at AS last_usage_at
                    FROM task_llm_usage tu
                    LEFT JOIN users u ON tu.owner_id = u.owner_id
                    WHERE {base_where_clause}
                ),
                grouped AS (
                    SELECT
                        f.owner_id AS owner_id,
                        MAX(f.user_name) AS user_name,
                        COUNT(*) AS task_count,
                        COALESCE(SUM(f.total_tokens), 0) AS total_tokens,
                        COALESCE(SUM(f.llm_call_count), 0) AS llm_call_count,
                        COALESCE(SUM(f.estimated_cost_cny), 0) AS estimated_cost_cny,
                        MAX(CASE WHEN f.price_missing = 1 THEN 1 ELSE 0 END) AS price_missing,
                        MAX(f.last_usage_at) AS latest_usage_at
                    FROM filtered f
                    GROUP BY f.owner_id
                )
                SELECT COUNT(*) AS c
                FROM grouped g
                {q_where_clause}
                """,
                base_params + q_params,
            ) or {}
            rows = self._fetchall(
                f"""
                WITH filtered AS (
                    SELECT
                        {owner_expr} AS owner_id,
                        u.name AS user_name,
                        tu.total_tokens AS total_tokens,
                        tu.llm_call_count AS llm_call_count,
                        tu.estimated_cost_cny AS estimated_cost_cny,
                        tu.price_missing AS price_missing,
                        tu.last_usage_at AS last_usage_at
                    FROM task_llm_usage tu
                    LEFT JOIN users u ON tu.owner_id = u.owner_id
                    WHERE {base_where_clause}
                ),
                grouped AS (
                    SELECT
                        f.owner_id AS owner_id,
                        MAX(f.user_name) AS user_name,
                        COUNT(*) AS task_count,
                        COALESCE(SUM(f.total_tokens), 0) AS total_tokens,
                        COALESCE(SUM(f.llm_call_count), 0) AS llm_call_count,
                        COALESCE(SUM(f.estimated_cost_cny), 0) AS estimated_cost_cny,
                        MAX(CASE WHEN f.price_missing = 1 THEN 1 ELSE 0 END) AS price_missing,
                        MAX(f.last_usage_at) AS latest_usage_at
                    FROM filtered f
                    GROUP BY f.owner_id
                )
                SELECT *
                FROM grouped g
                {q_where_clause}
                ORDER BY {safe_sort} {direction}, g.owner_id ASC
                LIMIT ? OFFSET ?
                """,
                base_params + q_params + [page_size, offset],
            )
            return {
                "total": int(total_row.get("c") or 0),
                "price_missing": price_missing,
                "summary": summary,
                "items": [
                    {
                        "owner_id": row.get("owner_id"),
                        "user_name": row.get("user_name"),
                        "task_count": int(row.get("task_count") or 0),
                        "total_tokens": int(row.get("total_tokens") or 0),
                        "llm_call_count": int(row.get("llm_call_count") or 0),
                        "estimated_cost_cny": round(float(row.get("estimated_cost_cny") or 0), 6),
                        "price_missing": bool(int(row.get("price_missing") or 0)),
                        "latest_usage_at": row.get("latest_usage_at"),
                    }
                    for row in rows
                ],
            }

        safe_sort_map = {
            "taskId": "tu.task_id",
            "ownerId": owner_expr,
            "userName": "COALESCE(u.name, '')",
            "taskType": "COALESCE(tu.task_type, '')",
            "taskStatus": "COALESCE(tu.task_status, '')",
            "totalTokens": "tu.total_tokens",
            "estimatedCostCny": "tu.estimated_cost_cny",
            "llmCallCount": "tu.llm_call_count",
            "lastUsageAt": "COALESCE(tu.last_usage_at, '')",
        }
        safe_sort = safe_sort_map.get(sort_by, "COALESCE(tu.last_usage_at, '')")
        total_row = self._fetchone(
            f"""
            SELECT COUNT(*) AS c
            FROM task_llm_usage tu
            LEFT JOIN users u ON tu.owner_id = u.owner_id
            WHERE {base_where_clause}{q_where_clause}
            """,
            base_params + q_params,
        ) or {}
        rows = self._fetchall(
            f"""
            SELECT
                tu.task_id AS task_id,
                {owner_expr} AS owner_id,
                u.name AS user_name,
                tu.task_type AS task_type,
                tu.task_status AS task_status,
                tu.total_tokens AS total_tokens,
                tu.llm_call_count AS llm_call_count,
                tu.estimated_cost_cny AS estimated_cost_cny,
                tu.price_missing AS price_missing,
                tu.model_breakdown_json AS model_breakdown_json,
                tu.last_usage_at AS last_usage_at
            FROM task_llm_usage tu
            LEFT JOIN users u ON tu.owner_id = u.owner_id
            WHERE {base_where_clause}{q_where_clause}
            ORDER BY {safe_sort} {direction}, tu.task_id DESC
            LIMIT ? OFFSET ?
            """,
            base_params + q_params + [page_size, offset],
        )

        task_items: List[Dict[str, Any]] = []
        for row in rows:
            model_breakdown = self._parse_metadata(row.get("model_breakdown_json"))
            models = [str(key) for key in model_breakdown.keys()] if isinstance(model_breakdown, dict) else []
            task_items.append(
                {
                    "task_id": row.get("task_id"),
                    "owner_id": row.get("owner_id"),
                    "user_name": row.get("user_name"),
                    "task_type": row.get("task_type") or "",
                    "task_status": row.get("task_status") or "",
                    "total_tokens": int(row.get("total_tokens") or 0),
                    "llm_call_count": int(row.get("llm_call_count") or 0),
                    "estimated_cost_cny": round(float(row.get("estimated_cost_cny") or 0), 6),
                    "price_missing": bool(int(row.get("price_missing") or 0)),
                    "models": models,
                    "last_usage_at": row.get("last_usage_at"),
                }
            )
        return {
            "total": int(total_row.get("c") or 0),
            "price_missing": price_missing,
            "summary": summary,
            "items": task_items,
        }

    def _row_to_system_log(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "log_id": row.get("log_id"),
            "timestamp": row.get("timestamp"),
            "category": row.get("category"),
            "event_name": row.get("event_name"),
            "level": row.get("level"),
            "owner_id": row.get("owner_id"),
            "user_name": row.get("user_name"),
            "task_id": row.get("task_id"),
            "task_type": row.get("task_type"),
            "request_id": row.get("request_id"),
            "trace_id": row.get("trace_id"),
            "method": row.get("method"),
            "path": row.get("path"),
            "status_code": row.get("status_code"),
            "duration_ms": row.get("duration_ms"),
            "provider": row.get("provider"),
            "target_host": row.get("target_host"),
            "success": bool(int(row.get("success") or 0)),
            "message": row.get("message"),
            "payload_inline_json": row.get("payload_inline_json"),
            "payload_file_path": row.get("payload_file_path"),
            "payload_bytes": int(row.get("payload_bytes") or 0),
            "payload_overflow": bool(int(row.get("payload_overflow") or 0)),
            "created_at": row.get("created_at"),
        }

    def insert_system_log(self, record: Dict[str, Any]) -> bool:
        payload = {
            "log_id": str(record.get("log_id", "")).strip(),
            "timestamp": str(record.get("timestamp") or utc_now_z()),
            "category": str(record.get("category", "")).strip(),
            "event_name": str(record.get("event_name", "")).strip(),
            "level": str(record.get("level", "INFO")).strip().upper() or "INFO",
            "owner_id": str(record.get("owner_id") or "").strip() or None,
            "task_id": str(record.get("task_id") or "").strip() or None,
            "task_type": str(record.get("task_type") or "").strip() or None,
            "request_id": str(record.get("request_id") or "").strip() or None,
            "trace_id": str(record.get("trace_id") or "").strip() or None,
            "method": str(record.get("method") or "").strip() or None,
            "path": str(record.get("path") or "").strip() or None,
            "status_code": int(record.get("status_code")) if record.get("status_code") is not None else None,
            "duration_ms": int(record.get("duration_ms")) if record.get("duration_ms") is not None else None,
            "provider": str(record.get("provider") or "").strip() or None,
            "target_host": str(record.get("target_host") or "").strip() or None,
            "success": 1 if record.get("success") else 0,
            "message": str(record.get("message") or "").strip() or None,
            "payload_inline_json": str(record.get("payload_inline_json") or "").strip() or None,
            "payload_file_path": str(record.get("payload_file_path") or "").strip() or None,
            "payload_bytes": int(record.get("payload_bytes") or 0),
            "payload_overflow": 1 if record.get("payload_overflow") else 0,
            "created_at": str(record.get("created_at") or utc_now_z()),
        }
        if not payload["log_id"] or not payload["category"] or not payload["event_name"]:
            return False

        result = self._request(
            """
            INSERT OR REPLACE INTO system_logs (
                log_id, timestamp, category, event_name, level,
                owner_id, task_id, task_type, request_id, trace_id,
                method, path, status_code, duration_ms,
                provider, target_host, success, message,
                payload_inline_json, payload_file_path, payload_bytes, payload_overflow, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                payload["log_id"],
                payload["timestamp"],
                payload["category"],
                payload["event_name"],
                payload["level"],
                payload["owner_id"],
                payload["task_id"],
                payload["task_type"],
                payload["request_id"],
                payload["trace_id"],
                payload["method"],
                payload["path"],
                payload["status_code"],
                payload["duration_ms"],
                payload["provider"],
                payload["target_host"],
                payload["success"],
                payload["message"],
                payload["payload_inline_json"],
                payload["payload_file_path"],
                payload["payload_bytes"],
                payload["payload_overflow"],
                payload["created_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def get_system_log(self, log_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT * FROM system_logs WHERE log_id = ?",
            [log_id],
        )
        return self._row_to_system_log(row) if row else None

    def list_system_logs(
        self,
        *,
        category: Optional[str] = None,
        event_name: Optional[str] = None,
        owner_id: Optional[str] = None,
        user_name: Optional[str] = None,
        task_id: Optional[str] = None,
        request_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        provider: Optional[str] = None,
        success: Optional[bool] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = 10,
    ) -> Dict[str, Any]:
        where = ["1=1"]
        params: List[Any] = []

        if category:
            where.append("sl.category = ?")
            params.append(category)
        if event_name:
            where.append("sl.event_name = ?")
            params.append(event_name)
        if owner_id:
            where.append("sl.owner_id = ?")
            params.append(owner_id)
        if user_name:
            where.append("u.name LIKE ?")
            params.append(f"%{user_name}%")
        if task_id:
            where.append("sl.task_id = ?")
            params.append(task_id)
        if request_id:
            where.append("sl.request_id = ?")
            params.append(request_id)
        if trace_id:
            where.append("sl.trace_id = ?")
            params.append(trace_id)
        if provider:
            where.append("sl.provider = ?")
            params.append(provider)
        if success is not None:
            where.append("sl.success = ?")
            params.append(1 if success else 0)
        if date_from:
            where.append("sl.timestamp >= ?")
            params.append(date_from)
        if date_to:
            where.append("sl.timestamp <= ?")
            params.append(date_to)
        if q:
            where.append(
                "(sl.category LIKE ? OR sl.event_name LIKE ? OR sl.task_id LIKE ? "
                "OR sl.request_id LIKE ? OR sl.trace_id LIKE ? OR sl.message LIKE ? OR sl.path LIKE ? "
                "OR sl.provider LIKE ? OR u.name LIKE ?)"
            )
            wildcard = f"%{q}%"
            params.extend([wildcard] * 9)

        where_clause = " AND ".join(where)
        total_row = self._fetchone(
            f"""
            SELECT COUNT(*) AS c
            FROM system_logs sl
            LEFT JOIN users u ON sl.owner_id = u.owner_id
            WHERE {where_clause}
            """,
            params,
        )
        offset = max(0, (page - 1) * page_size)
        rows = self._fetchall(
            f"""
            SELECT sl.*, u.name AS user_name
            FROM system_logs sl
            LEFT JOIN users u ON sl.owner_id = u.owner_id
            WHERE {where_clause}
            ORDER BY sl.timestamp DESC
            LIMIT ? OFFSET ?
            """,
            params + [page_size, offset],
        )
        return {
            "total": int((total_row or {}).get("c") or 0),
            "items": [self._row_to_system_log(row) for row in rows],
        }

    def summarize_system_logs(
        self,
        *,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        where = ["1=1"]
        params: List[Any] = []
        if date_from:
            where.append("timestamp >= ?")
            params.append(date_from)
        if date_to:
            where.append("timestamp <= ?")
            params.append(date_to)
        where_clause = " AND ".join(where)

        overview_row = self._fetchone(
            f"""
            SELECT
                COUNT(*) AS total_logs,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed_logs,
                SUM(CASE WHEN category = 'llm_call' THEN 1 ELSE 0 END) AS llm_call_count
            FROM system_logs
            WHERE {where_clause}
            """,
            params,
        ) or {}
        category_rows = self._fetchall(
            f"""
            SELECT category, COUNT(*) AS count
            FROM system_logs
            WHERE {where_clause}
            GROUP BY category
            ORDER BY count DESC
            """,
            params,
        )

        total_logs = int(overview_row.get("total_logs") or 0)
        failed_logs = int(overview_row.get("failed_logs") or 0)
        llm_call_count = int(overview_row.get("llm_call_count") or 0)
        failed_rate = (failed_logs / total_logs) if total_logs else 0.0
        return {
            "totalLogs": total_logs,
            "failedLogs": failed_logs,
            "failedRate": round(failed_rate, 6),
            "llmCallCount": llm_call_count,
            "byCategory": [
                {"category": row.get("category"), "count": int(row.get("count") or 0)}
                for row in category_rows
            ],
        }

    def cleanup_system_logs_before(self, cutoff_iso: str) -> int:
        result = self._request(
            "DELETE FROM system_logs WHERE timestamp < ?",
            [cutoff_iso],
        )
        return self._changed_rows(result)

    def list_system_log_payload_paths_for_policy_cleanup(self) -> List[str]:
        policy_where = (
            "(category = 'user_action' AND UPPER(COALESCE(method, '')) = 'GET') "
            "OR (category != 'llm_call' AND category != 'user_action' AND success = 1)"
        )
        rows = self._fetchall(
            f"""
            SELECT payload_file_path
            FROM system_logs
            WHERE ({policy_where})
              AND payload_file_path IS NOT NULL
              AND payload_file_path != ''
            """
        )
        return [str(row.get("payload_file_path") or "").strip() for row in rows if str(row.get("payload_file_path") or "").strip()]

    def cleanup_system_logs_by_policy(self) -> int:
        policy_where = (
            "(category = 'user_action' AND UPPER(COALESCE(method, '')) = 'GET') "
            "OR (category != 'llm_call' AND category != 'user_action' AND success = 1)"
        )
        result = self._request(f"DELETE FROM system_logs WHERE {policy_where}")
        return self._changed_rows(result)

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        pn: Optional[str] = None,
        owner_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "created_at",
        order_desc: bool = True,
    ) -> List[Task]:
        where = ["deleted_at IS NULL"]
        params: List[Any] = []

        if status:
            where.append("status = ?")
            params.append(status.value)
        if pn:
            where.append("pn LIKE ?")
            params.append(f"%{pn}%")
        if owner_id:
            where.append("owner_id = ?")
            params.append(owner_id)

        allowed_order_columns = {"created_at", "updated_at", "progress", "status", "pn"}
        safe_order_by = order_by if order_by in allowed_order_columns else "created_at"
        direction = "DESC" if order_desc else "ASC"

        sql = f"""
            SELECT * FROM tasks
            WHERE {' AND '.join(where)}
            ORDER BY {safe_order_by} {direction}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        rows = self._fetchall(sql, params)
        return [self._row_to_task(row) for row in rows]

    def list_admin_users(
        self,
        *,
        q: Optional[str] = None,
        role: Optional[str] = None,
        page: int = 1,
        page_size: int = 10,
        sort_by: str = "latest_task_at",
        sort_order: str = "desc",
    ) -> Dict[str, Any]:
        where = ["1=1"]
        params: List[Any] = []

        if role:
            where.append("base.role = ?")
            params.append(role)

        if q:
            wildcard = f"%{q}%"
            where.append("(base.owner_id LIKE ? OR COALESCE(base.user_name, '') LIKE ? OR COALESCE(base.email, '') LIKE ?)")
            params.extend([wildcard, wildcard, wildcard])

        where_clause = " AND ".join(where)
        safe_sort_map = {
            "owner_id": "base.owner_id",
            "user_name": "COALESCE(base.user_name, '')",
            "email": "COALESCE(base.email, '')",
            "role": "COALESCE(base.role, '')",
            "last_login_at": "COALESCE(base.last_login_at, '')",
            "created_at": "COALESCE(base.created_at, '')",
            "task_count": "base.task_count",
            "latest_task_at": "COALESCE(base.latest_task_at, '')",
        }
        safe_sort = safe_sort_map.get(sort_by, "COALESCE(base.latest_task_at, '')")
        direction = "ASC" if str(sort_order or "").strip().lower() == "asc" else "DESC"
        offset = max(0, (page - 1) * page_size)

        total_row = self._fetchone(
            f"""
            WITH user_task_stats AS (
                SELECT
                    owner_id,
                    COUNT(*) AS task_count,
                    MAX(updated_at) AS latest_task_at
                FROM tasks
                WHERE deleted_at IS NULL
                  AND owner_id IS NOT NULL
                  AND TRIM(owner_id) <> ''
                GROUP BY owner_id
            ),
            base AS (
                SELECT
                    u.owner_id AS owner_id,
                    u.name AS user_name,
                    u.email AS email,
                    u.role AS role,
                    u.last_login_at AS last_login_at,
                    u.created_at AS created_at,
                    COALESCE(s.task_count, 0) AS task_count,
                    s.latest_task_at AS latest_task_at
                FROM users u
                LEFT JOIN user_task_stats s ON u.owner_id = s.owner_id
                UNION
                SELECT
                    s.owner_id AS owner_id,
                    NULL AS user_name,
                    NULL AS email,
                    NULL AS role,
                    NULL AS last_login_at,
                    NULL AS created_at,
                    s.task_count AS task_count,
                    s.latest_task_at AS latest_task_at
                FROM user_task_stats s
                LEFT JOIN users u ON s.owner_id = u.owner_id
                WHERE u.owner_id IS NULL
            )
            SELECT COUNT(*) AS c
            FROM base
            WHERE {where_clause}
            """,
            params,
        )

        rows = self._fetchall(
            f"""
            WITH user_task_stats AS (
                SELECT
                    owner_id,
                    COUNT(*) AS task_count,
                    MAX(updated_at) AS latest_task_at
                FROM tasks
                WHERE deleted_at IS NULL
                  AND owner_id IS NOT NULL
                  AND TRIM(owner_id) <> ''
                GROUP BY owner_id
            ),
            base AS (
                SELECT
                    u.owner_id AS owner_id,
                    u.name AS user_name,
                    u.email AS email,
                    u.role AS role,
                    u.last_login_at AS last_login_at,
                    u.created_at AS created_at,
                    COALESCE(s.task_count, 0) AS task_count,
                    s.latest_task_at AS latest_task_at
                FROM users u
                LEFT JOIN user_task_stats s ON u.owner_id = s.owner_id
                UNION
                SELECT
                    s.owner_id AS owner_id,
                    NULL AS user_name,
                    NULL AS email,
                    NULL AS role,
                    NULL AS last_login_at,
                    NULL AS created_at,
                    s.task_count AS task_count,
                    s.latest_task_at AS latest_task_at
                FROM user_task_stats s
                LEFT JOIN users u ON s.owner_id = u.owner_id
                WHERE u.owner_id IS NULL
            )
            SELECT *
            FROM base
            WHERE {where_clause}
            ORDER BY {safe_sort} {direction}, base.owner_id ASC
            LIMIT ? OFFSET ?
            """,
                params + [page_size, offset],
            )

        return {
            "total": int((total_row or {}).get("c") or 0),
            "items": [
                {
                    "owner_id": row.get("owner_id"),
                    "user_name": row.get("user_name"),
                    "email": row.get("email"),
                    "role": row.get("role"),
                    "last_login_at": row.get("last_login_at"),
                    "created_at": row.get("created_at"),
                    "task_count": int(row.get("task_count") or 0),
                    "latest_task_at": row.get("latest_task_at"),
                }
                for row in rows
            ],
        }

    def summarize_admin_users(self) -> Dict[str, Any]:
        cutoff_1d, _ = local_recent_day_window_to_utc(1)
        cutoff_7d, _ = local_recent_day_window_to_utc(7)
        cutoff_30d, _ = local_recent_day_window_to_utc(30)
        overview_row = self._fetchone(
            """
            WITH all_identities AS (
                SELECT owner_id
                FROM users
                WHERE owner_id IS NOT NULL
                  AND TRIM(owner_id) <> ''
                UNION
                SELECT owner_id
                FROM tasks
                WHERE deleted_at IS NULL
                  AND owner_id IS NOT NULL
                  AND TRIM(owner_id) <> ''
            )
            SELECT
                (SELECT COUNT(*) FROM all_identities) AS total_users,
                (
                    SELECT COUNT(*)
                    FROM users
                    WHERE owner_id IS NOT NULL
                      AND TRIM(owner_id) <> ''
                ) AS registered_users
            """,
        )
        active_row = self._fetchone(
            """
            SELECT
                COUNT(DISTINCT CASE WHEN created_at >= ? THEN owner_id END) AS active_1d,
                COUNT(DISTINCT CASE WHEN created_at >= ? THEN owner_id END) AS active_7d,
                COUNT(DISTINCT CASE WHEN created_at >= ? THEN owner_id END) AS active_30d
            FROM tasks
            WHERE deleted_at IS NULL
              AND owner_id IS NOT NULL
              AND TRIM(owner_id) <> ''
            """,
            [cutoff_1d, cutoff_7d, cutoff_30d],
        )
        new_row = self._fetchone(
            """
            WITH identity_events AS (
                SELECT owner_id, created_at AS seen_at
                FROM users
                WHERE owner_id IS NOT NULL
                  AND TRIM(owner_id) <> ''
                UNION ALL
                SELECT owner_id, created_at AS seen_at
                FROM tasks
                WHERE deleted_at IS NULL
                  AND owner_id IS NOT NULL
                  AND TRIM(owner_id) <> ''
            ),
            first_seen AS (
                SELECT owner_id, MIN(seen_at) AS first_seen_at
                FROM identity_events
                GROUP BY owner_id
            )
            SELECT
                SUM(CASE WHEN first_seen_at >= ? THEN 1 ELSE 0 END) AS new_1d,
                SUM(CASE WHEN first_seen_at >= ? THEN 1 ELSE 0 END) AS new_7d,
                SUM(CASE WHEN first_seen_at >= ? THEN 1 ELSE 0 END) AS new_30d
            FROM first_seen
            """,
            [cutoff_1d, cutoff_7d, cutoff_30d],
        )
        return {
            "userStats": {
                "totalUsers": int((overview_row or {}).get("total_users") or 0),
                "registeredUsers": int((overview_row or {}).get("registered_users") or 0),
                "activeUsers1d": int((active_row or {}).get("active_1d") or 0),
                "activeUsers7d": int((active_row or {}).get("active_7d") or 0),
                "activeUsers30d": int((active_row or {}).get("active_30d") or 0),
                "newUsers1d": int((new_row or {}).get("new_1d") or 0),
                "newUsers7d": int((new_row or {}).get("new_7d") or 0),
                "newUsers30d": int((new_row or {}).get("new_30d") or 0),
            }
        }

    def list_admin_tasks(
        self,
        *,
        q: Optional[str] = None,
        user_name: Optional[str] = None,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        page: int = 1,
        page_size: int = 10,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> Dict[str, Any]:
        where = ["t.deleted_at IS NULL"]
        params: List[Any] = []

        if user_name:
            where.append("u.name LIKE ?")
            params.append(f"%{user_name}%")
        if task_type:
            where.append("t.task_type = ?")
            params.append(task_type)
        if status:
            where.append("LOWER(t.status) = ?")
            params.append(str(status).strip().lower())
        if date_from:
            where.append("t.created_at >= ?")
            params.append(date_from)
        if date_to:
            where.append("t.created_at <= ?")
            params.append(date_to)
        if q:
            wildcard = f"%{q}%"
            where.append(
                "(t.id LIKE ? OR COALESCE(t.title, '') LIKE ? OR COALESCE(t.pn, '') LIKE ? OR COALESCE(t.owner_id, '') LIKE ? OR COALESCE(u.name, '') LIKE ?)"
            )
            params.extend([wildcard, wildcard, wildcard, wildcard, wildcard])

        where_clause = " AND ".join(where)
        safe_sort_map = {
            "task_id": "t.id",
            "title": "COALESCE(t.title, '')",
            "user_name": "COALESCE(u.name, '')",
            "task_type": "COALESCE(t.task_type, '')",
            "status": "COALESCE(t.status, '')",
            "created_at": "COALESCE(t.created_at, '')",
            "updated_at": "COALESCE(t.updated_at, '')",
            "completed_at": "COALESCE(t.completed_at, '')",
        }
        safe_sort = safe_sort_map.get(sort_by, "COALESCE(t.created_at, '')")
        direction = "ASC" if str(sort_order or "").strip().lower() == "asc" else "DESC"
        offset = max(0, (page - 1) * page_size)

        total_row = self._fetchone(
            f"""
            SELECT COUNT(*) AS c
            FROM tasks t
            LEFT JOIN users u ON t.owner_id = u.owner_id
            WHERE {where_clause}
            """,
            params,
        )
        rows = self._fetchall(
            f"""
            SELECT
                t.id AS task_id,
                t.title AS title,
                t.owner_id AS owner_id,
                u.name AS user_name,
                t.task_type AS task_type,
                t.status AS status,
                t.created_at AS created_at,
                t.updated_at AS updated_at,
                t.completed_at AS completed_at
            FROM tasks t
            LEFT JOIN users u ON t.owner_id = u.owner_id
            WHERE {where_clause}
            ORDER BY {safe_sort} {direction}, t.id DESC
            LIMIT ? OFFSET ?
            """,
            params + [page_size, offset],
        )

        def _parse_iso(value: Any) -> Optional[datetime]:
            return parse_storage_ts(value, naive_strategy="utc")

        def _calc_duration_seconds(created_at: Any, completed_at: Any) -> Optional[int]:
            created_dt = _parse_iso(created_at)
            if not created_dt:
                return None
            end_dt = _parse_iso(completed_at)
            if not end_dt:
                end_dt = parse_storage_ts(utc_now_z(), naive_strategy="utc")
            try:
                seconds = int(end_dt.timestamp() - created_dt.timestamp())
            except Exception:
                return None
            return max(0, seconds)

        return {
            "total": int((total_row or {}).get("c") or 0),
            "items": [
                {
                    "task_id": row.get("task_id"),
                    "title": row.get("title"),
                    "owner_id": row.get("owner_id"),
                    "user_name": row.get("user_name"),
                    "task_type": row.get("task_type"),
                    "status": row.get("status"),
                    "duration_seconds": _calc_duration_seconds(row.get("created_at"), row.get("completed_at")),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                    "completed_at": row.get("completed_at"),
                }
                for row in rows
            ],
        }

    def summarize_admin_tasks(self) -> Dict[str, Any]:
        cutoff_1d, _ = local_recent_day_window_to_utc(1)
        cutoff_7d, _ = local_recent_day_window_to_utc(7)
        cutoff_30d, _ = local_recent_day_window_to_utc(30)
        task_type_rows = self._fetchall(
            """
            SELECT
                COALESCE(NULLIF(TRIM(task_type), ''), 'unknown') AS task_type,
                SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS count_1d,
                SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS count_7d,
                SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS count_30d
            FROM tasks
            WHERE deleted_at IS NULL
            GROUP BY COALESCE(NULLIF(TRIM(task_type), ''), 'unknown')
            ORDER BY count_30d DESC, task_type ASC
            """,
            [cutoff_1d, cutoff_7d, cutoff_30d],
        )
        return {
            "taskTypeWindows": [
                {
                    "taskType": row.get("task_type"),
                    "count1d": int(row.get("count_1d") or 0),
                    "count7d": int(row.get("count_7d") or 0),
                    "count30d": int(row.get("count_30d") or 0),
                }
                for row in task_type_rows
            ]
        }

    def get_admin_task_detail(self, task_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT
                t.id AS task_id,
                t.owner_id AS owner_id,
                u.name AS user_name,
                t.task_type AS task_type,
                t.pn AS pn,
                t.title AS title,
                t.status AS status,
                t.progress AS progress,
                t.current_step AS current_step,
                t.output_dir AS output_dir,
                t.error_message AS error_message,
                t.created_at AS created_at,
                t.updated_at AS updated_at,
                t.completed_at AS completed_at,
                t.metadata AS metadata
            FROM tasks t
            LEFT JOIN users u ON t.owner_id = u.owner_id
            WHERE t.id = ?
              AND t.deleted_at IS NULL
            LIMIT 1
            """,
            [task_id],
        )
        if not row:
            return None
        return {
            "task_id": row.get("task_id"),
            "owner_id": row.get("owner_id"),
            "user_name": row.get("user_name"),
            "task_type": row.get("task_type"),
            "pn": row.get("pn"),
            "title": row.get("title"),
            "status": row.get("status"),
            "progress": int(row.get("progress") or 0),
            "current_step": row.get("current_step"),
            "output_dir": row.get("output_dir"),
            "error_message": row.get("error_message"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "completed_at": row.get("completed_at"),
            "metadata": self._parse_metadata(row.get("metadata")),
        }

    def count_tasks(
        self,
        status: Optional[TaskStatus] = None,
        pn: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> int:
        where = ["deleted_at IS NULL"]
        params: List[Any] = []

        if status:
            where.append("status = ?")
            params.append(status.value)
        if pn:
            where.append("pn LIKE ?")
            params.append(f"%{pn}%")
        if owner_id:
            where.append("owner_id = ?")
            params.append(owner_id)

        row = self._fetchone(f"SELECT COUNT(*) AS c FROM tasks WHERE {' AND '.join(where)}", params)
        return int(row["c"]) if row else 0

    def count_user_tasks_today(
        self,
        owner_id: str,
        tz_offset_hours: int = 8,
        task_type: Optional[str] = None,
        include_deleted: bool = False,
        statuses: Optional[List[str]] = None,
    ) -> int:
        del tz_offset_hours
        start_iso, end_iso = local_recent_day_window_to_utc(1)
        where = ["owner_id = ?", "created_at >= ?", "created_at < ?"]
        params: List[Any] = [owner_id, start_iso, end_iso]
        if task_type:
            where.append("task_type = ?")
            params.append(task_type)
        normalized_statuses = [
            str(status).strip().lower()
            for status in (statuses or [])
            if str(status).strip()
        ]
        if normalized_statuses:
            placeholders = ", ".join(["?"] * len(normalized_statuses))
            where.append(f"LOWER(status) IN ({placeholders})")
            params.extend(normalized_statuses)
        if not include_deleted:
            where.append("deleted_at IS NULL")
        row = self._fetchone(
            f"SELECT COUNT(*) AS c FROM tasks WHERE {' AND '.join(where)}",
            params,
        )
        return int(row["c"]) if row else 0

    def get_statistics(self) -> Dict[str, Any]:
        rows = self._fetchall("SELECT status, COUNT(*) AS count FROM tasks WHERE deleted_at IS NULL GROUP BY status")
        status_counts = {row["status"]: int(row["count"]) for row in rows}

        today_start_iso, today_end_iso = local_recent_day_window_to_utc(1)
        today_count_row = self._fetchone(
            "SELECT COUNT(*) AS c FROM tasks WHERE created_at >= ? AND created_at < ? AND deleted_at IS NULL",
            [today_start_iso, today_end_iso],
        )
        today_count = int(today_count_row["c"]) if today_count_row else 0

        avg_row = self._fetchone(
            """
            SELECT AVG((julianday(completed_at) - julianday(created_at)) * 24 * 60) AS avg_minutes
            FROM tasks
            WHERE status = 'completed' AND completed_at IS NOT NULL AND deleted_at IS NULL
            """
        )

        completed_patents_row = self._fetchone("SELECT COUNT(*) AS c FROM patent_analyses")
        completed_patents = int(completed_patents_row["c"]) if completed_patents_row else 0

        avg_duration = avg_row.get("avg_minutes") if avg_row else None
        return {
            "by_status": status_counts,
            "today_created": today_count,
            "avg_duration_minutes": round(float(avg_duration), 2) if avg_duration else None,
            "completed_patents": completed_patents,
        }

    def record_patent_analysis(self, pn: Optional[str], sha256: Optional[str] = None) -> bool:
        if not pn:
            return False
        normalized = pn.strip().upper()
        if not normalized:
            return False
        normalized_sha256 = str(sha256 or "").strip().lower() or None
        self._request(
            """
            INSERT INTO patent_analyses (pn, first_completed_at, sha256)
            VALUES (?, ?, ?)
            ON CONFLICT(pn) DO UPDATE SET
                sha256 = CASE
                    WHEN excluded.sha256 IS NOT NULL AND TRIM(excluded.sha256) <> '' THEN excluded.sha256
                    ELSE patent_analyses.sha256
                END
            """,
            [normalized, utc_now_z(), normalized_sha256],
        )
        return True

    def get_patent_analysis_by_pn(self, pn: Optional[str]) -> Optional[Dict[str, Any]]:
        normalized = str(pn or "").strip().upper()
        if not normalized:
            return None
        row = self._fetchone(
            "SELECT pn, first_completed_at, sha256 FROM patent_analyses WHERE pn = ?",
            [normalized],
        )
        if not row:
            return None
        return {
            "pn": str(row.get("pn") or ""),
            "first_completed_at": str(row.get("first_completed_at") or ""),
            "sha256": str(row.get("sha256") or "").strip() or None,
        }

    def get_patent_analysis_by_sha256(self, sha256: Optional[str]) -> Optional[Dict[str, Any]]:
        normalized = str(sha256 or "").strip().lower()
        if not normalized:
            return None
        row = self._fetchone(
            "SELECT pn, first_completed_at, sha256 FROM patent_analyses WHERE sha256 = ?",
            [normalized],
        )
        if not row:
            return None
        return {
            "pn": str(row.get("pn") or ""),
            "first_completed_at": str(row.get("first_completed_at") or ""),
            "sha256": str(row.get("sha256") or "").strip() or None,
        }

    def cleanup_old_tasks(self, days: int = 365, dry_run: bool = False) -> int:
        cutoff = to_utc_z(datetime.utcnow() - timedelta(days=days), naive_strategy="utc")
        rows = self._fetchall(
            """
            SELECT id FROM tasks
            WHERE deleted_at IS NOT NULL AND deleted_at < ?
            OR (updated_at < ? AND status IN ('completed', 'failed', 'cancelled') AND deleted_at IS NULL)
            """,
            [cutoff, cutoff],
        )
        task_ids = [row["id"] for row in rows]

        if dry_run:
            return len(task_ids)

        if task_ids:
            placeholders = ",".join(["?"] * len(task_ids))
            self._request(f"DELETE FROM tasks WHERE id IN ({placeholders})", task_ids)
        return len(task_ids)

    def vacuum(self):
        try:
            self._request("VACUUM")
        except Exception as exc:
            logger.warning(f"D1 VACUUM 已跳过：{exc}")

    def upsert_authing_user(self, user: User) -> User:
        now_iso = utc_now_z()
        created_at_iso = to_utc_z(user.created_at, naive_strategy="utc") if user.created_at else now_iso
        raw_profile = json.dumps(user.raw_profile, ensure_ascii=False) if user.raw_profile else None

        self._request(
            """
            INSERT INTO users (
                owner_id, authing_sub, role, name, nickname, email, phone, picture,
                raw_profile, created_at, updated_at, last_login_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_id) DO UPDATE SET
                authing_sub = excluded.authing_sub,
                role = CASE
                    WHEN users.role IS NULL OR TRIM(users.role) = '' THEN excluded.role
                    ELSE users.role
                END,
                name = CASE
                    WHEN users.name IS NULL OR TRIM(users.name) = '' THEN excluded.name
                    ELSE users.name
                END,
                nickname = CASE
                    WHEN users.nickname IS NULL OR TRIM(users.nickname) = '' THEN excluded.nickname
                    ELSE users.nickname
                END,
                email = excluded.email,
                phone = excluded.phone,
                picture = CASE
                    WHEN users.picture IS NULL OR TRIM(users.picture) = '' THEN excluded.picture
                    ELSE users.picture
                END,
                raw_profile = excluded.raw_profile,
                updated_at = excluded.updated_at,
                last_login_at = excluded.last_login_at
            """,
            [
                user.owner_id,
                user.authing_sub,
                user.role,
                user.name,
                user.nickname,
                user.email,
                user.phone,
                user.picture,
                raw_profile,
                created_at_iso,
                now_iso,
                now_iso,
            ],
        )
        row = self._fetchone("SELECT * FROM users WHERE owner_id = ?", [user.owner_id])
        return self._row_to_user(row) if row else user

    def get_user_by_owner_id(self, owner_id: str) -> Optional[User]:
        row = self._fetchone("SELECT * FROM users WHERE owner_id = ?", [owner_id])
        return self._row_to_user(row) if row else None

    def get_user_by_name(self, name: str) -> Optional[User]:
        normalized = str(name or "").strip()
        if not normalized:
            return None
        row = self._fetchone("SELECT * FROM users WHERE name = ?", [normalized])
        return self._row_to_user(row) if row else None

    def upsert_refresh_session(self, session: RefreshSession) -> RefreshSession:
        created_at_iso = to_utc_z(session.created_at, naive_strategy="utc") if session.created_at else utc_now_z()
        updated_at_iso = to_utc_z(session.updated_at, naive_strategy="utc") if session.updated_at else utc_now_z()
        self._request(
            """
            INSERT INTO refresh_sessions (
                token_hash, owner_id, expires_at, created_at, updated_at, revoked_at, replaced_by_token_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token_hash) DO UPDATE SET
                owner_id = excluded.owner_id,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at,
                revoked_at = excluded.revoked_at,
                replaced_by_token_hash = excluded.replaced_by_token_hash
            """,
            [
                session.token_hash,
                session.owner_id,
                to_utc_z(session.expires_at, naive_strategy="utc"),
                created_at_iso,
                updated_at_iso,
                to_utc_z(session.revoked_at, naive_strategy="utc") if session.revoked_at else None,
                session.replaced_by_token_hash,
            ],
        )
        row = self._fetchone(
            "SELECT * FROM refresh_sessions WHERE token_hash = ?",
            [session.token_hash],
        )
        return self._row_to_refresh_session(row) if row else session

    def get_refresh_session(self, token_hash: str) -> Optional[RefreshSession]:
        normalized = str(token_hash or "").strip()
        if not normalized:
            return None
        row = self._fetchone(
            "SELECT * FROM refresh_sessions WHERE token_hash = ?",
            [normalized],
        )
        return self._row_to_refresh_session(row) if row else None

    def revoke_refresh_session(self, token_hash: str, replaced_by_token_hash: Optional[str] = None) -> bool:
        normalized = str(token_hash or "").strip()
        if not normalized:
            return False
        now_iso = utc_now_z()
        result = self._request(
            """
            UPDATE refresh_sessions
            SET revoked_at = ?, replaced_by_token_hash = ?, updated_at = ?
            WHERE token_hash = ? AND revoked_at IS NULL
            """,
            [now_iso, replaced_by_token_hash, now_iso, normalized],
        )
        return self._changed_rows(result) > 0

    def revoke_refresh_sessions_by_owner(self, owner_id: str) -> int:
        normalized = str(owner_id or "").strip()
        if not normalized:
            return 0
        now_iso = utc_now_z()
        result = self._request(
            """
            UPDATE refresh_sessions
            SET revoked_at = ?, updated_at = ?
            WHERE owner_id = ? AND revoked_at IS NULL
            """,
            [now_iso, now_iso, normalized],
        )
        return self._changed_rows(result)

    def update_user_profile(
        self,
        owner_id: str,
        name: Optional[str],
        picture: Optional[str],
    ) -> Optional[User]:
        now_iso = utc_now_z()
        result = self._request(
            """
            UPDATE users
            SET name = ?, picture = ?, updated_at = ?
            WHERE owner_id = ?
            """,
            [name, picture, now_iso, owner_id],
        )
        if self._changed_rows(result) <= 0:
            return None
        row = self._fetchone("SELECT * FROM users WHERE owner_id = ?", [owner_id])
        return self._row_to_user(row) if row else None

    def upsert_account_month_target(
        self,
        owner_id: str,
        year: int,
        month: int,
        target_count: int,
    ) -> AccountMonthTarget:
        now_iso = utc_now_z()
        self._request(
            """
            INSERT INTO account_month_targets (
                owner_id, year, month, target_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_id, year, month) DO UPDATE SET
                target_count = excluded.target_count,
                updated_at = excluded.updated_at
            """,
            [owner_id, year, month, target_count, now_iso, now_iso],
        )
        row = self._fetchone(
            """
            SELECT * FROM account_month_targets
            WHERE owner_id = ? AND year = ? AND month = ?
            """,
            [owner_id, year, month],
        )
        if row is None:
            raise RuntimeError("Failed to upsert account month target in D1")
        return self._row_to_account_month_target(row)

    def get_account_month_target(
        self,
        owner_id: str,
        year: int,
        month: int,
    ) -> Optional[AccountMonthTarget]:
        row = self._fetchone(
            """
            SELECT * FROM account_month_targets
            WHERE owner_id = ? AND year = ? AND month = ?
            """,
            [owner_id, year, month],
        )
        return self._row_to_account_month_target(row) if row else None

    def get_latest_account_month_target_before(
        self,
        owner_id: str,
        year: int,
        month: int,
    ) -> Optional[AccountMonthTarget]:
        row = self._fetchone(
            """
            SELECT * FROM account_month_targets
            WHERE owner_id = ?
              AND (year < ? OR (year = ? AND month < ?))
            ORDER BY year DESC, month DESC
            LIMIT 1
            """,
            [owner_id, year, year, month],
        )
        return self._row_to_account_month_target(row) if row else None

    def count_user_tasks_by_created_range(
        self,
        owner_id: str,
        start_iso: str,
        end_iso: str,
        task_type: Optional[str] = None,
    ) -> int:
        where = [
            "owner_id = ?",
            "created_at >= ?",
            "created_at < ?",
            "deleted_at IS NULL",
        ]
        params: List[Any] = [owner_id, start_iso, end_iso]
        if task_type:
            where.append("task_type = ?")
            params.append(task_type)
        row = self._fetchone(
            f"SELECT COUNT(*) AS c FROM tasks WHERE {' AND '.join(where)}",
            params,
        )
        return int(row["c"]) if row else 0

    def count_user_tasks_by_completed_range(
        self,
        owner_id: str,
        start_iso: str,
        end_iso: str,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        where = [
            "owner_id = ?",
            "completed_at IS NOT NULL",
            "completed_at >= ?",
            "completed_at < ?",
            "deleted_at IS NULL",
        ]
        params: List[Any] = [owner_id, start_iso, end_iso]
        if task_type:
            where.append("task_type = ?")
            params.append(task_type)
        if status:
            where.append("status = ?")
            params.append(status)
        row = self._fetchone(
            f"SELECT COUNT(*) AS c FROM tasks WHERE {' AND '.join(where)}",
            params,
        )
        return int(row["c"]) if row else 0

    def aggregate_user_created_tasks_daily(
        self,
        owner_id: str,
        start_day: date,
        end_day: date,
    ) -> List[Dict[str, Any]]:
        start_iso, end_iso = local_day_start_end_to_utc(start_day, day_count=(end_day - start_day).days + 1)
        rows = self._fetchall(
            """
            SELECT created_at, task_type
            FROM tasks
            WHERE owner_id = ?
              AND created_at >= ?
              AND created_at < ?
              AND deleted_at IS NULL
            """,
            [owner_id, start_iso, end_iso],
        )
        bucket: Dict[tuple[str, str], int] = {}
        for row in rows:
            day = utc_to_local_day(row.get("created_at"), naive_strategy="utc")
            task_type = str(row.get("task_type") or "")
            if not day or not task_type:
                continue
            key = (day, task_type)
            bucket[key] = bucket.get(key, 0) + 1
        return [
            {"day": day, "task_type": task_type, "count": count}
            for (day, task_type), count in sorted(bucket.items(), key=lambda item: (item[0][0], item[0][1]))
        ]

    def aggregate_user_completed_tasks_daily(
        self,
        owner_id: str,
        start_day: date,
        end_day: date,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        start_iso, end_iso = local_day_start_end_to_utc(start_day, day_count=(end_day - start_day).days + 1)
        where = [
            "owner_id = ?",
            "completed_at IS NOT NULL",
            "completed_at >= ?",
            "completed_at < ?",
            "deleted_at IS NULL",
        ]
        params: List[Any] = [owner_id, start_iso, end_iso]
        if task_type:
            where.append("task_type = ?")
            params.append(task_type)
        if status:
            where.append("status = ?")
            params.append(status)

        rows = self._fetchall(
            f"""
            SELECT completed_at, task_type, status
            FROM tasks
            WHERE {' AND '.join(where)}
            """,
            params,
        )
        bucket: Dict[tuple[str, str, str], int] = {}
        for row in rows:
            day = utc_to_local_day(row.get("completed_at"), naive_strategy="utc")
            resolved_task_type = str(row.get("task_type") or "")
            resolved_status = str(row.get("status") or "")
            if not day or not resolved_task_type or not resolved_status:
                continue
            key = (day, resolved_task_type, resolved_status)
            bucket[key] = bucket.get(key, 0) + 1
        return [
            {"day": day, "task_type": resolved_task_type, "status": resolved_status, "count": count}
            for (day, resolved_task_type, resolved_status), count in sorted(
                bucket.items(),
                key=lambda item: (item[0][0], item[0][1], item[0][2]),
            )
        ]

    def _row_to_ai_search_message(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "message_id": row.get("message_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]) if row.get("plan_version") is not None else None,
            "role": row.get("role"),
            "kind": row.get("kind"),
            "content": row.get("content"),
            "stream_status": row.get("stream_status"),
            "question_id": row.get("question_id"),
            "metadata": self._parse_metadata(row.get("metadata")),
            "created_at": row.get("created_at"),
        }

    def create_ai_search_message(self, record: Dict[str, Any]) -> bool:
        payload = {
            "message_id": str(record.get("message_id", "")).strip(),
            "task_id": str(record.get("task_id", "")).strip(),
            "plan_version": int(record["plan_version"]) if record.get("plan_version") is not None else None,
            "role": str(record.get("role", "")).strip(),
            "kind": str(record.get("kind", "")).strip(),
            "content": record.get("content"),
            "stream_status": str(record.get("stream_status", "")).strip() or None,
            "question_id": str(record.get("question_id", "")).strip() or None,
            "metadata": self._encode_json_value(record.get("metadata") or {}),
            "created_at": str(record.get("created_at") or utc_now_z()),
        }
        if not payload["message_id"] or not payload["task_id"] or not payload["role"] or not payload["kind"]:
            return False
        result = self._request(
            """
            INSERT INTO ai_search_messages (
                message_id, task_id, plan_version, role, kind, content,
                stream_status, question_id, metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                payload["message_id"],
                payload["task_id"],
                payload["plan_version"],
                payload["role"],
                payload["kind"],
                payload["content"],
                payload["stream_status"],
                payload["question_id"],
                payload["metadata"],
                payload["created_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def list_ai_search_messages(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT *
            FROM ai_search_messages
            WHERE task_id = ?
            ORDER BY created_at ASC, message_id ASC
            """,
            [task_id],
        )
        return [self._row_to_ai_search_message(row) for row in rows]

    def _row_to_ai_search_plan(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "status": row.get("status"),
            "review_markdown": str(row.get("review_markdown") or ""),
            "execution_spec_json": self._parse_metadata(row.get("execution_spec_json")),
            "created_at": row.get("created_at"),
            "confirmed_at": row.get("confirmed_at"),
            "superseded_at": row.get("superseded_at"),
        }

    def get_next_ai_search_plan_version(self, task_id: str) -> int:
        row = self._fetchone(
            "SELECT COALESCE(MAX(plan_version), 0) + 1 AS next_version FROM ai_search_plans WHERE task_id = ?",
            [task_id],
        )
        return int(row.get("next_version") or 1) if row else 1

    def create_ai_search_plan(self, record: Dict[str, Any]) -> bool:
        payload = {
            "task_id": str(record.get("task_id", "")).strip(),
            "plan_version": int(record.get("plan_version") or 0),
            "status": str(record.get("status", "")).strip(),
            "review_markdown": str(record.get("review_markdown") or "").strip(),
            "execution_spec_json": self._encode_json_value(record.get("execution_spec_json") or {}),
            "created_at": str(record.get("created_at") or utc_now_z()),
            "confirmed_at": str(record.get("confirmed_at") or "").strip() or None,
            "superseded_at": str(record.get("superseded_at") or "").strip() or None,
        }
        if not payload["task_id"] or payload["plan_version"] <= 0 or not payload["status"] or not payload["review_markdown"]:
            return False
        result = self._request(
            """
            INSERT INTO ai_search_plans (
                task_id, plan_version, status, review_markdown, execution_spec_json,
                created_at, confirmed_at, superseded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                payload["task_id"],
                payload["plan_version"],
                payload["status"],
                payload["review_markdown"],
                payload["execution_spec_json"],
                payload["created_at"],
                payload["confirmed_at"],
                payload["superseded_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def update_ai_search_plan(self, task_id: str, plan_version: int, **kwargs) -> bool:
        allowed_fields = {
            "status",
            "review_markdown",
            "execution_spec_json",
            "confirmed_at",
            "superseded_at",
        }
        updates = {key: kwargs[key] for key in kwargs if key in allowed_fields}
        if not updates:
            return False
        for key in list(updates.keys()):
            if key.endswith("_json"):
                updates[key] = self._encode_json_value(updates[key])
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        result = self._request(
            f"UPDATE ai_search_plans SET {set_clause} WHERE task_id = ? AND plan_version = ?",
            [*updates.values(), task_id, int(plan_version)],
        )
        return self._changed_rows(result) > 0

    def get_ai_search_plan(self, task_id: str, plan_version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        if plan_version is None:
            row = self._fetchone(
                """
                SELECT *
                FROM ai_search_plans
                WHERE task_id = ?
                ORDER BY plan_version DESC
                LIMIT 1
                """,
                [task_id],
            )
        else:
            row = self._fetchone(
                "SELECT * FROM ai_search_plans WHERE task_id = ? AND plan_version = ?",
                [task_id, int(plan_version)],
            )
        return self._row_to_ai_search_plan(row) if row else None

    def list_ai_search_plans(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT *
            FROM ai_search_plans
            WHERE task_id = ?
            ORDER BY plan_version DESC
            """,
            [task_id],
        )
        return [self._row_to_ai_search_plan(row) for row in rows]

    def _row_to_ai_search_run(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "run_id": row.get("run_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "phase": row.get("phase"),
            "status": row.get("status"),
            "active_retrieval_todo_id": row.get("active_retrieval_todo_id"),
            "active_batch_id": row.get("active_batch_id"),
            "selected_document_count": int(row.get("selected_document_count") or 0),
            "human_decision_state": self._parse_metadata(row.get("human_decision_state")),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "completed_at": row.get("completed_at"),
        }

    def create_ai_search_run(self, record: Dict[str, Any]) -> bool:
        payload = {
            "run_id": str(record.get("run_id") or "").strip(),
            "task_id": str(record.get("task_id") or "").strip(),
            "plan_version": int(record.get("plan_version") or 0),
            "phase": str(record.get("phase") or "").strip(),
            "status": str(record.get("status") or "").strip(),
            "active_retrieval_todo_id": str(record.get("active_retrieval_todo_id") or "").strip() or None,
            "active_batch_id": str(record.get("active_batch_id") or "").strip() or None,
            "selected_document_count": int(record.get("selected_document_count") or 0),
            "human_decision_state": self._encode_json_value(record.get("human_decision_state") or {}),
            "created_at": str(record.get("created_at") or utc_now_z()),
            "updated_at": str(record.get("updated_at") or utc_now_z()),
            "completed_at": record.get("completed_at"),
        }
        if not payload["run_id"] or not payload["task_id"] or payload["plan_version"] <= 0 or not payload["phase"] or not payload["status"]:
            return False
        result = self._request(
            """
            INSERT INTO ai_search_runs (
                run_id, task_id, plan_version, phase, status,
                active_retrieval_todo_id, active_batch_id, selected_document_count,
                human_decision_state, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                payload["run_id"],
                payload["task_id"],
                payload["plan_version"],
                payload["phase"],
                payload["status"],
                payload["active_retrieval_todo_id"],
                payload["active_batch_id"],
                payload["selected_document_count"],
                payload["human_decision_state"],
                payload["created_at"],
                payload["updated_at"],
                payload["completed_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def get_ai_search_run(self, task_id: str, run_id: Optional[str] = None, *, plan_version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        if run_id:
            row = self._fetchone(
                "SELECT * FROM ai_search_runs WHERE task_id = ? AND run_id = ? LIMIT 1",
                [task_id, str(run_id)],
            )
        elif plan_version is not None:
            row = self._fetchone(
                """
                SELECT *
                FROM ai_search_runs
                WHERE task_id = ? AND plan_version = ?
                ORDER BY updated_at DESC, run_id DESC
                LIMIT 1
                """,
                [task_id, int(plan_version)],
            )
        else:
            row = self._fetchone(
                """
                SELECT *
                FROM ai_search_runs
                WHERE task_id = ?
                ORDER BY updated_at DESC, run_id DESC
                LIMIT 1
                """,
                [task_id],
            )
        return self._row_to_ai_search_run(row) if row else None

    def list_ai_search_runs(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT *
            FROM ai_search_runs
            WHERE task_id = ?
            ORDER BY updated_at DESC, run_id DESC
            """,
            [task_id],
        )
        return [self._row_to_ai_search_run(row) for row in rows]

    def update_ai_search_run(self, task_id: str, run_id: str, **kwargs) -> bool:
        allowed_fields = {
            "phase",
            "status",
            "active_retrieval_todo_id",
            "active_batch_id",
            "selected_document_count",
            "human_decision_state",
            "updated_at",
            "completed_at",
        }
        updates = {key: kwargs[key] for key in kwargs if key in allowed_fields}
        if not updates:
            return False
        updates.setdefault("updated_at", utc_now_z())
        if "human_decision_state" in updates:
            updates["human_decision_state"] = self._encode_json_value(updates["human_decision_state"] or {})
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        result = self._request(
            f"UPDATE ai_search_runs SET {set_clause} WHERE task_id = ? AND run_id = ?",
            [*updates.values(), task_id, str(run_id)],
        )
        return self._changed_rows(result) > 0

    def _row_to_ai_search_retrieval_todo(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "todo_id": row.get("todo_id"),
            "run_id": row.get("run_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "sub_plan_id": row.get("sub_plan_id"),
            "step_id": row.get("step_id"),
            "title": row.get("title"),
            "description": row.get("description"),
            "status": row.get("status"),
            "attempt_count": int(row.get("attempt_count") or 0),
            "last_error": row.get("last_error") or "",
            "resume_from": row.get("resume_from") or "",
            "state": self._parse_metadata(row.get("state_json")),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def replace_ai_search_retrieval_todos(self, run_id: str, task_id: str, plan_version: int, todos: List[Dict[str, Any]]) -> int:
        now = utc_now_z()
        self._request("DELETE FROM ai_search_retrieval_todos WHERE run_id = ?", [run_id])
        changed = 0
        for item in todos:
            todo_id = str(item.get("todo_id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not todo_id or not title:
                continue
            result = self._request(
                """
                INSERT INTO ai_search_retrieval_todos (
                    todo_id, run_id, task_id, plan_version, sub_plan_id, step_id,
                    title, description, status, attempt_count, last_error, resume_from,
                    state_json, started_at, completed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    todo_id,
                    run_id,
                    task_id,
                    int(plan_version),
                    str(item.get("sub_plan_id") or "").strip() or None,
                    str(item.get("step_id") or "").strip() or None,
                    title,
                    str(item.get("description") or "").strip(),
                    str(item.get("status") or "pending").strip() or "pending",
                    int(item.get("attempt_count") or 0),
                    str(item.get("last_error") or "").strip() or None,
                    str(item.get("resume_from") or "").strip() or None,
                    self._encode_json_value(item.get("state") or {}),
                    item.get("started_at"),
                    item.get("completed_at"),
                    str(item.get("created_at") or now),
                    str(item.get("updated_at") or now),
                ],
            )
            changed += self._changed_rows(result)
        return changed

    def list_ai_search_retrieval_todos(self, run_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT *
            FROM ai_search_retrieval_todos
            WHERE run_id = ?
            ORDER BY created_at ASC, todo_id ASC
            """,
            [run_id],
        )
        return [self._row_to_ai_search_retrieval_todo(row) for row in rows]

    def get_ai_search_retrieval_todo(self, run_id: str, todo_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT *
            FROM ai_search_retrieval_todos
            WHERE run_id = ? AND todo_id = ?
            LIMIT 1
            """,
            [run_id, todo_id],
        )
        return self._row_to_ai_search_retrieval_todo(row) if row else None

    def update_ai_search_retrieval_todo(self, run_id: str, todo_id: str, **kwargs) -> bool:
        allowed_fields = {
            "status",
            "attempt_count",
            "last_error",
            "resume_from",
            "state_json",
            "started_at",
            "completed_at",
            "updated_at",
        }
        updates: Dict[str, Any] = {}
        for key, value in kwargs.items():
            if key not in allowed_fields:
                continue
            updates[key] = self._encode_json_value(value or {}) if key == "state_json" else value
        if not updates:
            return False
        updates.setdefault("updated_at", utc_now_z())
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        result = self._request(
            f"UPDATE ai_search_retrieval_todos SET {set_clause} WHERE run_id = ? AND todo_id = ?",
            [*updates.values(), run_id, todo_id],
        )
        return self._changed_rows(result) > 0

    def _row_to_ai_search_execution_summary(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "summary_id": row.get("summary_id"),
            "run_id": row.get("run_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "todo_id": row.get("todo_id"),
            "step_id": row.get("step_id"),
            "sub_plan_id": row.get("sub_plan_id"),
            "result_summary": row.get("result_summary") or "",
            "adjustments": self._parse_metadata(row.get("adjustments_json")),
            "plan_change_assessment": self._parse_metadata(row.get("plan_change_assessment_json")),
            "next_recommendation": row.get("next_recommendation") or "",
            "candidate_pool_size": int(row.get("candidate_pool_size") or 0),
            "new_unique_candidates": int(row.get("new_unique_candidates") or 0),
            "metadata": self._parse_metadata(row.get("metadata_json")),
            "created_at": row.get("created_at"),
        }

    def create_ai_search_execution_summary(self, record: Dict[str, Any]) -> bool:
        payload = {
            "summary_id": str(record.get("summary_id") or "").strip(),
            "run_id": str(record.get("run_id") or "").strip(),
            "task_id": str(record.get("task_id") or "").strip(),
            "plan_version": int(record.get("plan_version") or 0),
            "todo_id": str(record.get("todo_id") or "").strip(),
            "step_id": str(record.get("step_id") or "").strip() or None,
            "sub_plan_id": str(record.get("sub_plan_id") or "").strip() or None,
            "result_summary": str(record.get("result_summary") or "").strip() or None,
            "adjustments_json": self._encode_json_value(record.get("adjustments") or []),
            "plan_change_assessment_json": self._encode_json_value(record.get("plan_change_assessment") or {}),
            "next_recommendation": str(record.get("next_recommendation") or "").strip() or None,
            "candidate_pool_size": int(record.get("candidate_pool_size") or 0),
            "new_unique_candidates": int(record.get("new_unique_candidates") or 0),
            "metadata_json": self._encode_json_value(record.get("metadata") or {}),
            "created_at": str(record.get("created_at") or utc_now_z()),
        }
        if not payload["summary_id"] or not payload["run_id"] or not payload["task_id"] or payload["plan_version"] <= 0 or not payload["todo_id"]:
            return False
        result = self._request(
            """
            INSERT INTO ai_search_execution_summaries (
                summary_id, run_id, task_id, plan_version, todo_id, step_id, sub_plan_id,
                result_summary, adjustments_json, plan_change_assessment_json, next_recommendation,
                candidate_pool_size, new_unique_candidates, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                payload["summary_id"],
                payload["run_id"],
                payload["task_id"],
                payload["plan_version"],
                payload["todo_id"],
                payload["step_id"],
                payload["sub_plan_id"],
                payload["result_summary"],
                payload["adjustments_json"],
                payload["plan_change_assessment_json"],
                payload["next_recommendation"],
                payload["candidate_pool_size"],
                payload["new_unique_candidates"],
                payload["metadata_json"],
                payload["created_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def list_ai_search_execution_summaries(self, run_id: str, *, sub_plan_id: str = "") -> List[Dict[str, Any]]:
        where = ["run_id = ?"]
        params: List[Any] = [run_id]
        if sub_plan_id:
            where.append("sub_plan_id = ?")
            params.append(sub_plan_id)
        rows = self._fetchall(
            f"""
            SELECT *
            FROM ai_search_execution_summaries
            WHERE {' AND '.join(where)}
            ORDER BY created_at ASC, summary_id ASC
            """,
            params,
        )
        return [self._row_to_ai_search_execution_summary(row) for row in rows]

    def _row_to_ai_search_batch(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "batch_id": row.get("batch_id"),
            "run_id": row.get("run_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "batch_type": row.get("batch_type"),
            "status": row.get("status"),
            "workspace_dir": row.get("workspace_dir"),
            "input_hash": row.get("input_hash"),
            "loaded_at": row.get("loaded_at"),
            "committed_at": row.get("committed_at"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def create_ai_search_batch(self, record: Dict[str, Any]) -> bool:
        payload = {
            "batch_id": str(record.get("batch_id") or "").strip(),
            "run_id": str(record.get("run_id") or "").strip(),
            "task_id": str(record.get("task_id") or "").strip(),
            "plan_version": int(record.get("plan_version") or 0),
            "batch_type": str(record.get("batch_type") or "").strip(),
            "status": str(record.get("status") or "").strip(),
            "workspace_dir": str(record.get("workspace_dir") or "").strip() or None,
            "input_hash": str(record.get("input_hash") or "").strip() or None,
            "loaded_at": str(record.get("loaded_at") or utc_now_z()),
            "committed_at": record.get("committed_at"),
            "created_at": str(record.get("created_at") or utc_now_z()),
            "updated_at": str(record.get("updated_at") or utc_now_z()),
        }
        if not payload["batch_id"] or not payload["run_id"] or not payload["task_id"] or payload["plan_version"] <= 0 or not payload["batch_type"] or not payload["status"]:
            return False
        result = self._request(
            """
            INSERT INTO ai_search_batches (
                batch_id, run_id, task_id, plan_version, batch_type, status,
                workspace_dir, input_hash, loaded_at, committed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                payload["batch_id"],
                payload["run_id"],
                payload["task_id"],
                payload["plan_version"],
                payload["batch_type"],
                payload["status"],
                payload["workspace_dir"],
                payload["input_hash"],
                payload["loaded_at"],
                payload["committed_at"],
                payload["created_at"],
                payload["updated_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def get_ai_search_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone("SELECT * FROM ai_search_batches WHERE batch_id = ? LIMIT 1", [batch_id])
        return self._row_to_ai_search_batch(row) if row else None

    def get_latest_ai_search_batch(self, run_id: str, batch_type: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT *
            FROM ai_search_batches
            WHERE run_id = ? AND batch_type = ?
            ORDER BY created_at DESC, batch_id DESC
            LIMIT 1
            """,
            [run_id, batch_type],
        )
        return self._row_to_ai_search_batch(row) if row else None

    def update_ai_search_batch(self, batch_id: str, **kwargs) -> bool:
        allowed_fields = {"status", "workspace_dir", "input_hash", "loaded_at", "committed_at", "updated_at"}
        updates = {key: kwargs[key] for key in kwargs if key in allowed_fields}
        if not updates:
            return False
        updates.setdefault("updated_at", utc_now_z())
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        result = self._request(
            f"UPDATE ai_search_batches SET {set_clause} WHERE batch_id = ?",
            [*updates.values(), batch_id],
        )
        return self._changed_rows(result) > 0

    def replace_ai_search_batch_documents(self, batch_id: str, run_id: str, document_ids: List[str]) -> int:
        self._request("DELETE FROM ai_search_batch_documents WHERE batch_id = ?", [batch_id])
        changed = 0
        for index, document_id in enumerate(document_ids, start=1):
            doc_id = str(document_id or "").strip()
            if not doc_id:
                continue
            result = self._request(
                """
                INSERT INTO ai_search_batch_documents (batch_id, run_id, document_id, ord)
                VALUES (?, ?, ?, ?)
                """,
                [batch_id, run_id, doc_id, index],
            )
            changed += self._changed_rows(result)
        return changed

    def list_ai_search_batch_documents(self, batch_id: str) -> List[str]:
        rows = self._fetchall(
            """
            SELECT document_id
            FROM ai_search_batch_documents
            WHERE batch_id = ?
            ORDER BY ord ASC, document_id ASC
            """,
            [batch_id],
        )
        return [str(row.get("document_id") or "").strip() for row in rows if str(row.get("document_id") or "").strip()]

    def _row_to_ai_search_pending_action(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "action_id": row.get("action_id"),
            "task_id": row.get("task_id"),
            "run_id": row.get("run_id"),
            "action_type": row.get("action_type"),
            "status": row.get("status"),
            "payload": self._parse_metadata(row.get("payload_json")),
            "created_at": row.get("created_at"),
            "resolved_at": row.get("resolved_at"),
        }

    def create_ai_search_pending_action(self, record: Dict[str, Any]) -> bool:
        payload = {
            "action_id": str(record.get("action_id") or "").strip(),
            "task_id": str(record.get("task_id") or "").strip(),
            "run_id": str(record.get("run_id") or "").strip() or None,
            "action_type": str(record.get("action_type") or "").strip(),
            "status": str(record.get("status") or "pending").strip() or "pending",
            "payload_json": self._encode_json_value(record.get("payload") or {}),
            "created_at": str(record.get("created_at") or utc_now_z()),
            "resolved_at": record.get("resolved_at"),
        }
        if not payload["action_id"] or not payload["task_id"] or not payload["action_type"]:
            return False
        result = self._request(
            """
            INSERT INTO ai_search_pending_actions (
                action_id, task_id, run_id, action_type, status, payload_json, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                payload["action_id"],
                payload["task_id"],
                payload["run_id"],
                payload["action_type"],
                payload["status"],
                payload["payload_json"],
                payload["created_at"],
                payload["resolved_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def get_ai_search_pending_action(self, task_id: str, action_type: str, *, status: str = "pending") -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT *
            FROM ai_search_pending_actions
            WHERE task_id = ? AND action_type = ? AND status = ?
            ORDER BY created_at DESC, action_id DESC
            LIMIT 1
            """,
            [task_id, action_type, status],
        )
        return self._row_to_ai_search_pending_action(row) if row else None

    def resolve_ai_search_pending_action(self, action_id: str, *, status: str = "resolved") -> bool:
        result = self._request(
            """
            UPDATE ai_search_pending_actions
            SET status = ?, resolved_at = ?
            WHERE action_id = ?
            """,
            [status, utc_now_z(), action_id],
        )
        return self._changed_rows(result) > 0

    def _row_to_ai_search_document(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "run_id": row.get("run_id"),
            "document_id": row.get("document_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "pn": row.get("pn"),
            "title": row.get("title"),
            "abstract": row.get("abstract"),
            "publication_date": row.get("publication_date"),
            "application_date": row.get("application_date"),
            "primary_ipc": row.get("primary_ipc"),
            "document_type": row.get("document_type"),
            "claim_ids_json": self._parse_metadata(row.get("claim_ids_json")),
            "evidence_locations_json": self._parse_metadata(row.get("evidence_locations_json")),
            "evidence_summary": row.get("evidence_summary"),
            "report_row_order": int(row["report_row_order"]) if row.get("report_row_order") is not None else None,
            "ipc_cpc_json": self._parse_metadata(row.get("ipc_cpc_json")),
            "source_batches_json": self._parse_metadata(row.get("source_batches_json")),
            "source_lanes_json": self._parse_metadata(row.get("source_lanes_json")),
            "source_sub_plans_json": self._parse_metadata(row.get("source_sub_plans_json")),
            "source_steps_json": self._parse_metadata(row.get("source_steps_json")),
            "stage": row.get("stage"),
            "score": float(row["score"]) if row.get("score") is not None else None,
            "agent_reason": row.get("agent_reason"),
            "key_passages_json": self._parse_metadata(row.get("key_passages_json")),
            "user_pinned": bool(int(row.get("user_pinned") or 0)),
            "user_removed": bool(int(row.get("user_removed") or 0)),
            "coarse_status": row.get("coarse_status") or "pending",
            "coarse_reason": row.get("coarse_reason"),
            "coarse_screened_at": row.get("coarse_screened_at"),
            "close_read_status": row.get("close_read_status") or "pending",
            "close_read_reason": row.get("close_read_reason"),
            "close_read_at": row.get("close_read_at"),
            "detail_fingerprint": row.get("detail_fingerprint"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def _resolve_ai_search_run_id(self, task_id: str, plan_version_or_run_id: Any) -> Optional[str]:
        if isinstance(plan_version_or_run_id, str) and not str(plan_version_or_run_id).isdigit():
            return str(plan_version_or_run_id).strip() or None
        if isinstance(plan_version_or_run_id, str) and str(plan_version_or_run_id).strip():
            try:
                plan_version_or_run_id = int(str(plan_version_or_run_id).strip())
            except Exception:
                return str(plan_version_or_run_id).strip() or None
        if isinstance(plan_version_or_run_id, int):
            run = self.get_ai_search_run(task_id, plan_version=int(plan_version_or_run_id))
            return str(run.get("run_id") or "").strip() if run else None
        return None

    def upsert_ai_search_documents(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 0
        changed = 0
        now = utc_now_z()
        for record in records:
            document_id = str(record.get("document_id") or "").strip()
            run_id = str(record.get("run_id") or "").strip()
            task_id = str(record.get("task_id") or "").strip()
            plan_version = int(record.get("plan_version") or 0)
            stage = str(record.get("stage") or "").strip()
            if not run_id and task_id and plan_version > 0:
                run_id = str(self._resolve_ai_search_run_id(task_id, plan_version) or "").strip()
            if not document_id or not run_id or not task_id or plan_version <= 0 or not stage:
                continue
            result = self._request(
                """
                INSERT INTO ai_search_documents (
                    run_id, document_id, task_id, plan_version, pn, title, abstract,
                    publication_date, application_date, primary_ipc, document_type, claim_ids_json, evidence_locations_json,
                    evidence_summary, report_row_order,
                    ipc_cpc_json, source_batches_json, source_lanes_json, source_sub_plans_json, source_steps_json, stage, score, agent_reason,
                    key_passages_json, user_pinned, user_removed, coarse_status, coarse_reason,
                    coarse_screened_at, close_read_status, close_read_reason, close_read_at,
                    detail_fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, document_id) DO UPDATE SET
                    pn = excluded.pn,
                    title = excluded.title,
                    abstract = excluded.abstract,
                    publication_date = excluded.publication_date,
                    application_date = excluded.application_date,
                    primary_ipc = excluded.primary_ipc,
                    document_type = excluded.document_type,
                    claim_ids_json = excluded.claim_ids_json,
                    evidence_locations_json = excluded.evidence_locations_json,
                    evidence_summary = excluded.evidence_summary,
                    report_row_order = excluded.report_row_order,
                    ipc_cpc_json = excluded.ipc_cpc_json,
                    source_batches_json = excluded.source_batches_json,
                    source_lanes_json = excluded.source_lanes_json,
                    source_sub_plans_json = excluded.source_sub_plans_json,
                    source_steps_json = excluded.source_steps_json,
                    stage = excluded.stage,
                    score = excluded.score,
                    agent_reason = excluded.agent_reason,
                    key_passages_json = excluded.key_passages_json,
                    user_pinned = excluded.user_pinned,
                    user_removed = excluded.user_removed,
                    coarse_status = excluded.coarse_status,
                    coarse_reason = excluded.coarse_reason,
                    coarse_screened_at = excluded.coarse_screened_at,
                    close_read_status = excluded.close_read_status,
                    close_read_reason = excluded.close_read_reason,
                    close_read_at = excluded.close_read_at,
                    detail_fingerprint = excluded.detail_fingerprint,
                    created_at = COALESCE(ai_search_documents.created_at, excluded.created_at),
                    updated_at = excluded.updated_at
                """,
                [
                    run_id,
                    document_id,
                    task_id,
                    plan_version,
                    record.get("pn"),
                    record.get("title"),
                    record.get("abstract"),
                    record.get("publication_date"),
                    record.get("application_date"),
                    record.get("primary_ipc"),
                    record.get("document_type"),
                    self._encode_json_value(record.get("claim_ids_json") or []),
                    self._encode_json_value(record.get("evidence_locations_json") or []),
                    record.get("evidence_summary"),
                    record.get("report_row_order"),
                    self._encode_json_value(record.get("ipc_cpc_json") or []),
                    self._encode_json_value(record.get("source_batches_json") or []),
                    self._encode_json_value(record.get("source_lanes_json") or []),
                    self._encode_json_value(record.get("source_sub_plans_json") or []),
                    self._encode_json_value(record.get("source_steps_json") or []),
                    stage,
                    record.get("score"),
                    record.get("agent_reason"),
                    self._encode_json_value(record.get("key_passages_json") or []),
                    1 if record.get("user_pinned") else 0,
                    1 if record.get("user_removed") else 0,
                    str(record.get("coarse_status") or "pending"),
                    record.get("coarse_reason"),
                    record.get("coarse_screened_at"),
                    str(record.get("close_read_status") or "pending"),
                    record.get("close_read_reason"),
                    record.get("close_read_at"),
                    record.get("detail_fingerprint"),
                    str(record.get("created_at") or now),
                    str(record.get("updated_at") or now),
                ],
            )
            changed += self._changed_rows(result)
        return changed

    def list_ai_search_documents(
        self,
        task_id: str,
        plan_version: Any,
        stages: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        run_id = self._resolve_ai_search_run_id(task_id, plan_version)
        if not run_id:
            return []
        where = ["task_id = ?", "run_id = ?"]
        params: List[Any] = [task_id, run_id]
        if stages:
            placeholders = ", ".join("?" for _ in stages)
            where.append(f"stage IN ({placeholders})")
            params.extend(stages)
        rows = self._fetchall(
            f"""
            SELECT *
            FROM ai_search_documents
            WHERE {' AND '.join(where)}
            ORDER BY
                CASE stage
                    WHEN 'selected' THEN 0
                    WHEN 'shortlisted' THEN 1
                    WHEN 'candidate' THEN 2
                    WHEN 'rejected' THEN 3
                    ELSE 9
                END,
                COALESCE(score, 0) DESC,
                updated_at DESC
            """,
            params,
        )
        return [self._row_to_ai_search_document(row) for row in rows]

    def update_ai_search_document(self, task_id: str, plan_version: int, document_id: str, **kwargs) -> bool:
        run_id = self._resolve_ai_search_run_id(task_id, plan_version)
        if not run_id:
            return False
        allowed_fields = {
            "stage",
            "score",
            "agent_reason",
            "key_passages_json",
            "publication_date",
            "application_date",
            "primary_ipc",
            "document_type",
            "claim_ids_json",
            "evidence_locations_json",
            "evidence_summary",
            "report_row_order",
            "user_pinned",
            "user_removed",
            "title",
            "abstract",
            "source_batches_json",
            "source_lanes_json",
            "source_sub_plans_json",
            "source_steps_json",
            "ipc_cpc_json",
            "coarse_status",
            "coarse_reason",
            "coarse_screened_at",
            "close_read_status",
            "close_read_reason",
            "close_read_at",
            "detail_fingerprint",
            "updated_at",
        }
        updates = {key: kwargs[key] for key in kwargs if key in allowed_fields}
        if not updates:
            return False
        updates.setdefault("updated_at", utc_now_z())
        for key in list(updates.keys()):
            if key.endswith("_json"):
                updates[key] = self._encode_json_value(updates[key])
            elif key in {"user_pinned", "user_removed"}:
                updates[key] = 1 if updates[key] else 0
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        result = self._request(
            f"""
            UPDATE ai_search_documents
            SET {set_clause}
            WHERE task_id = ? AND run_id = ? AND document_id = ?
            """,
            [*updates.values(), task_id, run_id, document_id],
        )
        return self._changed_rows(result) > 0

    def create_ai_search_document_decision(self, record: Dict[str, Any]) -> bool:
        payload = {
            "decision_id": str(record.get("decision_id") or "").strip(),
            "run_id": str(record.get("run_id") or "").strip(),
            "batch_id": str(record.get("batch_id") or "").strip() or None,
            "task_id": str(record.get("task_id") or "").strip(),
            "plan_version": int(record.get("plan_version") or 0),
            "document_id": str(record.get("document_id") or "").strip(),
            "decision_stage": str(record.get("decision_stage") or "").strip(),
            "decision": str(record.get("decision") or "").strip(),
            "reason": str(record.get("reason") or "").strip() or None,
            "metadata_json": self._encode_json_value(record.get("metadata") or {}),
            "created_at": str(record.get("created_at") or utc_now_z()),
        }
        if not payload["decision_id"] or not payload["run_id"] or not payload["task_id"] or payload["plan_version"] <= 0 or not payload["document_id"] or not payload["decision_stage"] or not payload["decision"]:
            return False
        result = self._request(
            """
            INSERT INTO ai_search_document_decisions (
                decision_id, run_id, batch_id, task_id, plan_version, document_id,
                decision_stage, decision, reason, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                payload["decision_id"],
                payload["run_id"],
                payload["batch_id"],
                payload["task_id"],
                payload["plan_version"],
                payload["document_id"],
                payload["decision_stage"],
                payload["decision"],
                payload["reason"],
                payload["metadata_json"],
                payload["created_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def list_ai_search_document_decisions(self, run_id: str, *, decision_stage: str = "") -> List[Dict[str, Any]]:
        where = ["run_id = ?"]
        params: List[Any] = [run_id]
        if decision_stage:
            where.append("decision_stage = ?")
            params.append(decision_stage)
        rows = self._fetchall(
            f"""
            SELECT *
            FROM ai_search_document_decisions
            WHERE {' AND '.join(where)}
            ORDER BY created_at ASC, decision_id ASC
            """,
            params,
        )
        return [
            {
                "decision_id": row.get("decision_id"),
                "run_id": row.get("run_id"),
                "batch_id": row.get("batch_id"),
                "task_id": row.get("task_id"),
                "plan_version": int(row["plan_version"]),
                "document_id": row.get("document_id"),
                "decision_stage": row.get("decision_stage"),
                "decision": row.get("decision"),
                "reason": row.get("reason"),
                "metadata": self._parse_metadata(row.get("metadata_json")),
                "created_at": row.get("created_at"),
            }
            for row in rows
        ]

    def _row_to_ai_search_feature_comparison(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "feature_comparison_id": row.get("result_id"),
            "result_id": row.get("result_id"),
            "run_id": row.get("run_id"),
            "batch_id": row.get("batch_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "status": "completed",
            "table_json": self._parse_metadata(row.get("table_rows_json")),
            "table_rows": self._parse_metadata(row.get("table_rows_json")),
            "summary_markdown": row.get("summary_markdown"),
            "overall_findings": row.get("overall_findings"),
            "coverage_gaps": self._parse_metadata(row.get("coverage_gaps_json")),
            "difference_highlights": self._parse_metadata(row.get("difference_highlights_json")),
            "follow_up_search_hints": self._parse_metadata(row.get("follow_up_search_hints_json")),
            "creativity_readiness": row.get("creativity_readiness"),
            "readiness_rationale": row.get("readiness_rationale"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def create_ai_search_feature_comparison(self, record: Dict[str, Any]) -> bool:
        payload = {
            "result_id": str(record.get("feature_comparison_id") or record.get("result_id") or "").strip(),
            "run_id": str(record.get("run_id") or "").strip(),
            "batch_id": str(record.get("batch_id") or "").strip(),
            "task_id": str(record.get("task_id") or "").strip(),
            "plan_version": int(record.get("plan_version") or 0),
            "table_rows_json": self._encode_json_value(record.get("table_rows") or record.get("table_json") or []),
            "summary_markdown": record.get("summary_markdown"),
            "overall_findings": record.get("overall_findings"),
            "coverage_gaps_json": self._encode_json_value(record.get("coverage_gaps") or []),
            "difference_highlights_json": self._encode_json_value(record.get("difference_highlights") or []),
            "follow_up_search_hints_json": self._encode_json_value(record.get("follow_up_search_hints") or []),
            "creativity_readiness": record.get("creativity_readiness"),
            "readiness_rationale": record.get("readiness_rationale"),
            "created_at": str(record.get("created_at") or utc_now_z()),
            "updated_at": str(record.get("updated_at") or utc_now_z()),
        }
        if not payload["result_id"] or not payload["run_id"] or not payload["batch_id"] or not payload["task_id"] or payload["plan_version"] <= 0:
            return False
        result = self._request(
            """
            INSERT INTO ai_search_feature_compare_results (
                result_id, run_id, batch_id, task_id, plan_version, table_rows_json,
                summary_markdown, overall_findings, coverage_gaps_json, difference_highlights_json,
                follow_up_search_hints_json, creativity_readiness, readiness_rationale, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                payload["result_id"],
                payload["run_id"],
                payload["batch_id"],
                payload["task_id"],
                payload["plan_version"],
                payload["table_rows_json"],
                payload["summary_markdown"],
                payload["overall_findings"],
                payload["coverage_gaps_json"],
                payload["difference_highlights_json"],
                payload["follow_up_search_hints_json"],
                payload["creativity_readiness"],
                payload["readiness_rationale"],
                payload["created_at"],
                payload["updated_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def list_ai_search_feature_comparisons(self, task_id: str, plan_version: int) -> List[Dict[str, Any]]:
        run_id = self._resolve_ai_search_run_id(task_id, plan_version)
        if not run_id:
            return []
        rows = self._fetchall(
            """
            SELECT *
            FROM ai_search_feature_compare_results
            WHERE task_id = ? AND run_id = ?
            ORDER BY updated_at DESC, result_id DESC
            """,
            [task_id, run_id],
        )
        return [self._row_to_ai_search_feature_comparison(row) for row in rows]

    def get_ai_search_feature_comparison(
        self,
        task_id: str,
        plan_version: Any,
        feature_comparison_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        run_id = self._resolve_ai_search_run_id(task_id, plan_version)
        if not run_id:
            return None
        if feature_comparison_id:
            row = self._fetchone(
                """
                SELECT *
                FROM ai_search_feature_compare_results
                WHERE task_id = ? AND run_id = ? AND result_id = ?
                LIMIT 1
                """,
                [task_id, run_id, feature_comparison_id],
            )
        else:
            row = self._fetchone(
                """
                SELECT *
                FROM ai_search_feature_compare_results
                WHERE task_id = ? AND run_id = ?
                ORDER BY updated_at DESC, result_id DESC
                LIMIT 1
                """,
                [task_id, run_id],
            )
        return self._row_to_ai_search_feature_comparison(row) if row else None

    def create_ai_search_close_read_result(self, record: Dict[str, Any]) -> bool:
        payload = {
            "result_id": str(record.get("result_id") or "").strip(),
            "run_id": str(record.get("run_id") or "").strip(),
            "batch_id": str(record.get("batch_id") or "").strip(),
            "task_id": str(record.get("task_id") or "").strip(),
            "plan_version": int(record.get("plan_version") or 0),
            "coverage_summary": record.get("coverage_summary"),
            "selection_summary": record.get("selection_summary"),
            "follow_up_hints_json": self._encode_json_value(record.get("follow_up_hints") or []),
            "document_assessments_json": self._encode_json_value(record.get("document_assessments") or []),
            "key_passages_json": self._encode_json_value(record.get("key_passages") or []),
            "claim_alignments_json": self._encode_json_value(record.get("claim_alignments") or []),
            "limitation_coverage_json": self._encode_json_value(record.get("limitation_coverage") or []),
            "limitation_gaps_json": self._encode_json_value(record.get("limitation_gaps") or []),
            "created_at": str(record.get("created_at") or utc_now_z()),
        }
        if not payload["result_id"] or not payload["run_id"] or not payload["batch_id"] or not payload["task_id"] or payload["plan_version"] <= 0:
            return False
        result = self._request(
            """
            INSERT INTO ai_search_close_read_results (
                result_id, run_id, batch_id, task_id, plan_version, coverage_summary, selection_summary,
                follow_up_hints_json, document_assessments_json, key_passages_json, claim_alignments_json,
                limitation_coverage_json, limitation_gaps_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                payload["result_id"],
                payload["run_id"],
                payload["batch_id"],
                payload["task_id"],
                payload["plan_version"],
                payload["coverage_summary"],
                payload["selection_summary"],
                payload["follow_up_hints_json"],
                payload["document_assessments_json"],
                payload["key_passages_json"],
                payload["claim_alignments_json"],
                payload["limitation_coverage_json"],
                payload["limitation_gaps_json"],
                payload["created_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def get_latest_ai_search_close_read_result(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT *
            FROM ai_search_close_read_results
            WHERE run_id = ?
            ORDER BY created_at DESC, result_id DESC
            LIMIT 1
            """,
            [run_id],
        )
        if not row:
            return None
        return {
            "result_id": row.get("result_id"),
            "run_id": row.get("run_id"),
            "batch_id": row.get("batch_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "coverage_summary": row.get("coverage_summary"),
            "selection_summary": row.get("selection_summary"),
            "follow_up_hints": self._parse_metadata(row.get("follow_up_hints_json")),
            "document_assessments": self._parse_metadata(row.get("document_assessments_json")),
            "key_passages": self._parse_metadata(row.get("key_passages_json")),
            "claim_alignments": self._parse_metadata(row.get("claim_alignments_json")),
            "limitation_coverage": self._parse_metadata(row.get("limitation_coverage_json")),
            "limitation_gaps": self._parse_metadata(row.get("limitation_gaps_json")),
            "created_at": row.get("created_at"),
        }

    def _row_to_ai_search_checkpoint(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "thread_id": row.get("thread_id"),
            "checkpoint_ns": row.get("checkpoint_ns"),
            "checkpoint_id": row.get("checkpoint_id"),
            "checkpoint_json": row.get("checkpoint_json"),
            "metadata_json": row.get("metadata_json"),
            "parent_checkpoint_id": row.get("parent_checkpoint_id"),
            "created_at": row.get("created_at"),
        }

    def put_ai_search_checkpoint(self, record: Dict[str, Any]) -> bool:
        payload = {
            "thread_id": str(record.get("thread_id", "")).strip(),
            "checkpoint_ns": str(record.get("checkpoint_ns", "")).strip(),
            "checkpoint_id": str(record.get("checkpoint_id", "")).strip(),
            "checkpoint_json": str(record.get("checkpoint_json", "")).strip(),
            "metadata_json": str(record.get("metadata_json", "")).strip(),
            "parent_checkpoint_id": str(record.get("parent_checkpoint_id", "")).strip() or None,
            "created_at": str(record.get("created_at") or utc_now_z()),
        }
        if not payload["thread_id"] or not payload["checkpoint_id"] or not payload["checkpoint_json"] or not payload["metadata_json"]:
            return False
        result = self._request(
            """
            INSERT INTO ai_search_checkpoints (
                thread_id, checkpoint_ns, checkpoint_id, checkpoint_json,
                metadata_json, parent_checkpoint_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_id, checkpoint_ns, checkpoint_id) DO UPDATE SET
                checkpoint_json = excluded.checkpoint_json,
                metadata_json = excluded.metadata_json,
                parent_checkpoint_id = excluded.parent_checkpoint_id,
                created_at = COALESCE(ai_search_checkpoints.created_at, excluded.created_at)
            """,
            [
                payload["thread_id"],
                payload["checkpoint_ns"],
                payload["checkpoint_id"],
                payload["checkpoint_json"],
                payload["metadata_json"],
                payload["parent_checkpoint_id"],
                payload["created_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def get_ai_search_checkpoint(
        self,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if checkpoint_id:
            row = self._fetchone(
                """
                SELECT *
                FROM ai_search_checkpoints
                WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                LIMIT 1
                """,
                [thread_id, checkpoint_ns, checkpoint_id],
            )
        else:
            row = self._fetchone(
                """
                SELECT *
                FROM ai_search_checkpoints
                WHERE thread_id = ? AND checkpoint_ns = ?
                ORDER BY checkpoint_id DESC
                LIMIT 1
                """,
                [thread_id, checkpoint_ns],
            )
        return self._row_to_ai_search_checkpoint(row) if row else None

    def list_ai_search_checkpoints(
        self,
        thread_id: str,
        checkpoint_ns: Optional[str] = None,
        checkpoint_id: Optional[str] = None,
        before_checkpoint_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        where = ["thread_id = ?"]
        params: List[Any] = [thread_id]
        if checkpoint_ns is not None:
            where.append("checkpoint_ns = ?")
            params.append(checkpoint_ns)
        if checkpoint_id is not None:
            where.append("checkpoint_id = ?")
            params.append(checkpoint_id)
        if before_checkpoint_id:
            where.append("checkpoint_id < ?")
            params.append(before_checkpoint_id)
        sql = f"""
            SELECT *
            FROM ai_search_checkpoints
            WHERE {' AND '.join(where)}
            ORDER BY checkpoint_id DESC
        """
        if limit is not None:
            sql += f" LIMIT {max(1, int(limit))}"
        rows = self._fetchall(sql, params)
        return [self._row_to_ai_search_checkpoint(row) for row in rows]

    def put_ai_search_checkpoint_blobs(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 0
        changed = 0
        for record in records:
            thread_id = str(record.get("thread_id", "")).strip()
            checkpoint_ns = str(record.get("checkpoint_ns", "")).strip()
            channel = str(record.get("channel", "")).strip()
            version = str(record.get("version", "")).strip()
            typed_value_json = str(record.get("typed_value_json", "")).strip()
            if not thread_id or not channel or not version or not typed_value_json:
                continue
            result = self._request(
                """
                INSERT INTO ai_search_checkpoint_blobs (
                    thread_id, checkpoint_ns, channel, version, typed_value_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(thread_id, checkpoint_ns, channel, version) DO UPDATE SET
                    typed_value_json = excluded.typed_value_json
                """,
                [thread_id, checkpoint_ns, channel, version, typed_value_json],
            )
            changed += self._changed_rows(result)
        return changed

    def get_ai_search_checkpoint_blobs(
        self,
        thread_id: str,
        checkpoint_ns: str,
        versions: Dict[str, Any],
    ) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for channel, version in (versions or {}).items():
            row = self._fetchone(
                """
                SELECT typed_value_json
                FROM ai_search_checkpoint_blobs
                WHERE thread_id = ? AND checkpoint_ns = ? AND channel = ? AND version = ?
                LIMIT 1
                """,
                [thread_id, checkpoint_ns, str(channel), str(version)],
            )
            if row and row.get("typed_value_json"):
                result[str(channel)] = str(row["typed_value_json"])
        return result

    def put_ai_search_checkpoint_writes(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 0
        changed = 0
        for record in records:
            thread_id = str(record.get("thread_id", "")).strip()
            checkpoint_ns = str(record.get("checkpoint_ns", "")).strip()
            checkpoint_id = str(record.get("checkpoint_id", "")).strip()
            task_id = str(record.get("task_id", "")).strip()
            channel = str(record.get("channel", "")).strip()
            typed_value_json = str(record.get("typed_value_json", "")).strip()
            write_idx = int(record.get("write_idx") or 0)
            task_path = str(record.get("task_path", "")).strip()
            if not thread_id or not checkpoint_id or not task_id or not channel or not typed_value_json:
                continue
            result = self._request(
                """
                INSERT OR IGNORE INTO ai_search_checkpoint_writes (
                    thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx,
                    channel, typed_value_json, task_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx, channel, typed_value_json, task_path],
            )
            changed += self._changed_rows(result)
        return changed

    def list_ai_search_checkpoint_writes(
        self,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT *
            FROM ai_search_checkpoint_writes
            WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
            ORDER BY task_id ASC, write_idx ASC
            """,
            [thread_id, checkpoint_ns, checkpoint_id],
        )
        return [
            {
                "thread_id": row.get("thread_id"),
                "checkpoint_ns": row.get("checkpoint_ns"),
                "checkpoint_id": row.get("checkpoint_id"),
                "task_id": row.get("task_id"),
                "write_idx": int(row.get("write_idx") or 0),
                "channel": row.get("channel"),
                "typed_value_json": row.get("typed_value_json"),
                "task_path": row.get("task_path"),
            }
            for row in rows
        ]

    def delete_ai_search_thread_checkpoints(self, thread_id: str) -> bool:
        changed = 0
        for table_name in (
            "ai_search_checkpoints",
            "ai_search_checkpoint_writes",
            "ai_search_checkpoint_blobs",
        ):
            result = self._request(f"DELETE FROM {table_name} WHERE thread_id = ?", [thread_id])
            changed += self._changed_rows(result)
        return changed > 0
