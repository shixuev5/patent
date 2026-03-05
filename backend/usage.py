"""
用户使用额度管理
"""
import os
from typing import Optional

from fastapi import HTTPException

from backend.auth import _quota_reset_utc
from backend.models import UsageResponse
from backend.storage import TaskType
from backend.storage import get_pipeline_manager


task_manager = get_pipeline_manager()
ALLOWED_TASK_TYPES = {
    TaskType.PATENT_ANALYSIS.value,
    TaskType.OFFICE_ACTION_REPLY.value,
}


def _parse_limit(value: Optional[str], fallback: int) -> int:
    try:
        return int(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _normalize_task_type(raw: Optional[str]) -> str:
    task_type = (raw or TaskType.PATENT_ANALYSIS.value).strip().lower()
    if task_type not in ALLOWED_TASK_TYPES:
        return TaskType.PATENT_ANALYSIS.value
    return task_type


def _daily_limit_for(task_type: str) -> int:
    if task_type == TaskType.OFFICE_ACTION_REPLY.value:
        return _parse_limit(os.getenv("MAX_DAILY_OFFICE_ACTION_REPLY"), 3)
    return _parse_limit(os.getenv("MAX_DAILY_PATENT_ANALYSIS"), 3)


def _get_user_usage(owner_id: str, task_type: Optional[str] = None) -> UsageResponse:
    normalized_task_type = _normalize_task_type(task_type)
    daily_limit = _daily_limit_for(normalized_task_type)
    used_today = task_manager.storage.count_user_tasks_today(
        owner_id,
        tz_offset_hours=8,
        task_type=normalized_task_type,
        include_deleted=True,
    )
    remaining = max(0, daily_limit - used_today)
    reset_at = _quota_reset_utc().isoformat()
    return UsageResponse(
        userId=owner_id,
        dailyLimit=daily_limit,
        usedToday=used_today,
        remaining=remaining,
        resetAt=reset_at,
    )


def _enforce_daily_quota(owner_id: str, task_type: Optional[str] = None):
    usage = _get_user_usage(owner_id, task_type=task_type)
    if usage.usedToday >= usage.dailyLimit:
        raise HTTPException(
            status_code=429,
            detail={
                "message": "已达到每日分析上限。",
                "dailyLimit": usage.dailyLimit,
                "usedToday": usage.usedToday,
                "remaining": usage.remaining,
                "resetAt": usage.resetAt,
            },
        )
