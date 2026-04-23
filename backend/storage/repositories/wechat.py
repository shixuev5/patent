"""WeChat binding/session repository methods."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.time_utils import to_utc_z, utc_now_z
from ..models import WeChatBinding, WeChatConversationSession, WeChatDeliveryJob, WeChatFlowSession, WeChatLoginSession


class WeChatRepositoryMixin:
    def create_wechat_login_session(self, session: WeChatLoginSession) -> WeChatLoginSession:
        self._request(
            """
            INSERT INTO wechat_login_sessions (
                login_session_id, owner_id, status, qr_url, expires_at,
                bot_account_id, wechat_user_id, wechat_display_name,
                error_message, online_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                session.login_session_id, session.owner_id, session.status, session.qr_url,
                to_utc_z(session.expires_at, naive_strategy="utc"), session.bot_account_id, session.wechat_user_id, session.wechat_display_name,
                session.error_message, to_utc_z(session.online_at, naive_strategy="utc") if session.online_at else None,
                to_utc_z(session.created_at, naive_strategy="utc"), to_utc_z(session.updated_at, naive_strategy="utc"),
            ],
        )
        row = self._fetchone("SELECT * FROM wechat_login_sessions WHERE login_session_id = ?", [session.login_session_id])
        if row is None:
            raise RuntimeError("Failed to create wechat login session")
        return self._row_to_wechat_login_session(row)

    def get_wechat_login_session(self, login_session_id: str) -> Optional[WeChatLoginSession]:
        row = self._fetchone("SELECT * FROM wechat_login_sessions WHERE login_session_id = ?", [str(login_session_id or "").strip()])
        return self._row_to_wechat_login_session(row) if row else None

    def get_current_wechat_login_session(self, owner_id: str) -> Optional[WeChatLoginSession]:
        row = self._fetchone(
            "SELECT * FROM wechat_login_sessions WHERE owner_id = ? AND status IN ('pending', 'qr_ready', 'scanned', 'online') ORDER BY created_at DESC LIMIT 1",
            [owner_id],
        )
        return self._row_to_wechat_login_session(row) if row else None

    def list_pending_wechat_login_sessions(self) -> List[WeChatLoginSession]:
        rows = self._fetchall(
            "SELECT * FROM wechat_login_sessions WHERE status IN ('pending', 'qr_ready', 'scanned') ORDER BY created_at ASC",
            [],
        )
        return [self._row_to_wechat_login_session(row) for row in rows]

    def update_wechat_login_session(self, login_session_id: str, **updates: Any) -> Optional[WeChatLoginSession]:
        normalized = {k: v for k, v in updates.items() if k}
        if not normalized:
            return self.get_wechat_login_session(login_session_id)
        normalized.setdefault("updated_at", utc_now_z())
        assignments = ", ".join(f"{key} = ?" for key in normalized)
        values = [to_utc_z(value, naive_strategy="utc") if key in {"created_at", "updated_at", "expires_at", "online_at"} and value is not None else value for key, value in normalized.items()]
        values.append(str(login_session_id or "").strip())
        result = self._request(f"UPDATE wechat_login_sessions SET {assignments} WHERE login_session_id = ?", values)
        if self._changed_rows(result) <= 0:
            return None
        row = self._fetchone("SELECT * FROM wechat_login_sessions WHERE login_session_id = ?", [str(login_session_id or "").strip()])
        return self._row_to_wechat_login_session(row) if row else None

    def get_wechat_binding_by_owner(self, owner_id: str) -> Optional[WeChatBinding]:
        row = self._fetchone("SELECT * FROM wechat_bindings WHERE owner_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1", [owner_id])
        return self._row_to_wechat_binding(row) if row else None

    def get_wechat_binding_by_account(self, bot_account_id: str) -> Optional[WeChatBinding]:
        row = self._fetchone(
            "SELECT * FROM wechat_bindings WHERE bot_account_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
            [str(bot_account_id or "").strip()],
        )
        return self._row_to_wechat_binding(row) if row else None

    def get_wechat_binding_by_peer(self, bot_account_id: str, wechat_peer_id: str) -> Optional[WeChatBinding]:
        row = self._fetchone(
            "SELECT * FROM wechat_bindings WHERE bot_account_id = ? AND delivery_peer_id = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
            [bot_account_id, wechat_peer_id],
        )
        return self._row_to_wechat_binding(row) if row else None

    def list_active_wechat_bindings(self) -> List[WeChatBinding]:
        rows = self._fetchall(
            "SELECT * FROM wechat_bindings WHERE status = 'active' ORDER BY updated_at DESC",
            [],
        )
        return [self._row_to_wechat_binding(row) for row in rows]

    def upsert_wechat_binding(self, binding: WeChatBinding) -> WeChatBinding:
        now_iso = utc_now_z()
        self._request("UPDATE wechat_bindings SET status = 'disconnected', disconnected_at = ?, updated_at = ? WHERE owner_id = ? AND status = 'active'", [now_iso, now_iso, binding.owner_id])
        if binding.bot_account_id and binding.wechat_user_id:
            self._request(
                "UPDATE wechat_bindings SET status = 'disconnected', disconnected_at = ?, updated_at = ? WHERE bot_account_id = ? AND wechat_user_id = ? AND status = 'active'",
                [now_iso, now_iso, binding.bot_account_id, binding.wechat_user_id],
            )
        self._request(
            """
            INSERT INTO wechat_bindings (
                binding_id, owner_id, status, bot_account_id, wechat_user_id, wechat_display_name,
                delivery_peer_id, delivery_peer_name,
                push_task_completed, push_task_failed, push_ai_search_pending_action,
                bound_at, disconnected_at, last_inbound_at, last_outbound_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(binding_id) DO UPDATE SET
                owner_id = excluded.owner_id, status = excluded.status, bot_account_id = excluded.bot_account_id,
                wechat_user_id = excluded.wechat_user_id, wechat_display_name = excluded.wechat_display_name,
                delivery_peer_id = excluded.delivery_peer_id, delivery_peer_name = excluded.delivery_peer_name,
                push_task_completed = excluded.push_task_completed, push_task_failed = excluded.push_task_failed,
                push_ai_search_pending_action = excluded.push_ai_search_pending_action, bound_at = excluded.bound_at,
                disconnected_at = excluded.disconnected_at, last_inbound_at = excluded.last_inbound_at,
                last_outbound_at = excluded.last_outbound_at, updated_at = excluded.updated_at
            """,
            [
                binding.binding_id, binding.owner_id, binding.status, binding.bot_account_id, binding.wechat_user_id, binding.wechat_display_name,
                binding.delivery_peer_id, binding.delivery_peer_name,
                1 if binding.push_task_completed else 0, 1 if binding.push_task_failed else 0, 1 if binding.push_ai_search_pending_action else 0,
                to_utc_z(binding.bound_at, naive_strategy="utc") if binding.bound_at else None,
                to_utc_z(binding.disconnected_at, naive_strategy="utc") if binding.disconnected_at else None,
                to_utc_z(binding.last_inbound_at, naive_strategy="utc") if binding.last_inbound_at else None,
                to_utc_z(binding.last_outbound_at, naive_strategy="utc") if binding.last_outbound_at else None,
                to_utc_z(binding.created_at, naive_strategy="utc"), to_utc_z(binding.updated_at, naive_strategy="utc"),
            ],
        )
        row = self._fetchone("SELECT * FROM wechat_bindings WHERE binding_id = ?", [binding.binding_id])
        if row is None:
            raise RuntimeError("Failed to upsert wechat binding")
        return self._row_to_wechat_binding(row)

    def update_wechat_binding(self, binding_id: str, **updates: Any) -> Optional[WeChatBinding]:
        normalized = {k: v for k, v in updates.items() if k}
        if not normalized:
            return None
        normalized.setdefault("updated_at", utc_now_z())
        assignments = ", ".join(f"{key} = ?" for key in normalized)
        values = [to_utc_z(value, naive_strategy="utc") if key in {"bound_at", "disconnected_at", "last_inbound_at", "last_outbound_at", "created_at", "updated_at"} and value is not None else value for key, value in normalized.items()]
        values.append(str(binding_id or "").strip())
        result = self._request(f"UPDATE wechat_bindings SET {assignments} WHERE binding_id = ?", values)
        if self._changed_rows(result) <= 0:
            return None
        row = self._fetchone("SELECT * FROM wechat_bindings WHERE binding_id = ?", [str(binding_id or "").strip()])
        return self._row_to_wechat_binding(row) if row else None

    def disconnect_wechat_binding(self, owner_id: str) -> Optional[WeChatBinding]:
        binding = self.get_wechat_binding_by_owner(owner_id)
        if not binding:
            return None
        now_iso = utc_now_z()
        return self.update_wechat_binding(binding.binding_id, status="disconnected", disconnected_at=now_iso, updated_at=now_iso)

    def create_wechat_flow_session(self, flow_session: WeChatFlowSession) -> WeChatFlowSession:
        self._request(
            """
            INSERT INTO wechat_flow_sessions (
                flow_session_id, owner_id, flow_type, status, current_step,
                draft_payload_json, expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                flow_session.flow_session_id, flow_session.owner_id, flow_session.flow_type, flow_session.status, flow_session.current_step,
                self._encode_json_value(flow_session.draft_payload),
                to_utc_z(flow_session.expires_at, naive_strategy="utc") if flow_session.expires_at else None,
                to_utc_z(flow_session.created_at, naive_strategy="utc"), to_utc_z(flow_session.updated_at, naive_strategy="utc"),
            ],
        )
        row = self._fetchone("SELECT * FROM wechat_flow_sessions WHERE flow_session_id = ?", [flow_session.flow_session_id])
        if row is None:
            raise RuntimeError("Failed to create wechat flow session")
        return self._row_to_wechat_flow_session(row)

    def get_active_wechat_flow_session(self, owner_id: str, flow_type: str) -> Optional[WeChatFlowSession]:
        row = self._fetchone("SELECT * FROM wechat_flow_sessions WHERE owner_id = ? AND flow_type = ? AND status = 'active' ORDER BY updated_at DESC LIMIT 1", [owner_id, flow_type])
        return self._row_to_wechat_flow_session(row) if row else None

    def upsert_wechat_flow_session(self, owner_id: str, flow_type: str, *, current_step: Optional[str], draft_payload: Dict[str, Any], expires_at: Optional[Any], status: str = "active") -> WeChatFlowSession:
        existing = self.get_active_wechat_flow_session(owner_id, flow_type)
        now_iso = utc_now_z()
        flow_session_id = existing.flow_session_id if existing else f"wf-{str(now_iso).replace(':', '').replace('-', '').replace('T', '').replace('Z', '')}"
        created_at = to_utc_z(existing.created_at, naive_strategy="utc") if existing else now_iso
        self._request(
            """
            INSERT INTO wechat_flow_sessions (
                flow_session_id, owner_id, flow_type, status, current_step,
                draft_payload_json, expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(flow_session_id) DO UPDATE SET
                status = excluded.status, current_step = excluded.current_step, draft_payload_json = excluded.draft_payload_json,
                expires_at = excluded.expires_at, updated_at = excluded.updated_at
            """,
            [flow_session_id, owner_id, flow_type, status, current_step, self._encode_json_value(draft_payload), to_utc_z(expires_at, naive_strategy="utc") if expires_at else None, created_at, now_iso],
        )
        row = self._fetchone("SELECT * FROM wechat_flow_sessions WHERE flow_session_id = ?", [flow_session_id])
        if row is None:
            raise RuntimeError("Failed to upsert wechat flow session")
        return self._row_to_wechat_flow_session(row)

    def resolve_wechat_flow_session(self, owner_id: str, flow_type: str, status: str = "completed") -> bool:
        return self._changed_rows(self._request(
            "UPDATE wechat_flow_sessions SET status = ?, updated_at = ? WHERE owner_id = ? AND flow_type = ? AND status = 'active'",
            [status, utc_now_z(), owner_id, flow_type],
        )) > 0

    def get_wechat_conversation_session(self, binding_id: str, peer_id: Optional[str] = None) -> Optional[WeChatConversationSession]:
        normalized_binding_id = str(binding_id or "").strip()
        normalized_peer_id = str(peer_id or "").strip()
        if normalized_peer_id:
            row = self._fetchone(
                "SELECT * FROM wechat_conversation_sessions WHERE binding_id = ? AND peer_id = ? ORDER BY updated_at DESC LIMIT 1",
                [normalized_binding_id, normalized_peer_id],
            )
        else:
            row = self._fetchone(
                "SELECT * FROM wechat_conversation_sessions WHERE binding_id = ? ORDER BY updated_at DESC LIMIT 1",
                [normalized_binding_id],
            )
        return self._row_to_wechat_conversation_session(row) if row else None

    def upsert_wechat_conversation_session(self, session: WeChatConversationSession) -> WeChatConversationSession:
        self._request(
            """
            INSERT INTO wechat_conversation_sessions (
                conversation_id, owner_id, binding_id, peer_id, peer_name, status,
                active_context_kind, active_context_session_id, active_context_title,
                memory_json, last_inbound_at, last_outbound_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET
                owner_id = excluded.owner_id,
                binding_id = excluded.binding_id,
                peer_id = excluded.peer_id,
                peer_name = excluded.peer_name,
                status = excluded.status,
                active_context_kind = excluded.active_context_kind,
                active_context_session_id = excluded.active_context_session_id,
                active_context_title = excluded.active_context_title,
                memory_json = excluded.memory_json,
                last_inbound_at = excluded.last_inbound_at,
                last_outbound_at = excluded.last_outbound_at,
                updated_at = excluded.updated_at
            """,
            [
                session.conversation_id,
                session.owner_id,
                session.binding_id,
                session.peer_id,
                session.peer_name,
                session.status,
                session.active_context_kind,
                session.active_context_session_id,
                session.active_context_title,
                self._encode_json_value(session.memory),
                to_utc_z(session.last_inbound_at, naive_strategy="utc") if session.last_inbound_at else None,
                to_utc_z(session.last_outbound_at, naive_strategy="utc") if session.last_outbound_at else None,
                to_utc_z(session.created_at, naive_strategy="utc"),
                to_utc_z(session.updated_at, naive_strategy="utc"),
            ],
        )
        row = self._fetchone("SELECT * FROM wechat_conversation_sessions WHERE conversation_id = ?", [session.conversation_id])
        if row is None:
            raise RuntimeError("Failed to upsert wechat conversation session")
        return self._row_to_wechat_conversation_session(row)

    def update_wechat_conversation_session(self, conversation_id: str, **updates: Any) -> Optional[WeChatConversationSession]:
        normalized = {k: v for k, v in updates.items() if k}
        if not normalized:
            row = self._fetchone("SELECT * FROM wechat_conversation_sessions WHERE conversation_id = ?", [str(conversation_id or "").strip()])
            return self._row_to_wechat_conversation_session(row) if row else None
        normalized.setdefault("updated_at", utc_now_z())
        assignments = ", ".join(
            f"{('memory_json' if key == 'memory' else key)} = ?" for key in normalized
        )
        values = []
        for key, value in normalized.items():
            target_key = "memory_json" if key == "memory" else key
            if target_key == "memory_json":
                values.append(self._encode_json_value(value))
            elif target_key in {"last_inbound_at", "last_outbound_at", "created_at", "updated_at"} and value is not None:
                values.append(to_utc_z(value, naive_strategy="utc"))
            else:
                values.append(value)
        values.append(str(conversation_id or "").strip())
        result = self._request(
            f"UPDATE wechat_conversation_sessions SET {assignments} WHERE conversation_id = ?",
            values,
        )
        if self._changed_rows(result) <= 0:
            return None
        row = self._fetchone("SELECT * FROM wechat_conversation_sessions WHERE conversation_id = ?", [str(conversation_id or "").strip()])
        return self._row_to_wechat_conversation_session(row) if row else None

    def create_wechat_delivery_job(self, job: WeChatDeliveryJob) -> WeChatDeliveryJob:
        self._request(
            """
            INSERT INTO wechat_delivery_jobs (
                delivery_job_id, owner_id, binding_id, task_id, event_type, status,
                delivery_stage, payload_json, stage_details_json, attempt_count, max_attempts, next_attempt_at, claimed_at,
                completed_at, failed_at, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                job.delivery_job_id, job.owner_id, job.binding_id, job.task_id, job.event_type, job.status, job.delivery_stage,
                self._encode_json_value(job.payload), self._encode_json_value(job.stage_details), job.attempt_count, job.max_attempts,
                to_utc_z(job.next_attempt_at, naive_strategy="utc") if job.next_attempt_at else None,
                to_utc_z(job.claimed_at, naive_strategy="utc") if job.claimed_at else None,
                to_utc_z(job.completed_at, naive_strategy="utc") if job.completed_at else None,
                to_utc_z(job.failed_at, naive_strategy="utc") if job.failed_at else None,
                job.last_error, to_utc_z(job.created_at, naive_strategy="utc"), to_utc_z(job.updated_at, naive_strategy="utc"),
            ],
        )
        row = self._fetchone("SELECT * FROM wechat_delivery_jobs WHERE delivery_job_id = ?", [job.delivery_job_id])
        if row is None:
            raise RuntimeError("Failed to create wechat delivery job")
        return self._row_to_wechat_delivery_job(row)

    def claim_wechat_delivery_jobs(self, limit: int = 1) -> List[WeChatDeliveryJob]:
        normalized_limit = max(1, int(limit or 1))
        now_iso = utc_now_z()
        rows = self._fetchall(
            "SELECT * FROM wechat_delivery_jobs WHERE status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= ?) ORDER BY created_at ASC LIMIT ?",
            [now_iso, normalized_limit],
        )
        jobs: List[WeChatDeliveryJob] = []
        for row in rows:
            delivery_job_id = str(row.get("delivery_job_id") or "").strip()
            if not delivery_job_id:
                continue
            result = self._request(
                "UPDATE wechat_delivery_jobs SET status = 'processing', delivery_stage = 'claimed', claimed_at = ?, updated_at = ?, attempt_count = attempt_count + 1 WHERE delivery_job_id = ? AND status = 'pending'",
                [now_iso, now_iso, delivery_job_id],
            )
            if self._changed_rows(result) <= 0:
                continue
            claimed = self._fetchone("SELECT * FROM wechat_delivery_jobs WHERE delivery_job_id = ?", [delivery_job_id])
            if claimed:
                jobs.append(self._row_to_wechat_delivery_job(claimed))
        return jobs

    def update_wechat_delivery_job(self, delivery_job_id: str, **updates: Any) -> Optional[WeChatDeliveryJob]:
        normalized = {k: v for k, v in updates.items() if k}
        if not normalized:
            return None
        normalized.setdefault("updated_at", utc_now_z())
        assignments = ", ".join(
            f"{('payload_json' if key == 'payload' else 'stage_details_json' if key == 'stage_details' else key)} = ?"
            for key in normalized
        )
        values = []
        for key, value in normalized.items():
            target_key = "payload_json" if key == "payload" else "stage_details_json" if key == "stage_details" else key
            if target_key in {"payload_json", "stage_details_json"}:
                values.append(self._encode_json_value(value))
            elif target_key in {"next_attempt_at", "claimed_at", "completed_at", "failed_at", "created_at", "updated_at"} and value is not None:
                values.append(to_utc_z(value, naive_strategy="utc"))
            else:
                values.append(value)
        values.append(str(delivery_job_id or "").strip())
        result = self._request(f"UPDATE wechat_delivery_jobs SET {assignments} WHERE delivery_job_id = ?", values)
        if self._changed_rows(result) <= 0:
            return None
        row = self._fetchone("SELECT * FROM wechat_delivery_jobs WHERE delivery_job_id = ?", [str(delivery_job_id or "").strip()])
        return self._row_to_wechat_delivery_job(row) if row else None
