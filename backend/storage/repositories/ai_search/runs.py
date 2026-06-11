"""AI search run lifecycle repository methods."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.time_utils import utc_now_z


class AiSearchRunsRepositoryMixin:
    def _row_to_ai_search_run(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "run_id": row.get("run_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "phase": row.get("phase"),
            "status": row.get("status"),
            "selected_document_count": int(row.get("selected_document_count") or 0),
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
            "selected_document_count": int(record.get("selected_document_count") or 0),
            "created_at": str(record.get("created_at") or utc_now_z()),
            "updated_at": str(record.get("updated_at") or utc_now_z()),
            "completed_at": record.get("completed_at"),
        }
        if (
            not payload["run_id"]
            or not payload["task_id"]
            or payload["plan_version"] <= 0
            or not payload["phase"]
            or not payload["status"]
        ):
            return False
        return self._changed_rows(
            self._request(
                """
            INSERT INTO ai_search_runs (
                run_id, task_id, plan_version, phase, status,
                selected_document_count, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    payload["run_id"],
                    payload["task_id"],
                    payload["plan_version"],
                    payload["phase"],
                    payload["status"],
                    payload["selected_document_count"],
                    payload["created_at"],
                    payload["updated_at"],
                    payload["completed_at"],
                ],
            )
        ) > 0

    def get_ai_search_run(
        self,
        task_id: str,
        run_id: Optional[str] = None,
        *,
        plan_version: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        if run_id:
            row = self._fetchone(
                "SELECT * FROM ai_search_runs WHERE task_id = ? AND run_id = ? LIMIT 1",
                [task_id, str(run_id)],
            )
        elif plan_version is not None:
            row = self._fetchone(
                "SELECT * FROM ai_search_runs WHERE task_id = ? AND plan_version = ? ORDER BY updated_at DESC, run_id DESC LIMIT 1",
                [task_id, int(plan_version)],
            )
        else:
            row = self._fetchone(
                "SELECT * FROM ai_search_runs WHERE task_id = ? ORDER BY updated_at DESC, run_id DESC LIMIT 1",
                [task_id],
            )
        return self._row_to_ai_search_run(row) if row else None

    def list_ai_search_runs(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM ai_search_runs WHERE task_id = ? ORDER BY updated_at DESC, run_id DESC",
            [task_id],
        )
        return [self._row_to_ai_search_run(row) for row in rows]

    def update_ai_search_run(self, task_id: str, run_id: str, **kwargs: Any) -> bool:
        allowed_fields = {
            "phase",
            "status",
            "selected_document_count",
            "updated_at",
            "completed_at",
        }
        updates = {key: kwargs[key] for key in kwargs if key in allowed_fields}
        if not updates:
            return False
        updates.setdefault("updated_at", utc_now_z())
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        return self._changed_rows(
            self._request(
                f"UPDATE ai_search_runs SET {set_clause} WHERE task_id = ? AND run_id = ?",
                [*updates.values(), task_id, str(run_id)],
            )
        ) > 0
