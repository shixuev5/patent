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
    REQUIRED_COLUMNS = {
        "tasks": [
            ("owner_id", "owner_id TEXT"),
            ("pn", "pn TEXT"),
            ("title", "title TEXT"),
            ("status", "status TEXT NOT NULL DEFAULT 'pending'"),
            ("progress", "progress INTEGER DEFAULT 0"),
            ("current_step", "current_step TEXT"),
            ("output_dir", "output_dir TEXT"),
            ("raw_pdf_path", "raw_pdf_path TEXT"),
            ("error_message", "error_message TEXT"),
            ("created_at", "created_at TEXT NOT NULL"),
            ("updated_at", "updated_at TEXT NOT NULL"),
            ("completed_at", "completed_at TEXT"),
            ("deleted_at", "deleted_at TEXT"),
            ("metadata", "metadata TEXT"),
        ],
        "task_steps": [
            ("step_name", "step_name TEXT NOT NULL"),
            ("step_order", "step_order INTEGER NOT NULL"),
            ("status", "status TEXT DEFAULT 'pending'"),
            ("start_time", "start_time TEXT"),
            ("end_time", "end_time TEXT"),
            ("error_message", "error_message TEXT"),
        ],
    }

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
        deleted_at TEXT,
        metadata TEXT
    );

    CREATE TABLE IF NOT EXISTS task_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        step_name TEXT NOT NULL,
        step_order INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        start_time TEXT,
        end_time TEXT,
        error_message TEXT,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS patent_analyses (
        pn TEXT PRIMARY KEY,
        first_completed_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_owner_id ON tasks(owner_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_pn ON tasks(pn);
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
    CREATE INDEX IF NOT EXISTS idx_steps_task_id ON task_steps(task_id);
    CREATE INDEX IF NOT EXISTS idx_patent_analyses_completed_at ON patent_analyses(first_completed_at);
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
        self._apply_schema_migrations()

    def _apply_schema_migrations(self):
        for table_name, columns in self.REQUIRED_COLUMNS.items():
            existing = self._get_existing_columns(table_name)
            for column_name, definition in columns:
                if column_name in existing:
                    continue
                self._request(f"ALTER TABLE {table_name} ADD COLUMN {definition}")
                logger.info(f"[D1 Migration] Added column {table_name}.{column_name}")

            # 直接指定要删除的列，以确保安全
            if table_name == "task_steps":
                columns_to_drop = ["progress", "metadata"]
                for column_name in columns_to_drop:
                    if column_name in existing:
                        self._request(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")
                        logger.info(f"[D1 Migration] Dropped column {table_name}.{column_name}")

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
            completed_at=datetime.fromisoformat(row["completed_at"]) if row.get("completed_at") else None,
            deleted_at=datetime.fromisoformat(row["deleted_at"]) if row.get("deleted_at") else None,
            metadata=self._parse_metadata(row.get("metadata")),
        )

    def _row_to_step(self, row: Dict[str, Any]) -> TaskStep:
        return TaskStep(
            step_name=row["step_name"],
            step_order=row["step_order"],
            status=row.get("status", "pending"),
            start_time=datetime.fromisoformat(row["start_time"])
            if row.get("start_time")
            else None,
            end_time=datetime.fromisoformat(row["end_time"]) if row.get("end_time") else None,
            error_message=row.get("error_message"),
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
            "deleted_at",
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
        result = self._request(
            "UPDATE tasks SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
            [datetime.now().isoformat(), datetime.now().isoformat(), task_id]
        )
        return self._changed_rows(result) > 0

    def add_task_step(self, task_id: str, step: TaskStep) -> bool:
        self._request(
            """
            INSERT INTO task_steps (
                task_id, step_name, step_order, status,
                start_time, end_time, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                task_id,
                step.step_name,
                step.step_order,
                step.status,
                step.start_time.isoformat() if step.start_time else None,
                step.end_time.isoformat() if step.end_time else None,
                step.error_message,
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
        allowed_fields = {"status", "start_time", "end_time", "error_message"}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return False

        clauses = []
        params = []
        for key, value in updates.items():
            value = self._normalize_update_value(value)
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

    def count_user_tasks_today(self, owner_id: str, tz_offset_hours: int = 8) -> int:
        today = datetime.utcnow() + timedelta(hours=tz_offset_hours)
        today_str = today.strftime("%Y-%m-%d")
        modifier = f"{tz_offset_hours:+d} hours"
        row = self._fetchone(
            """
            SELECT COUNT(*) AS c FROM tasks
            WHERE owner_id = ?
            AND DATE(created_at, ?) = ?
            AND deleted_at IS NULL
            """,
            [owner_id, modifier, today_str],
        )
        return int(row["c"]) if row else 0

    def get_statistics(self) -> Dict[str, Any]:
        rows = self._fetchall("SELECT status, COUNT(*) AS count FROM tasks WHERE deleted_at IS NULL GROUP BY status")
        status_counts = {row["status"]: int(row["count"]) for row in rows}

        today = datetime.now().strftime("%Y-%m-%d")
        today_count_row = self._fetchone(
            "SELECT COUNT(*) AS c FROM tasks WHERE DATE(created_at) = ? AND deleted_at IS NULL",
            [today],
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

    def record_patent_analysis(self, pn: Optional[str]) -> bool:
        if not pn:
            return False
        normalized = pn.strip().upper()
        if not normalized:
            return False
        self._request(
            """
            INSERT OR IGNORE INTO patent_analyses (pn, first_completed_at)
            VALUES (?, ?)
            """,
            [normalized, datetime.now().isoformat()],
        )
        return True

    def cleanup_old_tasks(self, days: int = 365, dry_run: bool = False) -> int:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
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
            logger.warning(f"D1 VACUUM skipped: {exc}")
