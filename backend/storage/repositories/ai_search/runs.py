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
            "active_retrieval_todo_id": row.get("active_retrieval_todo_id"),
            "active_batch_id": row.get("active_batch_id"),
            "selected_document_count": int(row.get("selected_document_count") or 0),
            "human_decision_state": self._parse_metadata(row.get("human_decision_state")),
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
            "active_retrieval_todo_id": str(record.get("active_retrieval_todo_id") or "").strip() or None,
            "active_batch_id": str(record.get("active_batch_id") or "").strip() or None,
            "selected_document_count": int(record.get("selected_document_count") or 0),
            "human_decision_state": self._encode_json_value(record.get("human_decision_state") or {}),
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
                active_retrieval_todo_id, active_batch_id, selected_document_count,
                human_decision_state, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    payload["run_id"],
                    payload["task_id"],
                    payload["plan_version"],
                    payload["phase"],
                    payload["status"],
                    payload["active_retrieval_todo_id"],
                    payload["active_batch_id"],
                    payload["selected_document_count"],
                    payload["human_decision_state"],
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

    def update_ai_search_run(self, task_id: str, run_id: str, **kwargs) -> bool:
        allowed_fields = {
            "phase",
            "status",
            "active_retrieval_todo_id",
            "active_batch_id",
            "selected_document_count",
            "human_decision_state",
            "updated_at",
            "completed_at",
        }
        updates = {key: kwargs[key] for key in kwargs if key in allowed_fields}
        if not updates:
            return False
        updates.setdefault("updated_at", utc_now_z())
        if "human_decision_state" in updates:
            updates["human_decision_state"] = self._encode_json_value(
                updates["human_decision_state"] or {}
            )
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        return self._changed_rows(
            self._request(
                f"UPDATE ai_search_runs SET {set_clause} WHERE task_id = ? AND run_id = ?",
                [*updates.values(), task_id, str(run_id)],
            )
        ) > 0

    def _row_to_ai_search_retrieval_todo(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "todo_id": row.get("todo_id"),
            "run_id": row.get("run_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "sub_plan_id": row.get("sub_plan_id"),
            "step_id": row.get("step_id"),
            "title": row.get("title"),
            "description": row.get("description"),
            "status": row.get("status"),
            "attempt_count": int(row.get("attempt_count") or 0),
            "last_error": row.get("last_error") or "",
            "resume_from": row.get("resume_from") or "",
            "state": self._parse_metadata(row.get("state_json")),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def replace_ai_search_retrieval_todos(
        self, run_id: str, task_id: str, plan_version: int, todos: List[Dict[str, Any]]
    ) -> int:
        now = utc_now_z()
        self._request("DELETE FROM ai_search_retrieval_todos WHERE run_id = ?", [run_id])
        changed = 0
        for item in todos:
            todo_id = str(item.get("todo_id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not todo_id or not title:
                continue
            changed += self._changed_rows(
                self._request(
                    """
                INSERT INTO ai_search_retrieval_todos (
                    todo_id, run_id, task_id, plan_version, sub_plan_id, step_id,
                    title, description, status, attempt_count, last_error, resume_from,
                    state_json, started_at, completed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    [
                        todo_id,
                        run_id,
                        task_id,
                        int(plan_version),
                        str(item.get("sub_plan_id") or "").strip() or None,
                        str(item.get("step_id") or "").strip() or None,
                        title,
                        str(item.get("description") or "").strip(),
                        str(item.get("status") or "pending").strip() or "pending",
                        int(item.get("attempt_count") or 0),
                        str(item.get("last_error") or "").strip() or None,
                        str(item.get("resume_from") or "").strip() or None,
                        self._encode_json_value(item.get("state") or {}),
                        item.get("started_at"),
                        item.get("completed_at"),
                        str(item.get("created_at") or now),
                        str(item.get("updated_at") or now),
                    ],
                )
            )
        return changed

    def list_ai_search_retrieval_todos(self, run_id: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            "SELECT * FROM ai_search_retrieval_todos WHERE run_id = ? ORDER BY created_at ASC, todo_id ASC",
            [run_id],
        )
        return [self._row_to_ai_search_retrieval_todo(row) for row in rows]

    def get_ai_search_retrieval_todo(self, run_id: str, todo_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT * FROM ai_search_retrieval_todos WHERE run_id = ? AND todo_id = ? LIMIT 1",
            [run_id, todo_id],
        )
        return self._row_to_ai_search_retrieval_todo(row) if row else None

    def update_ai_search_retrieval_todo(self, run_id: str, todo_id: str, **kwargs) -> bool:
        allowed_fields = {
            "status",
            "attempt_count",
            "last_error",
            "resume_from",
            "state_json",
            "started_at",
            "completed_at",
            "updated_at",
        }
        updates: Dict[str, Any] = {}
        for key, value in kwargs.items():
            if key not in allowed_fields:
                continue
            updates[key] = self._encode_json_value(value or {}) if key == "state_json" else value
        if not updates:
            return False
        updates.setdefault("updated_at", utc_now_z())
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        return self._changed_rows(
            self._request(
                f"UPDATE ai_search_retrieval_todos SET {set_clause} WHERE run_id = ? AND todo_id = ?",
                [*updates.values(), run_id, todo_id],
            )
        ) > 0

    def _row_to_ai_search_execution_summary(self, row: Dict[str, Any]) -> Dict[str, Any]:
        metadata = self._parse_metadata(row.get("metadata_json"))
        outcome_signals = metadata.get("outcome_signals") if isinstance(metadata, dict) else {}
        return {
            "summary_id": row.get("summary_id"),
            "run_id": row.get("run_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "todo_id": row.get("todo_id"),
            "step_id": row.get("step_id"),
            "sub_plan_id": row.get("sub_plan_id"),
            "result_summary": row.get("result_summary") or "",
            "adjustments": self._parse_metadata(row.get("adjustments_json")),
            "plan_change_assessment": self._parse_metadata(
                row.get("plan_change_assessment_json")
            ),
            "next_recommendation": row.get("next_recommendation") or "",
            "candidate_pool_size": int(row.get("candidate_pool_size") or 0),
            "new_unique_candidates": int(row.get("new_unique_candidates") or 0),
            "metadata": metadata,
            "outcome_signals": outcome_signals if isinstance(outcome_signals, dict) else {},
            "created_at": row.get("created_at"),
        }

    def create_ai_search_execution_summary(self, record: Dict[str, Any]) -> bool:
        payload = {
            "summary_id": str(record.get("summary_id") or "").strip(),
            "run_id": str(record.get("run_id") or "").strip(),
            "task_id": str(record.get("task_id") or "").strip(),
            "plan_version": int(record.get("plan_version") or 0),
            "todo_id": str(record.get("todo_id") or "").strip(),
            "step_id": str(record.get("step_id") or "").strip() or None,
            "sub_plan_id": str(record.get("sub_plan_id") or "").strip() or None,
            "result_summary": str(record.get("result_summary") or "").strip() or None,
            "adjustments_json": self._encode_json_value(record.get("adjustments") or []),
            "plan_change_assessment_json": self._encode_json_value(
                record.get("plan_change_assessment") or {}
            ),
            "next_recommendation": str(record.get("next_recommendation") or "").strip() or None,
            "candidate_pool_size": int(record.get("candidate_pool_size") or 0),
            "new_unique_candidates": int(record.get("new_unique_candidates") or 0),
            "metadata_json": self._encode_json_value(record.get("metadata") or {}),
            "created_at": str(record.get("created_at") or utc_now_z()),
        }
        if (
            not payload["summary_id"]
            or not payload["run_id"]
            or not payload["task_id"]
            or payload["plan_version"] <= 0
            or not payload["todo_id"]
        ):
            return False
        return self._changed_rows(
            self._request(
                """
            INSERT INTO ai_search_execution_summaries (
                summary_id, run_id, task_id, plan_version, todo_id, step_id, sub_plan_id,
                result_summary, adjustments_json, plan_change_assessment_json, next_recommendation,
                candidate_pool_size, new_unique_candidates, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    payload["summary_id"],
                    payload["run_id"],
                    payload["task_id"],
                    payload["plan_version"],
                    payload["todo_id"],
                    payload["step_id"],
                    payload["sub_plan_id"],
                    payload["result_summary"],
                    payload["adjustments_json"],
                    payload["plan_change_assessment_json"],
                    payload["next_recommendation"],
                    payload["candidate_pool_size"],
                    payload["new_unique_candidates"],
                    payload["metadata_json"],
                    payload["created_at"],
                ],
            )
        ) > 0

    def list_ai_search_execution_summaries(
        self, run_id: str, *, sub_plan_id: str = ""
    ) -> List[Dict[str, Any]]:
        where = ["run_id = ?"]
        params: List[Any] = [run_id]
        if sub_plan_id:
            where.append("sub_plan_id = ?")
            params.append(sub_plan_id)
        rows = self._fetchall(
            f"SELECT * FROM ai_search_execution_summaries WHERE {' AND '.join(where)} ORDER BY created_at ASC, summary_id ASC",
            params,
        )
        return [self._row_to_ai_search_execution_summary(row) for row in rows]

    def _row_to_ai_search_batch(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "batch_id": row.get("batch_id"),
            "run_id": row.get("run_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "batch_type": row.get("batch_type"),
            "status": row.get("status"),
            "workspace_dir": row.get("workspace_dir"),
            "input_hash": row.get("input_hash"),
            "loaded_at": row.get("loaded_at"),
            "committed_at": row.get("committed_at"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def create_ai_search_batch(self, record: Dict[str, Any]) -> bool:
        payload = {
            "batch_id": str(record.get("batch_id") or "").strip(),
            "run_id": str(record.get("run_id") or "").strip(),
            "task_id": str(record.get("task_id") or "").strip(),
            "plan_version": int(record.get("plan_version") or 0),
            "batch_type": str(record.get("batch_type") or "").strip(),
            "status": str(record.get("status") or "").strip(),
            "workspace_dir": str(record.get("workspace_dir") or "").strip() or None,
            "input_hash": str(record.get("input_hash") or "").strip() or None,
            "loaded_at": str(record.get("loaded_at") or utc_now_z()),
            "committed_at": record.get("committed_at"),
            "created_at": str(record.get("created_at") or utc_now_z()),
            "updated_at": str(record.get("updated_at") or utc_now_z()),
        }
        if (
            not payload["batch_id"]
            or not payload["run_id"]
            or not payload["task_id"]
            or payload["plan_version"] <= 0
            or not payload["batch_type"]
            or not payload["status"]
        ):
            return False
        return self._changed_rows(
            self._request(
                """
            INSERT INTO ai_search_batches (
                batch_id, run_id, task_id, plan_version, batch_type, status,
                workspace_dir, input_hash, loaded_at, committed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    payload["batch_id"],
                    payload["run_id"],
                    payload["task_id"],
                    payload["plan_version"],
                    payload["batch_type"],
                    payload["status"],
                    payload["workspace_dir"],
                    payload["input_hash"],
                    payload["loaded_at"],
                    payload["committed_at"],
                    payload["created_at"],
                    payload["updated_at"],
                ],
            )
        ) > 0

    def get_ai_search_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone("SELECT * FROM ai_search_batches WHERE batch_id = ? LIMIT 1", [batch_id])
        return self._row_to_ai_search_batch(row) if row else None

    def get_latest_ai_search_batch(self, run_id: str, batch_type: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT * FROM ai_search_batches WHERE run_id = ? AND batch_type = ? ORDER BY created_at DESC, batch_id DESC LIMIT 1",
            [run_id, batch_type],
        )
        return self._row_to_ai_search_batch(row) if row else None

    def update_ai_search_batch(self, batch_id: str, **kwargs) -> bool:
        allowed_fields = {
            "status",
            "workspace_dir",
            "input_hash",
            "loaded_at",
            "committed_at",
            "updated_at",
        }
        updates = {key: kwargs[key] for key in kwargs if key in allowed_fields}
        if not updates:
            return False
        updates.setdefault("updated_at", utc_now_z())
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        return self._changed_rows(
            self._request(
                f"UPDATE ai_search_batches SET {set_clause} WHERE batch_id = ?",
                [*updates.values(), batch_id],
            )
        ) > 0

    def replace_ai_search_batch_documents(
        self, batch_id: str, run_id: str, document_ids: List[str]
    ) -> int:
        self._request("DELETE FROM ai_search_batch_documents WHERE batch_id = ?", [batch_id])
        changed = 0
        for index, document_id in enumerate(document_ids, start=1):
            doc_id = str(document_id or "").strip()
            if not doc_id:
                continue
            changed += self._changed_rows(
                self._request(
                    "INSERT INTO ai_search_batch_documents (batch_id, run_id, document_id, ord) VALUES (?, ?, ?, ?)",
                    [batch_id, run_id, doc_id, index],
                )
            )
        return changed

    def list_ai_search_batch_documents(self, batch_id: str) -> List[str]:
        rows = self._fetchall(
            "SELECT document_id FROM ai_search_batch_documents WHERE batch_id = ? ORDER BY ord ASC, document_id ASC",
            [batch_id],
        )
        return [
            str(row.get("document_id") or "").strip()
            for row in rows
            if str(row.get("document_id") or "").strip()
        ]

    def _row_to_ai_search_execution_queue_message(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "queue_message_id": row.get("queue_message_id"),
            "task_id": row.get("task_id"),
            "run_id": row.get("run_id"),
            "content": row.get("content"),
            "ordinal": int(row.get("ordinal") or 0),
            "status": row.get("status"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "consumed_at": row.get("consumed_at"),
        }

    def get_next_ai_search_execution_queue_ordinal(self, task_id: str, run_id: str) -> int:
        row = self._fetchone(
            """
            SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal
            FROM ai_search_execution_message_queue
            WHERE task_id = ? AND run_id = ?
            """,
            [task_id, run_id],
        )
        return int(row.get("next_ordinal") or 1) if row else 1

    def create_ai_search_execution_queue_message(self, record: Dict[str, Any]) -> bool:
        payload = {
            "queue_message_id": str(record.get("queue_message_id") or "").strip(),
            "task_id": str(record.get("task_id") or "").strip(),
            "run_id": str(record.get("run_id") or "").strip(),
            "content": str(record.get("content") or "").strip(),
            "ordinal": int(record.get("ordinal") or 0),
            "status": str(record.get("status") or "pending").strip() or "pending",
            "created_at": str(record.get("created_at") or utc_now_z()),
            "updated_at": str(record.get("updated_at") or utc_now_z()),
            "consumed_at": record.get("consumed_at"),
        }
        if (
            not payload["queue_message_id"]
            or not payload["task_id"]
            or not payload["run_id"]
            or not payload["content"]
            or payload["ordinal"] <= 0
        ):
            return False
        return self._changed_rows(
            self._request(
                """
            INSERT INTO ai_search_execution_message_queue (
                queue_message_id, task_id, run_id, content, ordinal,
                status, created_at, updated_at, consumed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    payload["queue_message_id"],
                    payload["task_id"],
                    payload["run_id"],
                    payload["content"],
                    payload["ordinal"],
                    payload["status"],
                    payload["created_at"],
                    payload["updated_at"],
                    payload["consumed_at"],
                ],
            )
        ) > 0

    def get_ai_search_execution_queue_message(
        self, queue_message_id: str
    ) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT * FROM ai_search_execution_message_queue WHERE queue_message_id = ? LIMIT 1",
            [queue_message_id],
        )
        return self._row_to_ai_search_execution_queue_message(row) if row else None

    def list_ai_search_execution_queue_messages(
        self, task_id: str, run_id: str, *, statuses: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        where = ["task_id = ?", "run_id = ?"]
        params: List[Any] = [task_id, run_id]
        if statuses:
            active_statuses = [str(item or "").strip() for item in statuses if str(item or "").strip()]
            if not active_statuses:
                return []
            placeholders = ", ".join("?" for _ in active_statuses)
            where.append(f"status IN ({placeholders})")
            params.extend(active_statuses)
        rows = self._fetchall(
            f"""
            SELECT *
            FROM ai_search_execution_message_queue
            WHERE {' AND '.join(where)}
            ORDER BY ordinal ASC, created_at ASC, queue_message_id ASC
            """,
            params,
        )
        return [self._row_to_ai_search_execution_queue_message(row) for row in rows]

    def update_ai_search_execution_queue_message(self, queue_message_id: str, **kwargs) -> bool:
        allowed_fields = {"content", "ordinal", "status", "updated_at", "consumed_at"}
        updates = {key: kwargs[key] for key in kwargs if key in allowed_fields}
        if not updates:
            return False
        updates.setdefault("updated_at", utc_now_z())
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        return self._changed_rows(
            self._request(
                f"UPDATE ai_search_execution_message_queue SET {set_clause} WHERE queue_message_id = ?",
                [*updates.values(), queue_message_id],
            )
        ) > 0
