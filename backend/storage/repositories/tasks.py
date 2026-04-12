"""Task, admin task, and patent analysis repository methods."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from backend.time_utils import (
    local_day_start_end_to_utc,
    local_recent_day_window_to_utc,
    parse_storage_ts,
    to_utc_z,
    utc_now_z,
    utc_to_local_day,
)
from ..models import AccountMonthTarget, Task, TaskStatus


class TaskRepositoryMixin:
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
        allowed_fields = {"owner_id", "task_type", "pn", "title", "status", "progress", "current_step", "output_dir", "error_message", "completed_at", "deleted_at", "metadata", "updated_at"}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return False
        updates["updated_at"] = utc_now_z()
        for key in list(updates.keys()):
            updates[key] = self._normalize_update_value(self._encode_metadata(updates[key]))
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [task_id]
        return self._changed_rows(self._request(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)) > 0

    def delete_task(self, task_id: str) -> bool:
        result = self._request(
            "UPDATE tasks SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
            [utc_now_z(), utc_now_z(), task_id],
        )
        return self._changed_rows(result) > 0

    def list_tasks(self, status: Optional[TaskStatus] = None, pn: Optional[str] = None, owner_id: Optional[str] = None, limit: int = 100, offset: int = 0, order_by: str = "created_at", order_desc: bool = True) -> List[Task]:
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
        sql = f"SELECT * FROM tasks WHERE {' AND '.join(where)} ORDER BY {safe_order_by} {direction} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._fetchall(sql, params)
        return [self._row_to_task(row) for row in rows]

    def list_admin_tasks(self, *, q: Optional[str] = None, user_name: Optional[str] = None, task_type: Optional[str] = None, status: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None, page: int = 1, page_size: int = 10, sort_by: str = "created_at", sort_order: str = "desc") -> Dict[str, Any]:
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
            where.append("(t.id LIKE ? OR COALESCE(t.title, '') LIKE ? OR COALESCE(t.pn, '') LIKE ? OR COALESCE(t.owner_id, '') LIKE ? OR COALESCE(u.name, '') LIKE ?)")
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
        total_row = self._fetchone(f"SELECT COUNT(*) AS c FROM tasks t LEFT JOIN users u ON t.owner_id = u.owner_id WHERE {where_clause}", params)
        rows = self._fetchall(
            f"""
            SELECT t.id AS task_id, t.title AS title, t.owner_id AS owner_id, u.name AS user_name, t.task_type AS task_type,
                   t.status AS status, t.created_at AS created_at, t.updated_at AS updated_at, t.completed_at AS completed_at
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
            end_dt = _parse_iso(completed_at) or parse_storage_ts(utc_now_z(), naive_strategy="utc")
            try:
                seconds = int(end_dt.timestamp() - created_dt.timestamp())
            except Exception:
                return None
            return max(0, seconds)

        return {"total": int((total_row or {}).get("c") or 0), "items": [{
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
        } for row in rows]}

    def summarize_admin_tasks(self) -> Dict[str, Any]:
        cutoff_1d, _ = local_recent_day_window_to_utc(1)
        cutoff_7d, _ = local_recent_day_window_to_utc(7)
        cutoff_30d, _ = local_recent_day_window_to_utc(30)
        task_type_rows = self._fetchall(
            """
            SELECT COALESCE(NULLIF(TRIM(task_type), ''), 'unknown') AS task_type,
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
        return {"taskTypeWindows": [{
            "taskType": row.get("task_type"),
            "count1d": int(row.get("count_1d") or 0),
            "count7d": int(row.get("count_7d") or 0),
            "count30d": int(row.get("count_30d") or 0),
        } for row in task_type_rows]}

    def get_admin_task_detail(self, task_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT t.id AS task_id, t.owner_id AS owner_id, u.name AS user_name, t.task_type AS task_type, t.pn AS pn,
                   t.title AS title, t.status AS status, t.progress AS progress, t.current_step AS current_step,
                   t.output_dir AS output_dir, t.error_message AS error_message, t.created_at AS created_at,
                   t.updated_at AS updated_at, t.completed_at AS completed_at, t.metadata AS metadata
            FROM tasks t
            LEFT JOIN users u ON t.owner_id = u.owner_id
            WHERE t.id = ? AND t.deleted_at IS NULL
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

    def count_tasks(self, status: Optional[TaskStatus] = None, pn: Optional[str] = None, owner_id: Optional[str] = None) -> int:
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

    def count_user_tasks_today(self, owner_id: str, tz_offset_hours: int = 8, task_type: Optional[str] = None, include_deleted: bool = False, statuses: Optional[List[str]] = None) -> int:
        del tz_offset_hours
        start_iso, end_iso = local_recent_day_window_to_utc(1)
        where = ["owner_id = ?", "created_at >= ?", "created_at < ?"]
        params: List[Any] = [owner_id, start_iso, end_iso]
        if task_type:
            where.append("task_type = ?")
            params.append(task_type)
        normalized_statuses = [str(status).strip().lower() for status in (statuses or []) if str(status).strip()]
        if normalized_statuses:
            placeholders = ", ".join(["?"] * len(normalized_statuses))
            where.append(f"LOWER(status) IN ({placeholders})")
            params.extend(normalized_statuses)
        if not include_deleted:
            where.append("deleted_at IS NULL")
        row = self._fetchone(f"SELECT COUNT(*) AS c FROM tasks WHERE {' AND '.join(where)}", params)
        return int(row["c"]) if row else 0

    def get_statistics(self) -> Dict[str, Any]:
        rows = self._fetchall("SELECT status, COUNT(*) AS count FROM tasks WHERE deleted_at IS NULL GROUP BY status")
        status_counts = {row["status"]: int(row["count"]) for row in rows}
        today_start_iso, today_end_iso = local_recent_day_window_to_utc(1)
        today_count_row = self._fetchone("SELECT COUNT(*) AS c FROM tasks WHERE created_at >= ? AND created_at < ? AND deleted_at IS NULL", [today_start_iso, today_end_iso])
        avg_row = self._fetchone(
            """
            SELECT AVG((julianday(completed_at) - julianday(created_at)) * 24 * 60) AS avg_minutes
            FROM tasks
            WHERE status = 'completed' AND completed_at IS NOT NULL AND deleted_at IS NULL
            """
        )
        completed_patents_row = self._fetchone("SELECT COUNT(*) AS c FROM patent_analyses")
        avg_duration = avg_row.get("avg_minutes") if avg_row else None
        return {
            "by_status": status_counts,
            "today_created": int(today_count_row["c"]) if today_count_row else 0,
            "avg_duration_minutes": round(float(avg_duration), 2) if avg_duration else None,
            "completed_patents": int(completed_patents_row["c"]) if completed_patents_row else 0,
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
        row = self._fetchone("SELECT pn, first_completed_at, sha256 FROM patent_analyses WHERE pn = ?", [normalized])
        if not row:
            return None
        return {"pn": str(row.get("pn") or ""), "first_completed_at": str(row.get("first_completed_at") or ""), "sha256": str(row.get("sha256") or "").strip() or None}

    def get_patent_analysis_by_sha256(self, sha256: Optional[str]) -> Optional[Dict[str, Any]]:
        normalized = str(sha256 or "").strip().lower()
        if not normalized:
            return None
        row = self._fetchone("SELECT pn, first_completed_at, sha256 FROM patent_analyses WHERE sha256 = ?", [normalized])
        if not row:
            return None
        return {"pn": str(row.get("pn") or ""), "first_completed_at": str(row.get("first_completed_at") or ""), "sha256": str(row.get("sha256") or "").strip() or None}

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

    def summarize_admin_users(self) -> Dict[str, Any]:
        cutoff_1d, _ = local_recent_day_window_to_utc(1)
        cutoff_7d, _ = local_recent_day_window_to_utc(7)
        cutoff_30d, _ = local_recent_day_window_to_utc(30)
        overview_row = self._fetchone(
            """
            WITH all_identities AS (
                SELECT owner_id FROM users WHERE owner_id IS NOT NULL AND TRIM(owner_id) <> ''
                UNION
                SELECT owner_id FROM tasks WHERE deleted_at IS NULL AND owner_id IS NOT NULL AND TRIM(owner_id) <> ''
            )
            SELECT
                (SELECT COUNT(*) FROM all_identities) AS total_users,
                (SELECT COUNT(*) FROM users WHERE owner_id IS NOT NULL AND TRIM(owner_id) <> '') AS registered_users
            """,
        )
        active_row = self._fetchone(
            """
            SELECT
                COUNT(DISTINCT CASE WHEN created_at >= ? THEN owner_id END) AS active_1d,
                COUNT(DISTINCT CASE WHEN created_at >= ? THEN owner_id END) AS active_7d,
                COUNT(DISTINCT CASE WHEN created_at >= ? THEN owner_id END) AS active_30d
            FROM tasks
            WHERE deleted_at IS NULL AND owner_id IS NOT NULL AND TRIM(owner_id) <> ''
            """,
            [cutoff_1d, cutoff_7d, cutoff_30d],
        )
        new_row = self._fetchone(
            """
            WITH identity_events AS (
                SELECT owner_id, created_at AS seen_at FROM users WHERE owner_id IS NOT NULL AND TRIM(owner_id) <> ''
                UNION ALL
                SELECT owner_id, created_at AS seen_at FROM tasks WHERE deleted_at IS NULL AND owner_id IS NOT NULL AND TRIM(owner_id) <> ''
            ),
            first_seen AS (
                SELECT owner_id, MIN(seen_at) AS first_seen_at FROM identity_events GROUP BY owner_id
            )
            SELECT
                SUM(CASE WHEN first_seen_at >= ? THEN 1 ELSE 0 END) AS new_1d,
                SUM(CASE WHEN first_seen_at >= ? THEN 1 ELSE 0 END) AS new_7d,
                SUM(CASE WHEN first_seen_at >= ? THEN 1 ELSE 0 END) AS new_30d
            FROM first_seen
            """,
            [cutoff_1d, cutoff_7d, cutoff_30d],
        )
        return {"userStats": {
            "totalUsers": int((overview_row or {}).get("total_users") or 0),
            "registeredUsers": int((overview_row or {}).get("registered_users") or 0),
            "activeUsers1d": int((active_row or {}).get("active_1d") or 0),
            "activeUsers7d": int((active_row or {}).get("active_7d") or 0),
            "activeUsers30d": int((active_row or {}).get("active_30d") or 0),
            "newUsers1d": int((new_row or {}).get("new_1d") or 0),
            "newUsers7d": int((new_row or {}).get("new_7d") or 0),
            "newUsers30d": int((new_row or {}).get("new_30d") or 0),
        }}

    def upsert_account_month_target(self, owner_id: str, year: int, month: int, target_count: int) -> AccountMonthTarget:
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
        row = self._fetchone("SELECT * FROM account_month_targets WHERE owner_id = ? AND year = ? AND month = ?", [owner_id, year, month])
        if row is None:
            raise RuntimeError("Failed to upsert account month target")
        return self._row_to_account_month_target(row)

    def get_account_month_target(self, owner_id: str, year: int, month: int) -> Optional[AccountMonthTarget]:
        row = self._fetchone("SELECT * FROM account_month_targets WHERE owner_id = ? AND year = ? AND month = ?", [owner_id, year, month])
        return self._row_to_account_month_target(row) if row else None

    def get_latest_account_month_target_before(self, owner_id: str, year: int, month: int) -> Optional[AccountMonthTarget]:
        row = self._fetchone(
            """
            SELECT * FROM account_month_targets
            WHERE owner_id = ? AND (year < ? OR (year = ? AND month < ?))
            ORDER BY year DESC, month DESC
            LIMIT 1
            """,
            [owner_id, year, year, month],
        )
        return self._row_to_account_month_target(row) if row else None

    def count_user_tasks_by_created_range(self, owner_id: str, start_iso: str, end_iso: str, task_type: Optional[str] = None) -> int:
        where = ["owner_id = ?", "created_at >= ?", "created_at < ?", "deleted_at IS NULL"]
        params: List[Any] = [owner_id, start_iso, end_iso]
        if task_type:
            where.append("task_type = ?")
            params.append(task_type)
        row = self._fetchone(f"SELECT COUNT(*) AS c FROM tasks WHERE {' AND '.join(where)}", params)
        return int(row["c"]) if row else 0

    def count_user_tasks_by_completed_range(self, owner_id: str, start_iso: str, end_iso: str, task_type: Optional[str] = None, status: Optional[str] = None) -> int:
        where = ["owner_id = ?", "completed_at IS NOT NULL", "completed_at >= ?", "completed_at < ?", "deleted_at IS NULL"]
        params: List[Any] = [owner_id, start_iso, end_iso]
        if task_type:
            where.append("task_type = ?")
            params.append(task_type)
        if status:
            where.append("status = ?")
            params.append(status)
        row = self._fetchone(f"SELECT COUNT(*) AS c FROM tasks WHERE {' AND '.join(where)}", params)
        return int(row["c"]) if row else 0

    def aggregate_user_created_tasks_daily(self, owner_id: str, start_day: date, end_day: date) -> List[Dict[str, Any]]:
        start_iso, end_iso = local_day_start_end_to_utc(start_day, day_count=(end_day - start_day).days + 1)
        rows = self._fetchall(
            """
            SELECT created_at, task_type
            FROM tasks
            WHERE owner_id = ? AND created_at >= ? AND created_at < ? AND deleted_at IS NULL
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
        return [{"day": day, "task_type": task_type, "count": count} for (day, task_type), count in sorted(bucket.items(), key=lambda item: (item[0][0], item[0][1]))]

    def aggregate_user_completed_tasks_daily(self, owner_id: str, start_day: date, end_day: date, task_type: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        start_iso, end_iso = local_day_start_end_to_utc(start_day, day_count=(end_day - start_day).days + 1)
        where = ["owner_id = ?", "completed_at IS NOT NULL", "completed_at >= ?", "completed_at < ?", "deleted_at IS NULL"]
        params: List[Any] = [owner_id, start_iso, end_iso]
        if task_type:
            where.append("task_type = ?")
            params.append(task_type)
        if status:
            where.append("status = ?")
            params.append(status)
        rows = self._fetchall(f"SELECT completed_at, task_type, status FROM tasks WHERE {' AND '.join(where)}", params)
        bucket: Dict[tuple[str, str, str], int] = {}
        for row in rows:
            day = utc_to_local_day(row.get("completed_at"), naive_strategy="utc")
            resolved_task_type = str(row.get("task_type") or "")
            resolved_status = str(row.get("status") or "")
            if not day or not resolved_task_type or not resolved_status:
                continue
            key = (day, resolved_task_type, resolved_status)
            bucket[key] = bucket.get(key, 0) + 1
        return [{"day": day, "task_type": resolved_task_type, "status": resolved_status, "count": count} for (day, resolved_task_type, resolved_status), count in sorted(bucket.items(), key=lambda item: (item[0][0], item[0][1], item[0][2]))]
