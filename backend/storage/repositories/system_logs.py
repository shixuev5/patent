"""System log repository methods."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.time_utils import utc_now_z


class SystemLogsRepositoryMixin:
    POLICY_CLEANUP_WHERE = (
        "(category = 'user_action' AND UPPER(COALESCE(method, '')) = 'GET') "
        "OR (category = 'user_action' AND success = 1 AND path LIKE '/api/internal/%') "
        "OR (category != 'llm_call' AND category != 'user_action' AND success = 1)"
    )

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
                payload["log_id"], payload["timestamp"], payload["category"], payload["event_name"], payload["level"],
                payload["owner_id"], payload["task_id"], payload["task_type"], payload["request_id"], payload["trace_id"],
                payload["method"], payload["path"], payload["status_code"], payload["duration_ms"],
                payload["provider"], payload["target_host"], payload["success"], payload["message"],
                payload["payload_inline_json"], payload["payload_file_path"], payload["payload_bytes"], payload["payload_overflow"], payload["created_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def get_system_log(self, log_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone("SELECT * FROM system_logs WHERE log_id = ?", [log_id])
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
            where.append("(sl.category LIKE ? OR sl.event_name LIKE ? OR sl.task_id LIKE ? OR sl.request_id LIKE ? OR sl.trace_id LIKE ? OR sl.message LIKE ? OR sl.path LIKE ? OR sl.provider LIKE ? OR u.name LIKE ?)")
            wildcard = f"%{q}%"
            params.extend([wildcard] * 9)
        where_clause = " AND ".join(where)
        total_row = self._fetchone(
            f"SELECT COUNT(*) AS c FROM system_logs sl LEFT JOIN users u ON sl.owner_id = u.owner_id WHERE {where_clause}",
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
        return {"total": int((total_row or {}).get("c") or 0), "items": [self._row_to_system_log(row) for row in rows]}

    def summarize_system_logs(self, *, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
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
            SELECT COUNT(*) AS total_logs,
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed_logs,
                   SUM(CASE WHEN category = 'llm_call' THEN 1 ELSE 0 END) AS llm_call_count
            FROM system_logs
            WHERE {where_clause}
            """,
            params,
        ) or {}
        category_rows = self._fetchall(
            f"SELECT category, COUNT(*) AS count FROM system_logs WHERE {where_clause} GROUP BY category ORDER BY count DESC",
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
            "byCategory": [{"category": row.get("category"), "count": int(row.get("count") or 0)} for row in category_rows],
        }

    def cleanup_system_logs_before(self, cutoff_iso: str) -> int:
        return self._changed_rows(self._request("DELETE FROM system_logs WHERE timestamp < ?", [cutoff_iso]))

    def list_system_log_payload_paths_for_policy_cleanup(self) -> List[str]:
        rows = self._fetchall(
            f"""
            SELECT payload_file_path
            FROM system_logs
            WHERE ({self.POLICY_CLEANUP_WHERE})
              AND payload_file_path IS NOT NULL
              AND payload_file_path != ''
            """
        )
        return [str(row.get("payload_file_path") or "").strip() for row in rows if str(row.get("payload_file_path") or "").strip()]

    def cleanup_system_logs_by_policy(self) -> int:
        return self._changed_rows(self._request(f"DELETE FROM system_logs WHERE {self.POLICY_CLEANUP_WHERE}"))
