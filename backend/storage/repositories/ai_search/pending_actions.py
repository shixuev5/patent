"""AI search pending action repository methods."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.time_utils import utc_now_z


_UNSET = object()


class AiSearchPendingActionsRepositoryMixin:
    def _row_to_ai_search_pending_action(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "action_id": row.get("action_id"),
            "task_id": row.get("task_id"),
            "run_id": row.get("run_id"),
            "plan_version": int(row["plan_version"]) if row.get("plan_version") is not None else None,
            "action_type": row.get("action_type"),
            "status": row.get("status"),
            "source": row.get("source"),
            "payload": self._parse_metadata(row.get("payload_json")),
            "resolution": self._parse_metadata(row.get("resolution_json")),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at") or row.get("created_at"),
            "resolved_at": row.get("resolved_at"),
            "superseded_by": row.get("superseded_by"),
        }

    def create_ai_search_pending_action(self, record: Dict[str, Any]) -> bool:
        created_at = str(record.get("created_at") or utc_now_z())
        payload = {
            "action_id": str(record.get("action_id") or "").strip(),
            "task_id": str(record.get("task_id") or "").strip(),
            "run_id": str(record.get("run_id") or "").strip() or None,
            "plan_version": int(record.get("plan_version") or 0) or None,
            "action_type": str(record.get("action_type") or "").strip(),
            "status": str(record.get("status") or "pending").strip() or "pending",
            "source": str(record.get("source") or "").strip() or None,
            "payload_json": self._encode_json_value(record.get("payload") or {}),
            "resolution_json": self._encode_json_value(record.get("resolution") or {}),
            "created_at": created_at,
            "updated_at": str(record.get("updated_at") or created_at),
            "resolved_at": record.get("resolved_at"),
            "superseded_by": str(record.get("superseded_by") or "").strip() or None,
        }
        if not payload["action_id"] or not payload["task_id"] or not payload["action_type"]:
            return False
        return self._changed_rows(
            self._request(
                """
            INSERT INTO ai_search_pending_actions (
                action_id, task_id, run_id, plan_version, action_type, status, source,
                payload_json, resolution_json, created_at, updated_at, resolved_at, superseded_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    payload["action_id"],
                    payload["task_id"],
                    payload["run_id"],
                    payload["plan_version"],
                    payload["action_type"],
                    payload["status"],
                    payload["source"],
                    payload["payload_json"],
                    payload["resolution_json"],
                    payload["created_at"],
                    payload["updated_at"],
                    payload["resolved_at"],
                    payload["superseded_by"],
                ],
            )
        ) > 0

    def get_ai_search_pending_action(
        self, task_id: str, action_type: str, *, status: str = "pending"
    ) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT * FROM ai_search_pending_actions WHERE task_id = ? AND action_type = ? AND status = ? ORDER BY created_at DESC, action_id DESC LIMIT 1",
            [task_id, action_type, status],
        )
        return self._row_to_ai_search_pending_action(row) if row else None

    def get_ai_search_pending_action_by_id(self, action_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone("SELECT * FROM ai_search_pending_actions WHERE action_id = ? LIMIT 1", [action_id])
        return self._row_to_ai_search_pending_action(row) if row else None

    def get_current_ai_search_pending_action(
        self, task_id: str, *, statuses: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        active_statuses = [
            str(item or "").strip() for item in (statuses or ["pending"]) if str(item or "").strip()
        ]
        if not active_statuses:
            return None
        placeholders = ", ".join("?" for _ in active_statuses)
        row = self._fetchone(
            f"SELECT * FROM ai_search_pending_actions WHERE task_id = ? AND status IN ({placeholders}) ORDER BY created_at DESC, action_id DESC LIMIT 1",
            [task_id, *active_statuses],
        )
        return self._row_to_ai_search_pending_action(row) if row else None

    def update_ai_search_pending_action(
        self,
        action_id: str,
        *,
        status: Any = _UNSET,
        payload: Any = _UNSET,
        resolution: Any = _UNSET,
        run_id: Any = _UNSET,
        plan_version: Any = _UNSET,
        source: Any = _UNSET,
        resolved_at: Any = _UNSET,
        superseded_by: Any = _UNSET,
    ) -> bool:
        assignments: List[str] = ["updated_at = ?"]
        params: List[Any] = [utc_now_z()]
        if status is not _UNSET:
            assignments.append("status = ?")
            params.append(str(status or "").strip())
        if payload is not _UNSET:
            assignments.append("payload_json = ?")
            params.append(self._encode_json_value(payload or {}))
        if resolution is not _UNSET:
            assignments.append("resolution_json = ?")
            params.append(self._encode_json_value(resolution or {}))
        if run_id is not _UNSET:
            assignments.append("run_id = ?")
            params.append(str(run_id or "").strip() or None)
        if plan_version is not _UNSET:
            assignments.append("plan_version = ?")
            params.append(int(plan_version or 0) or None)
        if source is not _UNSET:
            assignments.append("source = ?")
            params.append(str(source or "").strip() or None)
        if resolved_at is not _UNSET:
            assignments.append("resolved_at = ?")
            params.append(resolved_at)
        if superseded_by is not _UNSET:
            assignments.append("superseded_by = ?")
            params.append(str(superseded_by or "").strip() or None)
        params.append(action_id)
        result = self._request(
            f"UPDATE ai_search_pending_actions SET {', '.join(assignments)} WHERE action_id = ?",
            params,
        )
        return self._changed_rows(result) > 0

    def resolve_ai_search_pending_action(self, action_id: str, *, status: str = "resolved") -> bool:
        return self.update_ai_search_pending_action(action_id, status=status, resolved_at=utc_now_z())
