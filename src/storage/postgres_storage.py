"""
PostgreSQL task storage layer.
"""

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from .models import Task, TaskStep, TaskStatus

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - guarded by runtime env
    psycopg = None
    dict_row = None


class PostgresTaskStorage:
    CREATE_TABLES_SQL = """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        owner_id TEXT,
        pn TEXT,
        title TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        progress INTEGER DEFAULT 0,
        current_step TEXT,
        output_dir TEXT,
        raw_pdf_path TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        metadata TEXT
    );

    CREATE TABLE IF NOT EXISTS task_steps (
        id BIGSERIAL PRIMARY KEY,
        task_id TEXT NOT NULL,
        step_name TEXT NOT NULL,
        step_order INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        progress INTEGER DEFAULT 0,
        start_time TEXT,
        end_time TEXT,
        error_message TEXT,
        metadata TEXT,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_owner_id ON tasks(owner_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_pn ON tasks(pn);
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
    CREATE INDEX IF NOT EXISTS idx_steps_task_id ON task_steps(task_id);
    """

    def __init__(self, database_url: str):
        if not database_url:
            raise ValueError("database_url is required for PostgresTaskStorage")
        if psycopg is None:
            raise RuntimeError(
                "psycopg is not installed. Please add dependency `psycopg[binary]`."
            )

        self.database_url = database_url
        self._local = threading.local()
        self._init_database()
        logger.info("PostgresTaskStorage initialized")

    def _get_or_create_connection(self):
        conn = getattr(self._local, "connection", None)
        if conn is None or conn.closed:
            conn = psycopg.connect(
                self.database_url,
                autocommit=False,
                row_factory=dict_row,
            )
            self._local.connection = conn
        return conn

    @contextmanager
    def _get_connection(self):
        conn = self._get_or_create_connection()
        try:
            yield conn
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise

    def _init_database(self):
        with self._get_connection() as conn:
            conn.execute(self.CREATE_TABLES_SQL)
            conn.commit()

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

    def _row_to_task(self, row: Dict[str, Any]) -> Task:
        return Task(
            id=row["id"],
            owner_id=row.get("owner_id"),
            pn=row.get("pn"),
            title=row.get("title"),
            status=TaskStatus(row["status"]),
            progress=row.get("progress", 0),
            current_step=row.get("current_step"),
            output_dir=row.get("output_dir"),
            raw_pdf_path=row.get("raw_pdf_path"),
            error_message=row.get("error_message"),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"])
            if row.get("completed_at")
            else None,
            metadata=self._parse_metadata(row.get("metadata")),
        )

    def _row_to_step(self, row: Dict[str, Any]) -> TaskStep:
        return TaskStep(
            step_name=row["step_name"],
            step_order=row["step_order"],
            status=row.get("status", "pending"),
            progress=row.get("progress", 0),
            start_time=datetime.fromisoformat(row["start_time"])
            if row.get("start_time")
            else None,
            end_time=datetime.fromisoformat(row["end_time"]) if row.get("end_time") else None,
            error_message=row.get("error_message"),
            metadata=self._parse_metadata(row.get("metadata")),
        )

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
                    id, owner_id, pn, title, status, progress, current_step,
                    output_dir, raw_pdf_path, error_message,
                    created_at, updated_at, completed_at, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    task.id,
                    task.owner_id,
                    task.pn,
                    task.title,
                    task.status.value,
                    task.progress,
                    task.current_step,
                    task.output_dir,
                    task.raw_pdf_path,
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
            row = conn.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def get_task_with_steps(self, task_id: str) -> Optional[Task]:
        task = self.get_task(task_id)
        if not task:
            return None
        task.steps = self.get_task_steps(task_id)
        return task

    def update_task(self, task_id: str, **kwargs) -> bool:
        allowed_fields = {
            "owner_id",
            "pn",
            "title",
            "status",
            "progress",
            "current_step",
            "output_dir",
            "raw_pdf_path",
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

        set_clause = ", ".join([f"{k} = %s" for k in updates.keys()])
        values = list(updates.values()) + [task_id]

        with self._get_connection() as conn:
            cursor = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = %s", values)
            conn.commit()
            return cursor.rowcount > 0

    def delete_task(self, task_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
            conn.commit()
            return cursor.rowcount > 0

    def add_task_step(self, task_id: str, step: TaskStep) -> bool:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO task_steps (
                    task_id, step_name, step_order, status, progress,
                    start_time, end_time, error_message, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    task_id,
                    step.step_name,
                    step.step_order,
                    step.status,
                    step.progress,
                    step.start_time.isoformat() if step.start_time else None,
                    step.end_time.isoformat() if step.end_time else None,
                    step.error_message,
                    json.dumps(step.metadata, ensure_ascii=False) if step.metadata else None,
                ),
            )
            conn.commit()
        return True

    def get_task_steps(self, task_id: str) -> List[TaskStep]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM task_steps WHERE task_id = %s ORDER BY step_order ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_step(row) for row in rows]

    def update_task_step(self, task_id: str, step_name: str, **kwargs) -> bool:
        allowed_fields = {"status", "progress", "start_time", "end_time", "error_message", "metadata"}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return False

        clauses = []
        params = []
        for key, value in updates.items():
            value = self._normalize_update_value(self._encode_metadata(value))
            clauses.append(f"{key} = %s")
            params.append(value)

        params.extend([task_id, step_name])
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE task_steps SET {', '.join(clauses)} WHERE task_id = %s AND step_name = %s",
                params,
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_task_steps(self, task_id: str) -> bool:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM task_steps WHERE task_id = %s", (task_id,))
            conn.commit()
        return True

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
        where = ["1=1"]
        params: List[Any] = []

        if status:
            where.append("status = %s")
            params.append(status.value)
        if pn:
            where.append("pn LIKE %s")
            params.append(f"%{pn}%")
        if owner_id:
            where.append("owner_id = %s")
            params.append(owner_id)

        allowed_order_columns = {"created_at", "updated_at", "progress", "status", "pn"}
        safe_order_by = order_by if order_by in allowed_order_columns else "created_at"
        direction = "DESC" if order_desc else "ASC"

        sql = f"""
            SELECT * FROM tasks
            WHERE {' AND '.join(where)}
            ORDER BY {safe_order_by} {direction}
            LIMIT %s OFFSET %s
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
        where = ["1=1"]
        params: List[Any] = []

        if status:
            where.append("status = %s")
            params.append(status.value)
        if pn:
            where.append("pn LIKE %s")
            params.append(f"%{pn}%")
        if owner_id:
            where.append("owner_id = %s")
            params.append(owner_id)

        sql = f"SELECT COUNT(*) AS c FROM tasks WHERE {' AND '.join(where)}"
        with self._get_connection() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["c"]) if row else 0

    def count_user_tasks_today(self, owner_id: str, tz_offset_hours: int = 8) -> int:
        today = datetime.utcnow() + timedelta(hours=tz_offset_hours)
        today_str = today.strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM tasks
                WHERE owner_id = %s
                AND ((created_at::timestamp + (%s || ' hours')::interval)::date = %s::date)
                """,
                (owner_id, tz_offset_hours, today_str),
            ).fetchone()
        return int(row["c"]) if row else 0

    def get_statistics(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
            ).fetchall()
            status_counts = {row["status"]: row["count"] for row in rows}

            today_count_row = conn.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE created_at::date = CURRENT_DATE"
            ).fetchone()
            today_count = int(today_count_row["c"]) if today_count_row else 0

            avg_row = conn.execute(
                """
                SELECT AVG(EXTRACT(EPOCH FROM (completed_at::timestamp - created_at::timestamp)) / 60.0) AS avg_minutes
                FROM tasks
                WHERE status = 'completed' AND completed_at IS NOT NULL
                """
            ).fetchone()

        total = int(sum(status_counts.values()))
        avg_duration = avg_row["avg_minutes"] if avg_row else None
        return {
            "total": total,
            "by_status": status_counts,
            "today_created": today_count,
            "avg_duration_minutes": round(float(avg_duration), 2) if avg_duration else None,
        }

    def cleanup_old_tasks(self, days: int = 30, dry_run: bool = False) -> int:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id FROM tasks
                WHERE updated_at < %s
                AND status IN ('completed', 'failed', 'cancelled')
                """,
                (cutoff,),
            ).fetchall()
            task_ids = [row["id"] for row in rows]

            if dry_run:
                return len(task_ids)

            if task_ids:
                conn.execute("DELETE FROM tasks WHERE id = ANY(%s)", (task_ids,))
                conn.commit()
        return len(task_ids)

    def vacuum(self):
        with self._get_connection() as conn:
            conn.execute("VACUUM")
            conn.commit()
