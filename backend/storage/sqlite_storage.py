"""
SQLite task storage layer implementation.
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

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
        "users": [
            ("owner_id", "owner_id TEXT PRIMARY KEY"),
            ("authing_sub", "authing_sub TEXT NOT NULL UNIQUE"),
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
        first_completed_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS users (
        owner_id TEXT PRIMARY KEY,
        authing_sub TEXT NOT NULL UNIQUE,
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

    CREATE INDEX IF NOT EXISTS idx_tasks_pn ON tasks(pn);
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
    CREATE INDEX IF NOT EXISTS idx_patent_analyses_completed_at ON patent_analyses(first_completed_at);
    CREATE INDEX IF NOT EXISTS idx_users_authing_sub ON users(authing_sub);
    CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
    CREATE INDEX IF NOT EXISTS idx_account_month_targets_owner_ym ON account_month_targets(owner_id, year, month);
    """

    def __init__(self, db_path: Union[str, Path, None] = None):
        if db_path is None:
            db_path = settings.DATA_DIR / "tasks.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_database()
        logger.info(f"SQLiteTaskStorage initialized: {self.db_path}")

    def _init_database(self):
        with self._get_connection() as conn:
            conn.executescript(self.CREATE_TABLES_SQL)
            for table_name, required_columns in self.REQUIRED_COLUMNS.items():
                existing_columns = self._get_existing_columns(conn, table_name)
                for column_name, ddl in required_columns:
                    if column_name not in existing_columns:
                        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")
            conn.execute(
                "UPDATE tasks SET task_type = ? WHERE task_type IS NULL OR task_type = ''",
                (TaskType.PATENT_ANALYSIS.value,),
            )
            self._drop_legacy_raw_pdf_path_column(conn)
            conn.execute("DROP INDEX IF EXISTS idx_steps_task_id")
            conn.execute("DROP TABLE IF EXISTS task_steps")
            conn.commit()

    @staticmethod
    def _get_existing_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    def _drop_legacy_raw_pdf_path_column(self, conn: sqlite3.Connection):
        existing_columns = self._get_existing_columns(conn, "tasks")
        if "raw_pdf_path" not in existing_columns:
            return

        conn.execute("ALTER TABLE tasks DROP COLUMN raw_pdf_path")
        logger.info("Dropped legacy tasks.raw_pdf_path column")

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
    ) -> int:
        today = datetime.utcnow() + timedelta(hours=tz_offset_hours)
        today_str = today.strftime("%Y-%m-%d")
        modifier = f"{tz_offset_hours:+d} hours"
        where = ["owner_id = ?", "DATE(created_at, ?) = ?"]
        params: List[Any] = [owner_id, modifier, today_str]
        if task_type:
            where.append("task_type = ?")
            params.append(task_type)
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

    def record_patent_analysis(self, pn: Optional[str]) -> bool:
        if not pn:
            return False
        normalized = pn.strip().upper()
        if not normalized:
            return False
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO patent_analyses (pn, first_completed_at)
                VALUES (?, ?)
                """,
                (normalized, datetime.now().isoformat()),
            )
            conn.commit()
        return True

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
                    owner_id, authing_sub, name, nickname, email, phone, picture,
                    raw_profile, created_at, updated_at, last_login_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id) DO UPDATE SET
                    authing_sub = excluded.authing_sub,
                    name = excluded.name,
                    nickname = excluded.nickname,
                    email = excluded.email,
                    phone = excluded.phone,
                    picture = excluded.picture,
                    raw_profile = excluded.raw_profile,
                    updated_at = excluded.updated_at,
                    last_login_at = excluded.last_login_at
                """,
                (
                    user.owner_id,
                    user.authing_sub,
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
