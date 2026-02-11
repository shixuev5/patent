"""
SQLite task storage layer.
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from loguru import logger

from config import settings
from .models import Task, TaskStep, TaskStatus


class TaskStorage:
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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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

    def __init__(self, db_path: Union[str, Path, None] = None):
        if db_path is None:
            db_path = settings.DATA_DIR / "tasks.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_database()
        logger.info(f"TaskStorage initialized: {self.db_path}")

    def _init_database(self):
        with self._get_connection() as conn:
            conn.executescript(self.CREATE_TABLES_SQL)
            self._ensure_schema(conn)
            conn.commit()

    def _ensure_schema(self, conn: sqlite3.Connection):
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        if "owner_id" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN owner_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_owner_id ON tasks(owner_id)")

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
            pn=row["pn"],
            title=row["title"],
            status=TaskStatus(row["status"]),
            progress=row["progress"],
            current_step=row["current_step"],
            output_dir=row["output_dir"],
            raw_pdf_path=row["raw_pdf_path"],
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    def _row_to_step(self, row: sqlite3.Row) -> TaskStep:
        return TaskStep(
            step_name=row["step_name"],
            step_order=row["step_order"],
            status=row["status"],
            progress=row["progress"],
            start_time=datetime.fromisoformat(row["start_time"]) if row["start_time"] else None,
            end_time=datetime.fromisoformat(row["end_time"]) if row["end_time"] else None,
            error_message=row["error_message"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    def create_task(self, task: Task) -> Task:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, owner_id, pn, title, status, progress, current_step,
                    output_dir, raw_pdf_path, error_message,
                    created_at, updated_at, completed_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
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
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [task_id]

        with self._get_connection() as conn:
            cursor = conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
            conn.commit()
            return cursor.rowcount > 0

    def delete_task(self, task_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            return cursor.rowcount > 0

    def add_task_step(self, task_id: str, step: TaskStep) -> bool:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO task_steps (
                    task_id, step_name, step_order, status, progress,
                    start_time, end_time, error_message, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_order ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_step(row) for row in rows]

    def update_task_step(self, task_id: str, step_name: str, **kwargs) -> bool:
        allowed_fields = {"status", "progress", "start_time", "end_time", "error_message", "metadata"}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return False

        params = []
        clauses = []
        for key, value in updates.items():
            if key in ("start_time", "end_time") and isinstance(value, datetime):
                value = value.isoformat()
            elif key == "metadata" and value is not None:
                value = json.dumps(value, ensure_ascii=False)
            clauses.append(f"{key} = ?")
            params.append(value)

        params.extend([task_id, step_name])
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE task_steps SET {', '.join(clauses)} WHERE task_id = ? AND step_name = ?",
                params,
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_task_steps(self, task_id: str) -> bool:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM task_steps WHERE task_id = ?", (task_id,))
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
            where.append("status = ?")
            params.append(status.value)
        if pn:
            where.append("pn LIKE ?")
            params.append(f"%{pn}%")
        if owner_id:
            where.append("owner_id = ?")
            params.append(owner_id)

        direction = "DESC" if order_desc else "ASC"
        sql = f"""
            SELECT * FROM tasks
            WHERE {' AND '.join(where)}
            ORDER BY {order_by} {direction}
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
        where = ["1=1"]
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

    def count_user_tasks_today(self, owner_id: str, tz_offset_hours: int = 8) -> int:
        today = datetime.utcnow() + timedelta(hours=tz_offset_hours)
        today_str = today.strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM tasks
                WHERE owner_id = ?
                AND DATE(created_at, '+8 hours') = ?
                """,
                (owner_id, today_str),
            ).fetchone()
        return row[0] if row else 0

    def get_statistics(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
            ).fetchall()
            status_counts = {row["status"]: row["count"] for row in rows}

            today = datetime.now().strftime("%Y-%m-%d")
            today_count = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE DATE(created_at) = ?",
                (today,),
            ).fetchone()[0]

            avg_row = conn.execute(
                """
                SELECT AVG((julianday(completed_at) - julianday(created_at)) * 24 * 60) AS avg_minutes
                FROM tasks
                WHERE status = 'completed' AND completed_at IS NOT NULL
                """
            ).fetchone()

        total = sum(status_counts.values())
        avg_duration = avg_row[0] if avg_row and avg_row[0] else None
        return {
            "total": total,
            "by_status": status_counts,
            "today_created": today_count,
            "avg_duration_minutes": round(avg_duration, 2) if avg_duration else None,
        }

    def cleanup_old_tasks(self, days: int = 30, dry_run: bool = False) -> int:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        with self._get_connection() as conn:
            task_ids = [
                row["id"]
                for row in conn.execute(
                    """
                    SELECT id FROM tasks
                    WHERE updated_at < ?
                    AND status IN ('completed', 'failed', 'cancelled')
                    """,
                    (cutoff,),
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


_storage_instance: Optional[TaskStorage] = None
_storage_lock = threading.Lock()


def get_task_storage(db_path: Optional[Union[str, Path]] = None) -> TaskStorage:
    global _storage_instance
    if _storage_instance is None:
        with _storage_lock:
            if _storage_instance is None:
                _storage_instance = TaskStorage(db_path)
    return _storage_instance


def reset_storage_instance():
    global _storage_instance
    with _storage_lock:
        _storage_instance = None
