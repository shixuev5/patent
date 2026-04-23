"""Shared row codecs and JSON/timestamp helpers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from backend.time_utils import parse_storage_ts, to_utc_z
from .models import (
    AccountMonthTarget,
    RefreshSession,
    Task,
    TaskStatus,
    TaskType,
    User,
    WeChatBinding,
    WeChatConversationSession,
    WeChatDeliveryJob,
    WeChatFlowSession,
    WeChatLoginSession,
)


class StorageCodecsMixin:
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
    def _encode_json_value(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False)
        return value

    @staticmethod
    def _normalize_update_value(value: Any) -> Any:
        if isinstance(value, TaskStatus):
            return value.value
        if isinstance(value, datetime):
            return to_utc_z(value, naive_strategy="utc")
        return value

    @staticmethod
    def _normalize_usage_timestamp(value: Any, *, field: str) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, datetime) and str(value).strip() == "":
            return None
        normalized = to_utc_z(value, naive_strategy="utc")
        if normalized is None:
            raise ValueError(f"Invalid task_llm_usage timestamp for {field}: {value}")
        return normalized

    def _row_to_task(self, row: Dict[str, Any]) -> Task:
        return Task(
            id=row["id"],
            owner_id=row.get("owner_id"),
            task_type=row.get("task_type") or TaskType.PATENT_ANALYSIS.value,
            pn=row.get("pn"),
            title=row.get("title"),
            status=TaskStatus(row["status"]),
            progress=row.get("progress", 0),
            current_step=row.get("current_step"),
            output_dir=row.get("output_dir"),
            error_message=row.get("error_message"),
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
            completed_at=parse_storage_ts(row["completed_at"], naive_strategy="utc") if row.get("completed_at") else None,
            deleted_at=parse_storage_ts(row["deleted_at"], naive_strategy="utc") if row.get("deleted_at") else None,
            metadata=self._parse_metadata(row.get("metadata")),
        )

    def _row_to_user(self, row: Dict[str, Any]) -> User:
        return User(
            owner_id=row["owner_id"],
            authing_sub=row["authing_sub"],
            role=row.get("role"),
            name=row.get("name"),
            nickname=row.get("nickname"),
            email=row.get("email"),
            phone=row.get("phone"),
            picture=row.get("picture"),
            notification_email_enabled=bool(row.get("notification_email_enabled")),
            work_notification_email=row.get("work_notification_email"),
            personal_notification_email=row.get("personal_notification_email"),
            raw_profile=self._parse_metadata(row.get("raw_profile")),
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
            last_login_at=parse_storage_ts(row["last_login_at"], naive_strategy="utc"),
        )

    def _row_to_refresh_session(self, row: Dict[str, Any]) -> RefreshSession:
        return RefreshSession(
            token_hash=str(row["token_hash"]),
            owner_id=str(row["owner_id"]),
            expires_at=parse_storage_ts(row["expires_at"], naive_strategy="utc"),
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
            revoked_at=parse_storage_ts(row["revoked_at"], naive_strategy="utc") if row.get("revoked_at") else None,
            replaced_by_token_hash=str(row.get("replaced_by_token_hash") or "").strip() or None,
        )

    def _row_to_account_month_target(self, row: Dict[str, Any]) -> AccountMonthTarget:
        return AccountMonthTarget(
            owner_id=row["owner_id"],
            year=int(row["year"]),
            month=int(row["month"]),
            target_count=int(row["target_count"]),
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
        )

    def _row_to_wechat_binding(self, row: Dict[str, Any]) -> WeChatBinding:
        return WeChatBinding(
            binding_id=str(row["binding_id"]),
            owner_id=str(row["owner_id"]),
            status=str(row["status"]),
            bot_account_id=row.get("bot_account_id"),
            wechat_user_id=row.get("wechat_user_id"),
            wechat_display_name=row.get("wechat_display_name"),
            delivery_peer_id=row.get("delivery_peer_id"),
            delivery_peer_name=row.get("delivery_peer_name"),
            push_task_completed=bool(row.get("push_task_completed", 1)),
            push_task_failed=bool(row.get("push_task_failed", 1)),
            push_ai_search_pending_action=bool(row.get("push_ai_search_pending_action", 1)),
            bound_at=parse_storage_ts(row["bound_at"], naive_strategy="utc") if row.get("bound_at") else None,
            disconnected_at=parse_storage_ts(row["disconnected_at"], naive_strategy="utc") if row.get("disconnected_at") else None,
            last_inbound_at=parse_storage_ts(row["last_inbound_at"], naive_strategy="utc") if row.get("last_inbound_at") else None,
            last_outbound_at=parse_storage_ts(row["last_outbound_at"], naive_strategy="utc") if row.get("last_outbound_at") else None,
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
        )

    def _row_to_wechat_login_session(self, row: Dict[str, Any]) -> WeChatLoginSession:
        return WeChatLoginSession(
            login_session_id=str(row["login_session_id"]),
            owner_id=str(row["owner_id"]),
            status=str(row["status"]),
            qr_url=str(row.get("qr_url") or "").strip() or None,
            expires_at=parse_storage_ts(row["expires_at"], naive_strategy="utc"),
            bot_account_id=row.get("bot_account_id"),
            wechat_user_id=row.get("wechat_user_id"),
            wechat_display_name=row.get("wechat_display_name"),
            error_message=row.get("error_message"),
            online_at=parse_storage_ts(row["online_at"], naive_strategy="utc") if row.get("online_at") else None,
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
        )

    def _row_to_wechat_flow_session(self, row: Dict[str, Any]) -> WeChatFlowSession:
        return WeChatFlowSession(
            flow_session_id=str(row["flow_session_id"]),
            owner_id=str(row["owner_id"]),
            flow_type=str(row["flow_type"]),
            status=str(row["status"]),
            current_step=row.get("current_step"),
            draft_payload=self._parse_metadata(row.get("draft_payload_json")),
            expires_at=parse_storage_ts(row["expires_at"], naive_strategy="utc") if row.get("expires_at") else None,
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
        )

    def _row_to_wechat_conversation_session(self, row: Dict[str, Any]) -> WeChatConversationSession:
        return WeChatConversationSession(
            conversation_id=str(row["conversation_id"]),
            owner_id=str(row["owner_id"]),
            binding_id=str(row["binding_id"]),
            peer_id=str(row["peer_id"]),
            peer_name=str(row.get("peer_name") or "").strip() or None,
            status=str(row["status"]),
            active_context_kind=str(row.get("active_context_kind") or "none"),
            active_context_session_id=str(row.get("active_context_session_id") or "").strip() or None,
            active_context_title=str(row.get("active_context_title") or "").strip() or None,
            memory=self._parse_metadata(row.get("memory_json")),
            last_inbound_at=parse_storage_ts(row["last_inbound_at"], naive_strategy="utc") if row.get("last_inbound_at") else None,
            last_outbound_at=parse_storage_ts(row["last_outbound_at"], naive_strategy="utc") if row.get("last_outbound_at") else None,
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
        )

    def _row_to_wechat_delivery_job(self, row: Dict[str, Any]) -> WeChatDeliveryJob:
        return WeChatDeliveryJob(
            delivery_job_id=str(row["delivery_job_id"]),
            owner_id=str(row["owner_id"]),
            binding_id=row.get("binding_id"),
            task_id=row.get("task_id"),
            event_type=str(row["event_type"]),
            status=str(row["status"]),
            delivery_stage=str(row.get("delivery_stage") or "queued"),
            payload=self._parse_metadata(row.get("payload_json")),
            stage_details=self._parse_metadata(row.get("stage_details_json")),
            attempt_count=int(row.get("attempt_count") or 0),
            max_attempts=int(row.get("max_attempts") or 3),
            next_attempt_at=parse_storage_ts(row["next_attempt_at"], naive_strategy="utc") if row.get("next_attempt_at") else None,
            claimed_at=parse_storage_ts(row["claimed_at"], naive_strategy="utc") if row.get("claimed_at") else None,
            completed_at=parse_storage_ts(row["completed_at"], naive_strategy="utc") if row.get("completed_at") else None,
            failed_at=parse_storage_ts(row["failed_at"], naive_strategy="utc") if row.get("failed_at") else None,
            last_error=row.get("last_error"),
            created_at=parse_storage_ts(row["created_at"], naive_strategy="utc"),
            updated_at=parse_storage_ts(row["updated_at"], naive_strategy="utc"),
        )
