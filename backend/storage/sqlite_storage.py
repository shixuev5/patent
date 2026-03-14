"""
SQLite task storage layer implementation.
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from loguru import logger

from config import settings
from .models import AccountMonthTarget, Task, TaskStatus, TaskType, User


class SQLiteTaskStorage:
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
    """

    def __init__(self, db_path: Union[str, Path, None] = None):
        if db_path is None:
            db_path = settings.DATA_DIR / "tasks.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_database()
        logger.info(f"SQLite 任务存储初始化完成：{self.db_path}")

    def _init_database(self):
        with self._get_connection() as conn:
            conn.executescript(self.CREATE_TABLES_SQL)
            for table_name, required_columns in self.REQUIRED_COLUMNS.items():
                existing_columns = self._get_existing_columns(conn, table_name)
                for column_name, ddl in required_columns:
                    if column_name not in existing_columns:
                        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")
            patent_analysis_columns = self._get_existing_columns(conn, "patent_analyses")
            if "sha256" in patent_analysis_columns:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_patent_analyses_sha256 ON patent_analyses(sha256)"
                )
            user_columns = self._get_existing_columns(conn, "users")
            if "role" in user_columns:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
            conn.execute(
                "UPDATE tasks SET task_type = ? WHERE task_type IS NULL OR task_type = ''",
                (TaskType.PATENT_ANALYSIS.value,),
            )
            conn.commit()

    @staticmethod
    def _get_existing_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    @contextmanager
    def _get_connection(self):
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                isolation_level=None,
            )
            self._local.connection.execute("PRAGMA foreign_keys = ON")
            self._local.connection.row_factory = sqlite3.Row

        try:
            yield self._local.connection
        except Exception:
            self._local.connection.rollback()
            raise

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            owner_id=row["owner_id"] if "owner_id" in row.keys() else None,
            task_type=row["task_type"] if "task_type" in row.keys() else TaskType.PATENT_ANALYSIS.value,
            pn=row["pn"],
            title=row["title"],
            status=TaskStatus(row["status"]),
            progress=row["progress"],
            current_step=row["current_step"],
            output_dir=row["output_dir"],
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            deleted_at=datetime.fromisoformat(row["deleted_at"]) if row["deleted_at"] else None,
            metadata=self._parse_metadata(row["metadata"]),
        )

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(
            owner_id=row["owner_id"],
            authing_sub=row["authing_sub"],
            role=row["role"] if "role" in row.keys() else None,
            name=row["name"],
            nickname=row["nickname"],
            email=row["email"],
            phone=row["phone"],
            picture=row["picture"],
            raw_profile=self._parse_metadata(row["raw_profile"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_login_at=datetime.fromisoformat(row["last_login_at"]),
        )

    def _row_to_account_month_target(self, row: sqlite3.Row) -> AccountMonthTarget:
        return AccountMonthTarget(
            owner_id=row["owner_id"],
            year=int(row["year"]),
            month=int(row["month"]),
            target_count=int(row["target_count"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

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
    def _normalize_update_value(value: Any) -> Any:
        if isinstance(value, TaskStatus):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def create_task(self, task: Task) -> Task:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, owner_id, task_type, pn, title, status, progress, current_step,
                    output_dir, error_message, created_at, updated_at, completed_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
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
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                    task.completed_at.isoformat() if task.completed_at else None,
                    json.dumps(task.metadata, ensure_ascii=False) if task.metadata else None,
                ),
            )
            conn.commit()
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
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
            "metadata",
            "updated_at",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return False

        updates["updated_at"] = datetime.now().isoformat()
        for key in list(updates.keys()):
            updates[key] = self._normalize_update_value(self._encode_metadata(updates[key]))
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [task_id]

        with self._get_connection() as conn:
            cursor = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
            conn.commit()
            return cursor.rowcount > 0

    def delete_task(self, task_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE tasks SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                (datetime.now().isoformat(), datetime.now().isoformat(), task_id)
            )
            conn.commit()
            return cursor.rowcount > 0

    def _row_to_task_llm_usage(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "owner_id": row["owner_id"],
            "task_type": row["task_type"],
            "task_status": row["task_status"] or "",
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "reasoning_tokens": int(row["reasoning_tokens"] or 0),
            "llm_call_count": int(row["llm_call_count"] or 0),
            "estimated_cost_cny": float(row["estimated_cost_cny"] or 0),
            "price_missing": bool(int(row["price_missing"] or 0)),
            "model_breakdown_json": self._parse_metadata(row["model_breakdown_json"]),
            "first_usage_at": row["first_usage_at"],
            "last_usage_at": row["last_usage_at"],
            "currency": row["currency"] or "CNY",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
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
            "model_breakdown_json": self._encode_metadata(usage.get("model_breakdown_json") or {}),
            "first_usage_at": str(usage.get("first_usage_at") or "").strip() or None,
            "last_usage_at": str(usage.get("last_usage_at") or "").strip() or None,
            "currency": str(usage.get("currency") or "CNY").strip() or "CNY",
            "created_at": str(usage.get("created_at") or datetime.now().isoformat()),
            "updated_at": str(usage.get("updated_at") or datetime.now().isoformat()),
        }
        if not payload["task_id"] or not payload["owner_id"] or not payload["task_type"]:
            return False

        with self._get_connection() as conn:
            cursor = conn.execute(
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
                (
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
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_task_llm_usage_by_last_usage_range(
        self,
        start_iso: str,
        end_iso: str,
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM task_llm_usage
                WHERE last_usage_at IS NOT NULL
                  AND last_usage_at >= ?
                  AND last_usage_at < ?
                ORDER BY last_usage_at DESC
                """,
                (start_iso, end_iso),
            ).fetchall()
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
        with self._get_connection() as conn:
            summary_row = conn.execute(
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
            ).fetchone()

            total_tasks = int(summary_row["total_tasks"] if summary_row else 0)
            total_users = int(summary_row["total_users"] if summary_row else 0)
            total_tokens = int(summary_row["total_tokens"] if summary_row else 0)
            total_llm_call_count = int(summary_row["total_llm_call_count"] if summary_row else 0)
            total_estimated_cost_cny = float(summary_row["total_estimated_cost_cny"] if summary_row else 0.0)
            price_missing = bool(int(summary_row["price_missing"] or 0)) if summary_row else False

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
                total_row = conn.execute(
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
                ).fetchone()
                rows = conn.execute(
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
                ).fetchall()
                return {
                    "total": int(total_row["c"] if total_row else 0),
                    "price_missing": price_missing,
                    "summary": summary,
                    "items": [
                        {
                            "owner_id": row["owner_id"],
                            "user_name": row["user_name"],
                            "task_count": int(row["task_count"] or 0),
                            "total_tokens": int(row["total_tokens"] or 0),
                            "llm_call_count": int(row["llm_call_count"] or 0),
                            "estimated_cost_cny": round(float(row["estimated_cost_cny"] or 0), 6),
                            "price_missing": bool(int(row["price_missing"] or 0)),
                            "latest_usage_at": row["latest_usage_at"],
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
            total_row = conn.execute(
                f"""
                SELECT COUNT(*) AS c
                FROM task_llm_usage tu
                LEFT JOIN users u ON tu.owner_id = u.owner_id
                WHERE {base_where_clause}{q_where_clause}
                """,
                base_params + q_params,
            ).fetchone()
            rows = conn.execute(
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
            ).fetchall()

        task_items: List[Dict[str, Any]] = []
        for row in rows:
            model_breakdown = self._parse_metadata(row["model_breakdown_json"])
            models = [str(key) for key in model_breakdown.keys()] if isinstance(model_breakdown, dict) else []
            task_items.append(
                {
                    "task_id": row["task_id"],
                    "owner_id": row["owner_id"],
                    "user_name": row["user_name"],
                    "task_type": row["task_type"] or "",
                    "task_status": row["task_status"] or "",
                    "total_tokens": int(row["total_tokens"] or 0),
                    "llm_call_count": int(row["llm_call_count"] or 0),
                    "estimated_cost_cny": round(float(row["estimated_cost_cny"] or 0), 6),
                    "price_missing": bool(int(row["price_missing"] or 0)),
                    "models": models,
                    "last_usage_at": row["last_usage_at"],
                }
            )

        return {
            "total": int(total_row["c"] if total_row else 0),
            "price_missing": price_missing,
            "summary": summary,
            "items": task_items,
        }

    def _row_to_system_log(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "log_id": row["log_id"],
            "timestamp": row["timestamp"],
            "category": row["category"],
            "event_name": row["event_name"],
            "level": row["level"],
            "owner_id": row["owner_id"],
            "user_name": row["user_name"] if "user_name" in row.keys() else None,
            "task_id": row["task_id"],
            "task_type": row["task_type"],
            "request_id": row["request_id"],
            "trace_id": row["trace_id"],
            "method": row["method"],
            "path": row["path"],
            "status_code": row["status_code"],
            "duration_ms": row["duration_ms"],
            "provider": row["provider"],
            "target_host": row["target_host"],
            "success": bool(int(row["success"] or 0)),
            "message": row["message"],
            "payload_inline_json": row["payload_inline_json"],
            "payload_file_path": row["payload_file_path"],
            "payload_bytes": int(row["payload_bytes"] or 0),
            "payload_overflow": bool(int(row["payload_overflow"] or 0)),
            "created_at": row["created_at"],
        }

    def insert_system_log(self, record: Dict[str, Any]) -> bool:
        payload = {
            "log_id": str(record.get("log_id", "")).strip(),
            "timestamp": str(record.get("timestamp") or datetime.now().isoformat()),
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
            "created_at": str(record.get("created_at") or datetime.now().isoformat()),
        }
        if not payload["log_id"] or not payload["category"] or not payload["event_name"]:
            return False

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO system_logs (
                    log_id, timestamp, category, event_name, level,
                    owner_id, task_id, task_type, request_id, trace_id,
                    method, path, status_code, duration_ms,
                    provider, target_host, success, message,
                    payload_inline_json, payload_file_path, payload_bytes, payload_overflow, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
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
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_system_log(self, log_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM system_logs WHERE log_id = ?",
                (log_id,),
            ).fetchone()
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

        base_where = " AND ".join(where)
        offset = max(0, (page - 1) * page_size)
        with self._get_connection() as conn:
            total_row = conn.execute(
                f"""
                SELECT COUNT(*) AS c
                FROM system_logs sl
                LEFT JOIN users u ON sl.owner_id = u.owner_id
                WHERE {base_where}
                """,
                params,
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT sl.*, u.name AS user_name
                FROM system_logs sl
                LEFT JOIN users u ON sl.owner_id = u.owner_id
                WHERE {base_where}
                ORDER BY sl.timestamp DESC
                LIMIT ? OFFSET ?
                """,
                params + [page_size, offset],
            ).fetchall()
        return {
            "total": int(total_row["c"] if total_row else 0),
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
        with self._get_connection() as conn:
            overview_row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total_logs,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed_logs,
                    SUM(CASE WHEN category = 'llm_call' THEN 1 ELSE 0 END) AS llm_call_count
                FROM system_logs
                WHERE {where_clause}
                """,
                params,
            ).fetchone()
            category_rows = conn.execute(
                f"""
                SELECT category, COUNT(*) AS count
                FROM system_logs
                WHERE {where_clause}
                GROUP BY category
                ORDER BY count DESC
                """,
                params,
            ).fetchall()

        total_logs = int(overview_row["total_logs"] or 0) if overview_row else 0
        failed_logs = int(overview_row["failed_logs"] or 0) if overview_row else 0
        llm_call_count = int(overview_row["llm_call_count"] or 0) if overview_row else 0
        failed_rate = (failed_logs / total_logs) if total_logs else 0.0
        return {
            "totalLogs": total_logs,
            "failedLogs": failed_logs,
            "failedRate": round(failed_rate, 6),
            "llmCallCount": llm_call_count,
            "byCategory": [
                {"category": row["category"], "count": int(row["count"] or 0)}
                for row in category_rows
            ],
        }

    def cleanup_system_logs_before(self, cutoff_iso: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM system_logs WHERE timestamp < ?",
                (cutoff_iso,),
            )
            conn.commit()
            return int(cursor.rowcount or 0)

    def list_system_log_payload_paths_for_policy_cleanup(self) -> List[str]:
        policy_where = (
            "(category = 'user_action' AND UPPER(COALESCE(method, '')) = 'GET') "
            "OR (category != 'llm_call' AND category != 'user_action' AND success = 1)"
        )
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_file_path
                FROM system_logs
                WHERE ({policy_where})
                  AND payload_file_path IS NOT NULL
                  AND payload_file_path != ''
                """,
            ).fetchall()
        return [str(row["payload_file_path"]).strip() for row in rows if str(row["payload_file_path"] or "").strip()]

    def cleanup_system_logs_by_policy(self) -> int:
        policy_where = (
            "(category = 'user_action' AND UPPER(COALESCE(method, '')) = 'GET') "
            "OR (category != 'llm_call' AND category != 'user_action' AND success = 1)"
        )
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"DELETE FROM system_logs WHERE {policy_where}",
            )
            conn.commit()
            return int(cursor.rowcount or 0)

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

        with self._get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
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

        with self._get_connection() as conn:
            total_row = conn.execute(
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
            ).fetchone()

            rows = conn.execute(
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
            ).fetchall()

        return {
            "total": int(total_row["c"] if total_row else 0),
            "items": [
                {
                    "owner_id": row["owner_id"],
                    "user_name": row["user_name"],
                    "email": row["email"],
                    "role": row["role"],
                    "last_login_at": row["last_login_at"],
                    "created_at": row["created_at"],
                    "task_count": int(row["task_count"] or 0),
                    "latest_task_at": row["latest_task_at"],
                }
                for row in rows
            ],
        }

    def summarize_admin_users(self) -> Dict[str, Any]:
        now = datetime.now()
        cutoff_1d = (now - timedelta(days=1)).isoformat()
        cutoff_7d = (now - timedelta(days=7)).isoformat()
        cutoff_30d = (now - timedelta(days=30)).isoformat()
        with self._get_connection() as conn:
            overview_row = conn.execute(
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
            ).fetchone()
            active_row = conn.execute(
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
                (cutoff_1d, cutoff_7d, cutoff_30d),
            ).fetchone()
            new_row = conn.execute(
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
                (cutoff_1d, cutoff_7d, cutoff_30d),
            ).fetchone()
        return {
            "userStats": {
                "totalUsers": int(overview_row["total_users"] or 0) if overview_row else 0,
                "registeredUsers": int(overview_row["registered_users"] or 0) if overview_row else 0,
                "activeUsers1d": int(active_row["active_1d"] or 0) if active_row else 0,
                "activeUsers7d": int(active_row["active_7d"] or 0) if active_row else 0,
                "activeUsers30d": int(active_row["active_30d"] or 0) if active_row else 0,
                "newUsers1d": int(new_row["new_1d"] or 0) if new_row else 0,
                "newUsers7d": int(new_row["new_7d"] or 0) if new_row else 0,
                "newUsers30d": int(new_row["new_30d"] or 0) if new_row else 0,
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

        with self._get_connection() as conn:
            total_row = conn.execute(
                f"""
                SELECT COUNT(*) AS c
                FROM tasks t
                LEFT JOIN users u ON t.owner_id = u.owner_id
                WHERE {where_clause}
                """,
                params,
            ).fetchone()

            rows = conn.execute(
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
            ).fetchall()

        def _parse_iso(value: Any) -> Optional[datetime]:
            text = str(value or "").strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = f"{text[:-1]}+00:00"
            try:
                return datetime.fromisoformat(text)
            except ValueError:
                return None

        def _calc_duration_seconds(created_at: Any, completed_at: Any) -> Optional[int]:
            created_dt = _parse_iso(created_at)
            if not created_dt:
                return None
            end_dt = _parse_iso(completed_at)
            if not end_dt:
                end_dt = datetime.now(created_dt.tzinfo) if created_dt.tzinfo else datetime.now()
            try:
                seconds = int(end_dt.timestamp() - created_dt.timestamp())
            except Exception:
                return None
            return max(0, seconds)

        return {
            "total": int(total_row["c"] if total_row else 0),
            "items": [
                {
                    "task_id": row["task_id"],
                    "title": row["title"],
                    "owner_id": row["owner_id"],
                    "user_name": row["user_name"],
                    "task_type": row["task_type"],
                    "status": row["status"],
                    "duration_seconds": _calc_duration_seconds(row["created_at"], row["completed_at"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "completed_at": row["completed_at"],
                }
                for row in rows
            ],
        }

    def summarize_admin_tasks(self) -> Dict[str, Any]:
        now = datetime.now()
        cutoff_1d = (now - timedelta(days=1)).isoformat()
        cutoff_7d = (now - timedelta(days=7)).isoformat()
        cutoff_30d = (now - timedelta(days=30)).isoformat()
        with self._get_connection() as conn:
            task_type_rows = conn.execute(
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
                (cutoff_1d, cutoff_7d, cutoff_30d),
            ).fetchall()
        return {
            "taskTypeWindows": [
                {
                    "taskType": row["task_type"],
                    "count1d": int(row["count_1d"] or 0),
                    "count7d": int(row["count_7d"] or 0),
                    "count30d": int(row["count_30d"] or 0),
                }
                for row in task_type_rows
            ]
        }

    def get_admin_task_detail(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
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
                (task_id,),
            ).fetchone()

        if not row:
            return None

        return {
            "task_id": row["task_id"],
            "owner_id": row["owner_id"],
            "user_name": row["user_name"],
            "task_type": row["task_type"],
            "pn": row["pn"],
            "title": row["title"],
            "status": row["status"],
            "progress": int(row["progress"] or 0),
            "current_step": row["current_step"],
            "output_dir": row["output_dir"],
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
            "metadata": self._parse_metadata(row["metadata"]),
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

        sql = f"SELECT COUNT(*) FROM tasks WHERE {' AND '.join(where)}"
        with self._get_connection() as conn:
            return conn.execute(sql, params).fetchone()[0]

    def count_user_tasks_today(
        self,
        owner_id: str,
        tz_offset_hours: int = 8,
        task_type: Optional[str] = None,
        include_deleted: bool = False,
        statuses: Optional[List[str]] = None,
    ) -> int:
        today = datetime.utcnow() + timedelta(hours=tz_offset_hours)
        today_str = today.strftime("%Y-%m-%d")
        modifier = f"{tz_offset_hours:+d} hours"
        where = ["owner_id = ?", "DATE(created_at, ?) = ?"]
        params: List[Any] = [owner_id, modifier, today_str]
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
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM tasks WHERE {' AND '.join(where)}",
                params,
            ).fetchone()
        return row[0] if row else 0

    def get_statistics(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM tasks WHERE deleted_at IS NULL GROUP BY status"
            ).fetchall()
            status_counts = {row["status"]: row["count"] for row in rows}

            today = datetime.now().strftime("%Y-%m-%d")
            today_count = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE DATE(created_at) = ? AND deleted_at IS NULL",
                (today,),
            ).fetchone()[0]

            avg_row = conn.execute(
                """
                SELECT AVG((julianday(completed_at) - julianday(created_at)) * 24 * 60) AS avg_minutes
                FROM tasks
                WHERE status = 'completed' AND completed_at IS NOT NULL AND deleted_at IS NULL
                """
            ).fetchone()

            completed_patents = conn.execute(
                "SELECT COUNT(*) AS count FROM patent_analyses"
            ).fetchone()[0]

        avg_duration = avg_row[0] if avg_row and avg_row[0] else None
        return {
            "by_status": status_counts,
            "today_created": today_count,
            "avg_duration_minutes": round(avg_duration, 2) if avg_duration else None,
            "completed_patents": int(completed_patents or 0),
        }

    def record_patent_analysis(self, pn: Optional[str], sha256: Optional[str] = None) -> bool:
        if not pn:
            return False
        normalized = pn.strip().upper()
        if not normalized:
            return False
        normalized_sha256 = str(sha256 or "").strip().lower() or None
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO patent_analyses (pn, first_completed_at, sha256)
                VALUES (?, ?, ?)
                ON CONFLICT(pn) DO UPDATE SET
                    sha256 = CASE
                        WHEN excluded.sha256 IS NOT NULL AND TRIM(excluded.sha256) <> '' THEN excluded.sha256
                        ELSE patent_analyses.sha256
                    END
                """,
                (normalized, datetime.now().isoformat(), normalized_sha256),
            )
            conn.commit()
        return True

    def get_patent_analysis_by_pn(self, pn: Optional[str]) -> Optional[Dict[str, Any]]:
        normalized = str(pn or "").strip().upper()
        if not normalized:
            return None
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT pn, first_completed_at, sha256 FROM patent_analyses WHERE pn = ?",
                (normalized,),
            ).fetchone()
        if not row:
            return None
        return {
            "pn": str(row["pn"]),
            "first_completed_at": str(row["first_completed_at"]),
            "sha256": str(row["sha256"] or "").strip() or None,
        }

    def get_patent_analysis_by_sha256(self, sha256: Optional[str]) -> Optional[Dict[str, Any]]:
        normalized = str(sha256 or "").strip().lower()
        if not normalized:
            return None
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT pn, first_completed_at, sha256 FROM patent_analyses WHERE sha256 = ?",
                (normalized,),
            ).fetchone()
        if not row:
            return None
        return {
            "pn": str(row["pn"]),
            "first_completed_at": str(row["first_completed_at"]),
            "sha256": str(row["sha256"] or "").strip() or None,
        }

    def cleanup_old_tasks(self, days: int = 365, dry_run: bool = False) -> int:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._get_connection() as conn:
            task_ids = [
                row["id"]
                for row in conn.execute(
                    """
                    SELECT id FROM tasks
                    WHERE deleted_at IS NOT NULL AND deleted_at < ?
                    OR (updated_at < ? AND status IN ('completed', 'failed', 'cancelled') AND deleted_at IS NULL)
                    """,
                    (cutoff, cutoff),
                ).fetchall()
            ]

            if dry_run:
                return len(task_ids)

            if task_ids:
                placeholders = ",".join(["?"] * len(task_ids))
                conn.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", task_ids)
                conn.commit()
        return len(task_ids)

    def vacuum(self):
        with self._get_connection() as conn:
            conn.execute("VACUUM")
            conn.commit()

    def upsert_authing_user(self, user: User) -> User:
        now_iso = datetime.now().isoformat()
        created_at_iso = user.created_at.isoformat() if user.created_at else now_iso
        raw_profile = json.dumps(user.raw_profile, ensure_ascii=False) if user.raw_profile else None

        with self._get_connection() as conn:
            conn.execute(
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
                (
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
                ),
            )
            conn.commit()

            row = conn.execute(
                "SELECT * FROM users WHERE owner_id = ?",
                (user.owner_id,),
            ).fetchone()

        return self._row_to_user(row) if row else user

    def get_user_by_owner_id(self, owner_id: str) -> Optional[User]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE owner_id = ?",
                (owner_id,),
            ).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_name(self, name: str) -> Optional[User]:
        normalized = str(name or "").strip()
        if not normalized:
            return None
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE name = ?",
                (normalized,),
            ).fetchone()
        return self._row_to_user(row) if row else None

    def update_user_profile(
        self,
        owner_id: str,
        name: Optional[str],
        picture: Optional[str],
    ) -> Optional[User]:
        now_iso = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE users
                SET name = ?, picture = ?, updated_at = ?
                WHERE owner_id = ?
                """,
                (name, picture, now_iso, owner_id),
            )
            conn.commit()
            if cursor.rowcount <= 0:
                return None
            row = conn.execute(
                "SELECT * FROM users WHERE owner_id = ?",
                (owner_id,),
            ).fetchone()
        return self._row_to_user(row) if row else None

    def upsert_account_month_target(
        self,
        owner_id: str,
        year: int,
        month: int,
        target_count: int,
    ) -> AccountMonthTarget:
        now_iso = datetime.now().isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO account_month_targets (
                    owner_id, year, month, target_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, year, month) DO UPDATE SET
                    target_count = excluded.target_count,
                    updated_at = excluded.updated_at
                """,
                (owner_id, year, month, target_count, now_iso, now_iso),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT * FROM account_month_targets
                WHERE owner_id = ? AND year = ? AND month = ?
                """,
                (owner_id, year, month),
            ).fetchone()
        if row is None:
            raise RuntimeError("Failed to upsert account month target")
        return self._row_to_account_month_target(row)

    def get_account_month_target(
        self,
        owner_id: str,
        year: int,
        month: int,
    ) -> Optional[AccountMonthTarget]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM account_month_targets
                WHERE owner_id = ? AND year = ? AND month = ?
                """,
                (owner_id, year, month),
            ).fetchone()
        return self._row_to_account_month_target(row) if row else None

    def get_latest_account_month_target_before(
        self,
        owner_id: str,
        year: int,
        month: int,
    ) -> Optional[AccountMonthTarget]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM account_month_targets
                WHERE owner_id = ?
                  AND (year < ? OR (year = ? AND month < ?))
                ORDER BY year DESC, month DESC
                LIMIT 1
                """,
                (owner_id, year, year, month),
            ).fetchone()
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
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM tasks WHERE {' AND '.join(where)}",
                params,
            ).fetchone()
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
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM tasks WHERE {' AND '.join(where)}",
                params,
            ).fetchone()
        return int(row["c"]) if row else 0

    def aggregate_user_created_tasks_daily(
        self,
        owner_id: str,
        start_day: date,
        end_day: date,
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DATE(created_at) AS day, task_type, COUNT(*) AS count
                FROM tasks
                WHERE owner_id = ?
                  AND DATE(created_at) >= DATE(?)
                  AND DATE(created_at) <= DATE(?)
                  AND deleted_at IS NULL
                GROUP BY day, task_type
                ORDER BY day ASC
                """,
                (owner_id, start_day.isoformat(), end_day.isoformat()),
            ).fetchall()
        return [
            {
                "day": str(row["day"]),
                "task_type": str(row["task_type"]),
                "count": int(row["count"]),
            }
            for row in rows
        ]

    def aggregate_user_completed_tasks_daily(
        self,
        owner_id: str,
        start_day: date,
        end_day: date,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where = [
            "owner_id = ?",
            "completed_at IS NOT NULL",
            "DATE(completed_at) >= DATE(?)",
            "DATE(completed_at) <= DATE(?)",
            "deleted_at IS NULL",
        ]
        params: List[Any] = [owner_id, start_day.isoformat(), end_day.isoformat()]
        if task_type:
            where.append("task_type = ?")
            params.append(task_type)
        if status:
            where.append("status = ?")
            params.append(status)

        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT DATE(completed_at) AS day, task_type, status, COUNT(*) AS count
                FROM tasks
                WHERE {' AND '.join(where)}
                GROUP BY day, task_type, status
                ORDER BY day ASC
                """,
                params,
            ).fetchall()

        return [
            {
                "day": str(row["day"]),
                "task_type": str(row["task_type"]),
                "status": str(row["status"]),
                "count": int(row["count"]),
            }
            for row in rows
        ]
