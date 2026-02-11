"""
Cloudflare D1 task storage layer.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from loguru import logger

from .models import Task, TaskStep, TaskStatus


class D1TaskStorage:
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
        logger.info("D1TaskStorage initialized")

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
        for statement in self.CREATE_TABLES_SQL.split(";"):
            sql = statement.strip()
            if sql:
                self._request(sql)
        self._ensure_schema()

    def _ensure_schema(self):
        columns = {row.get("name") for row in self._fetchall("PRAGMA table_info(tasks)")}
        if "owner_id" not in columns:
            self._request("ALTER TABLE tasks ADD COLUMN owner_id TEXT")
        self._request("CREATE INDEX IF NOT EXISTS idx_tasks_owner_id ON tasks(owner_id)")

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

    def create_task(self, task: Task) -> Task:
        self._request(
            """
            INSERT INTO tasks (
                id, owner_id, pn, title, status, progress, current_step,
                output_dir, raw_pdf_path, error_message,
                created_at, updated_at, completed_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
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
            ],
        )
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        row = self._fetchone("SELECT * FROM tasks WHERE id = ?", [task_id])
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

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [task_id]
        result = self._request(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        return self._changed_rows(result) > 0

    def delete_task(self, task_id: str) -> bool:
        result = self._request("DELETE FROM tasks WHERE id = ?", [task_id])
        return self._changed_rows(result) > 0

    def add_task_step(self, task_id: str, step: TaskStep) -> bool:
        self._request(
            """
            INSERT INTO task_steps (
                task_id, step_name, step_order, status, progress,
                start_time, end_time, error_message, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                task_id,
                step.step_name,
                step.step_order,
                step.status,
                step.progress,
                step.start_time.isoformat() if step.start_time else None,
                step.end_time.isoformat() if step.end_time else None,
                step.error_message,
                json.dumps(step.metadata, ensure_ascii=False) if step.metadata else None,
            ],
        )
        return True

    def get_task_steps(self, task_id: str) -> List[TaskStep]:
        rows = self._fetchall(
            "SELECT * FROM task_steps WHERE task_id = ? ORDER BY step_order ASC",
            [task_id],
        )
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
            clauses.append(f"{key} = ?")
            params.append(value)

        params.extend([task_id, step_name])
        result = self._request(
            f"UPDATE task_steps SET {', '.join(clauses)} WHERE task_id = ? AND step_name = ?",
            params,
        )
        return self._changed_rows(result) > 0

    def delete_task_steps(self, task_id: str) -> bool:
        self._request("DELETE FROM task_steps WHERE task_id = ?", [task_id])
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

        row = self._fetchone(f"SELECT COUNT(*) AS c FROM tasks WHERE {' AND '.join(where)}", params)
        return int(row["c"]) if row else 0

    def count_user_tasks_today(self, owner_id: str, tz_offset_hours: int = 8) -> int:
        today = datetime.utcnow() + timedelta(hours=tz_offset_hours)
        today_str = today.strftime("%Y-%m-%d")
        modifier = f"{tz_offset_hours:+d} hours"
        row = self._fetchone(
            """
            SELECT COUNT(*) AS c FROM tasks
            WHERE owner_id = ?
            AND DATE(created_at, ?) = ?
            """,
            [owner_id, modifier, today_str],
        )
        return int(row["c"]) if row else 0

    def get_statistics(self) -> Dict[str, Any]:
        rows = self._fetchall("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status")
        status_counts = {row["status"]: int(row["count"]) for row in rows}

        today = datetime.now().strftime("%Y-%m-%d")
        today_count_row = self._fetchone(
            "SELECT COUNT(*) AS c FROM tasks WHERE DATE(created_at) = ?",
            [today],
        )
        today_count = int(today_count_row["c"]) if today_count_row else 0

        avg_row = self._fetchone(
            """
            SELECT AVG((julianday(completed_at) - julianday(created_at)) * 24 * 60) AS avg_minutes
            FROM tasks
            WHERE status = 'completed' AND completed_at IS NOT NULL
            """
        )

        total = int(sum(status_counts.values()))
        avg_duration = avg_row.get("avg_minutes") if avg_row else None
        return {
            "total": total,
            "by_status": status_counts,
            "today_created": today_count,
            "avg_duration_minutes": round(float(avg_duration), 2) if avg_duration else None,
        }

    def cleanup_old_tasks(self, days: int = 30, dry_run: bool = False) -> int:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = self._fetchall(
            """
            SELECT id FROM tasks
            WHERE updated_at < ?
            AND status IN ('completed', 'failed', 'cancelled')
            """,
            [cutoff],
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
            logger.warning(f"D1 VACUUM skipped: {exc}")
