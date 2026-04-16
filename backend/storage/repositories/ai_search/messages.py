"""AI search message and plan repository methods."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.time_utils import utc_now_z


class AiSearchMessagesPlansRepositoryMixin:
    def _row_to_ai_search_message(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "message_id": row.get("message_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]) if row.get("plan_version") is not None else None,
            "role": row.get("role"),
            "kind": row.get("kind"),
            "content": row.get("content"),
            "stream_status": row.get("stream_status"),
            "question_id": row.get("question_id"),
            "metadata": self._parse_metadata(row.get("metadata")),
            "created_at": row.get("created_at"),
        }

    def _row_to_ai_search_stream_event(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "seq": int(row.get("seq") or 0),
            "event_id": row.get("event_id"),
            "session_id": row.get("session_id"),
            "task_id": row.get("task_id"),
            "run_id": row.get("run_id"),
            "event_type": row.get("event_type"),
            "entity_id": row.get("entity_id"),
            "payload": self._parse_metadata(row.get("payload_json")),
            "created_at": row.get("created_at"),
        }

    def create_ai_search_message(self, record: Dict[str, Any]) -> bool:
        payload = {
            "message_id": str(record.get("message_id", "")).strip(),
            "task_id": str(record.get("task_id", "")).strip(),
            "plan_version": int(record["plan_version"]) if record.get("plan_version") is not None else None,
            "role": str(record.get("role", "")).strip(),
            "kind": str(record.get("kind", "")).strip(),
            "content": record.get("content"),
            "stream_status": str(record.get("stream_status", "")).strip() or None,
            "question_id": str(record.get("question_id", "")).strip() or None,
            "metadata": self._encode_json_value(record.get("metadata") or {}),
            "created_at": str(record.get("created_at") or utc_now_z()),
        }
        if not payload["message_id"] or not payload["task_id"] or not payload["role"] or not payload["kind"]:
            return False
        return self._changed_rows(
            self._request(
                """
            INSERT INTO ai_search_messages (
                message_id, task_id, plan_version, role, kind, content,
                stream_status, question_id, metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    payload["message_id"],
                    payload["task_id"],
                    payload["plan_version"],
                    payload["role"],
                    payload["kind"],
                    payload["content"],
                    payload["stream_status"],
                    payload["question_id"],
                    payload["metadata"],
                    payload["created_at"],
                ],
            )
        ) > 0

    def get_ai_search_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone("SELECT * FROM ai_search_messages WHERE message_id = ? LIMIT 1", [message_id])
        return self._row_to_ai_search_message(row) if row else None

    def update_ai_search_message(self, message_id: str, **kwargs) -> bool:
        allowed_fields = {
            "plan_version",
            "content",
            "stream_status",
            "question_id",
            "metadata",
            "created_at",
        }
        updates = {key: kwargs[key] for key in kwargs if key in allowed_fields}
        if not updates:
            return False
        if "metadata" in updates:
            updates["metadata"] = self._encode_json_value(updates.get("metadata") or {})
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        result = self._request(
            f"UPDATE ai_search_messages SET {set_clause} WHERE message_id = ?",
            [*updates.values(), str(message_id or "").strip()],
        )
        return self._changed_rows(result) > 0

    def list_ai_search_messages(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM ai_search_messages WHERE task_id = ? ORDER BY created_at ASC, message_id ASC",
            [task_id],
        )
        return [self._row_to_ai_search_message(row) for row in rows]

    def append_ai_search_stream_event(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        payload = {
            "event_id": str(record.get("event_id") or "").strip(),
            "session_id": str(record.get("session_id") or record.get("task_id") or "").strip(),
            "task_id": str(record.get("task_id") or record.get("session_id") or "").strip(),
            "run_id": str(record.get("run_id") or "").strip() or None,
            "event_type": str(record.get("event_type") or "").strip(),
            "entity_id": str(record.get("entity_id") or "").strip() or None,
            "payload_json": self._encode_json_value(record.get("payload") or {}),
            "created_at": str(record.get("created_at") or utc_now_z()),
        }
        if not payload["event_id"] or not payload["session_id"] or not payload["task_id"] or not payload["event_type"]:
            return None
        self._request(
            """
            INSERT INTO ai_search_stream_events (
                event_id, session_id, task_id, run_id, event_type, entity_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                payload["event_id"],
                payload["session_id"],
                payload["task_id"],
                payload["run_id"],
                payload["event_type"],
                payload["entity_id"],
                payload["payload_json"],
                payload["created_at"],
            ],
        )
        row = self._fetchone("SELECT * FROM ai_search_stream_events WHERE event_id = ? LIMIT 1", [payload["event_id"]])
        return self._row_to_ai_search_stream_event(row) if row else None

    def list_ai_search_stream_events(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        clauses = ["session_id = ?", "seq > ?"]
        params: List[Any] = [str(session_id or "").strip(), max(int(after_seq or 0), 0)]
        sql = f"SELECT * FROM ai_search_stream_events WHERE {' AND '.join(clauses)} ORDER BY seq ASC"
        if limit is not None and int(limit) > 0:
            sql = f"{sql} LIMIT ?"
            params.append(int(limit))
        rows = self._fetchall(sql, params)
        return [self._row_to_ai_search_stream_event(row) for row in rows]

    def get_latest_ai_search_stream_event(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT * FROM ai_search_stream_events WHERE session_id = ? ORDER BY seq DESC LIMIT 1",
            [str(session_id or "").strip()],
        )
        return self._row_to_ai_search_stream_event(row) if row else None

    def _row_to_ai_search_plan(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "status": row.get("status"),
            "review_markdown": str(row.get("review_markdown") or ""),
            "execution_spec_json": self._parse_metadata(row.get("execution_spec_json")),
            "created_at": row.get("created_at"),
            "confirmed_at": row.get("confirmed_at"),
            "superseded_at": row.get("superseded_at"),
        }

    def get_next_ai_search_plan_version(self, task_id: str) -> int:
        row = self._fetchone(
            "SELECT COALESCE(MAX(plan_version), 0) + 1 AS next_version FROM ai_search_plans WHERE task_id = ?",
            [task_id],
        )
        return int(row.get("next_version") or 1) if row else 1

    def create_ai_search_plan(self, record: Dict[str, Any]) -> bool:
        payload = {
            "task_id": str(record.get("task_id", "")).strip(),
            "plan_version": int(record.get("plan_version") or 0),
            "status": str(record.get("status", "")).strip(),
            "review_markdown": str(record.get("review_markdown") or "").strip(),
            "execution_spec_json": self._encode_json_value(record.get("execution_spec_json") or {}),
            "created_at": str(record.get("created_at") or utc_now_z()),
            "confirmed_at": str(record.get("confirmed_at") or "").strip() or None,
            "superseded_at": str(record.get("superseded_at") or "").strip() or None,
        }
        if (
            not payload["task_id"]
            or payload["plan_version"] <= 0
            or not payload["status"]
            or not payload["review_markdown"]
        ):
            return False
        return self._changed_rows(
            self._request(
                """
            INSERT INTO ai_search_plans (
                task_id, plan_version, status, review_markdown, execution_spec_json,
                created_at, confirmed_at, superseded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    payload["task_id"],
                    payload["plan_version"],
                    payload["status"],
                    payload["review_markdown"],
                    payload["execution_spec_json"],
                    payload["created_at"],
                    payload["confirmed_at"],
                    payload["superseded_at"],
                ],
            )
        ) > 0

    def update_ai_search_plan(self, task_id: str, plan_version: int, **kwargs) -> bool:
        allowed_fields = {
            "status",
            "review_markdown",
            "execution_spec_json",
            "confirmed_at",
            "superseded_at",
        }
        updates = {key: kwargs[key] for key in kwargs if key in allowed_fields}
        if not updates:
            return False
        for key in list(updates.keys()):
            if key.endswith("_json"):
                updates[key] = self._encode_json_value(updates[key])
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        result = self._request(
            f"UPDATE ai_search_plans SET {set_clause} WHERE task_id = ? AND plan_version = ?",
            [*updates.values(), task_id, int(plan_version)],
        )
        return self._changed_rows(result) > 0

    def get_ai_search_plan(
        self, task_id: str, plan_version: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        if plan_version is None:
            row = self._fetchone(
                "SELECT * FROM ai_search_plans WHERE task_id = ? ORDER BY plan_version DESC LIMIT 1",
                [task_id],
            )
        else:
            row = self._fetchone(
                "SELECT * FROM ai_search_plans WHERE task_id = ? AND plan_version = ?",
                [task_id, int(plan_version)],
            )
        return self._row_to_ai_search_plan(row) if row else None

    def list_ai_search_plans(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM ai_search_plans WHERE task_id = ? ORDER BY plan_version DESC",
            [task_id],
        )
        return [self._row_to_ai_search_plan(row) for row in rows]
