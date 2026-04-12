"""AI search checkpoint repository methods."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.time_utils import utc_now_z


class AiSearchCheckpointsRepositoryMixin:
    def _row_to_ai_search_checkpoint(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "thread_id": row.get("thread_id"),
            "checkpoint_ns": row.get("checkpoint_ns"),
            "checkpoint_id": row.get("checkpoint_id"),
            "checkpoint_json": row.get("checkpoint_json"),
            "metadata_json": row.get("metadata_json"),
            "parent_checkpoint_id": row.get("parent_checkpoint_id"),
            "created_at": row.get("created_at"),
        }

    def put_ai_search_checkpoint(self, record: Dict[str, Any]) -> bool:
        payload = {
            "thread_id": str(record.get("thread_id", "")).strip(),
            "checkpoint_ns": str(record.get("checkpoint_ns", "")).strip(),
            "checkpoint_id": str(record.get("checkpoint_id", "")).strip(),
            "checkpoint_json": str(record.get("checkpoint_json", "")).strip(),
            "metadata_json": str(record.get("metadata_json", "")).strip(),
            "parent_checkpoint_id": str(record.get("parent_checkpoint_id", "")).strip() or None,
            "created_at": str(record.get("created_at") or utc_now_z()),
        }
        if (
            not payload["thread_id"]
            or not payload["checkpoint_id"]
            or not payload["checkpoint_json"]
            or not payload["metadata_json"]
        ):
            return False
        return self._changed_rows(
            self._request(
                """
            INSERT INTO ai_search_checkpoints (
                thread_id, checkpoint_ns, checkpoint_id, checkpoint_json,
                metadata_json, parent_checkpoint_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_id, checkpoint_ns, checkpoint_id) DO UPDATE SET
                checkpoint_json = excluded.checkpoint_json,
                metadata_json = excluded.metadata_json,
                parent_checkpoint_id = excluded.parent_checkpoint_id,
                created_at = COALESCE(ai_search_checkpoints.created_at, excluded.created_at)
            """,
                [
                    payload["thread_id"],
                    payload["checkpoint_ns"],
                    payload["checkpoint_id"],
                    payload["checkpoint_json"],
                    payload["metadata_json"],
                    payload["parent_checkpoint_id"],
                    payload["created_at"],
                ],
            )
        ) > 0

    def get_ai_search_checkpoint(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if checkpoint_id:
            row = self._fetchone(
                "SELECT * FROM ai_search_checkpoints WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ? LIMIT 1",
                [thread_id, checkpoint_ns, checkpoint_id],
            )
        else:
            row = self._fetchone(
                "SELECT * FROM ai_search_checkpoints WHERE thread_id = ? AND checkpoint_ns = ? ORDER BY checkpoint_id DESC LIMIT 1",
                [thread_id, checkpoint_ns],
            )
        return self._row_to_ai_search_checkpoint(row) if row else None

    def list_ai_search_checkpoints(
        self,
        thread_id: str,
        checkpoint_ns: Optional[str] = None,
        checkpoint_id: Optional[str] = None,
        before_checkpoint_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        where = ["thread_id = ?"]
        params: List[Any] = [thread_id]
        if checkpoint_ns is not None:
            where.append("checkpoint_ns = ?")
            params.append(checkpoint_ns)
        if checkpoint_id is not None:
            where.append("checkpoint_id = ?")
            params.append(checkpoint_id)
        if before_checkpoint_id:
            where.append("checkpoint_id < ?")
            params.append(before_checkpoint_id)
        sql = f"SELECT * FROM ai_search_checkpoints WHERE {' AND '.join(where)} ORDER BY checkpoint_id DESC"
        if limit is not None:
            sql += f" LIMIT {max(1, int(limit))}"
        rows = self._fetchall(sql, params)
        return [self._row_to_ai_search_checkpoint(row) for row in rows]

    def put_ai_search_checkpoint_blobs(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 0
        changed = 0
        for record in records:
            thread_id = str(record.get("thread_id", "")).strip()
            checkpoint_ns = str(record.get("checkpoint_ns", "")).strip()
            channel = str(record.get("channel", "")).strip()
            version = str(record.get("version", "")).strip()
            typed_value_json = str(record.get("typed_value_json", "")).strip()
            if not thread_id or not channel or not version or not typed_value_json:
                continue
            changed += self._changed_rows(
                self._request(
                    """
                INSERT INTO ai_search_checkpoint_blobs (
                    thread_id, checkpoint_ns, channel, version, typed_value_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(thread_id, checkpoint_ns, channel, version) DO UPDATE SET
                    typed_value_json = excluded.typed_value_json
                """,
                    [thread_id, checkpoint_ns, channel, version, typed_value_json],
                )
            )
        return changed

    def get_ai_search_checkpoint_blobs(
        self, thread_id: str, checkpoint_ns: str, versions: Dict[str, Any]
    ) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for channel, version in (versions or {}).items():
            row = self._fetchone(
                "SELECT typed_value_json FROM ai_search_checkpoint_blobs WHERE thread_id = ? AND checkpoint_ns = ? AND channel = ? AND version = ? LIMIT 1",
                [thread_id, checkpoint_ns, str(channel), str(version)],
            )
            if row and row.get("typed_value_json"):
                result[str(channel)] = str(row["typed_value_json"])
        return result

    def put_ai_search_checkpoint_writes(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 0
        changed = 0
        for record in records:
            thread_id = str(record.get("thread_id", "")).strip()
            checkpoint_ns = str(record.get("checkpoint_ns", "")).strip()
            checkpoint_id = str(record.get("checkpoint_id", "")).strip()
            task_id = str(record.get("task_id", "")).strip()
            channel = str(record.get("channel", "")).strip()
            typed_value_json = str(record.get("typed_value_json", "")).strip()
            write_idx = int(record.get("write_idx") or 0)
            task_path = str(record.get("task_path", "")).strip()
            if not thread_id or not checkpoint_id or not task_id or not channel or not typed_value_json:
                continue
            changed += self._changed_rows(
                self._request(
                    """
                INSERT OR IGNORE INTO ai_search_checkpoint_writes (
                    thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx,
                    channel, typed_value_json, task_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    [thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx, channel, typed_value_json, task_path],
                )
            )
        return changed

    def list_ai_search_checkpoint_writes(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM ai_search_checkpoint_writes WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ? ORDER BY task_id ASC, write_idx ASC",
            [thread_id, checkpoint_ns, checkpoint_id],
        )
        return [
            {
                "thread_id": row.get("thread_id"),
                "checkpoint_ns": row.get("checkpoint_ns"),
                "checkpoint_id": row.get("checkpoint_id"),
                "task_id": row.get("task_id"),
                "write_idx": int(row.get("write_idx") or 0),
                "channel": row.get("channel"),
                "typed_value_json": row.get("typed_value_json"),
                "task_path": row.get("task_path"),
            }
            for row in rows
        ]

    def delete_ai_search_thread_checkpoints(self, thread_id: str) -> bool:
        changed = 0
        for table_name in (
            "ai_search_checkpoints",
            "ai_search_checkpoint_writes",
            "ai_search_checkpoint_blobs",
        ):
            changed += self._changed_rows(
                self._request(f"DELETE FROM {table_name} WHERE thread_id = ?", [thread_id])
            )
        return changed > 0
