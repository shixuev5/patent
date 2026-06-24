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
