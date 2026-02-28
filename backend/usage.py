"""
用户使用额度管理
"""
import os
from fastapi import HTTPException

from backend.auth import _quota_reset_utc
from backend.models import UsageResponse
from agents.patent_analysis.src.storage import get_pipeline_manager


task_manager = get_pipeline_manager()


def _get_user_usage(owner_id: str) -> UsageResponse:
    used_today = task_manager.storage.count_user_tasks_today(owner_id, tz_offset_hours=8)
    remaining = max(0, int(os.getenv("MAX_DAILY_ANALYSIS", "3")) - used_today)
    reset_at = _quota_reset_utc().isoformat()
    return UsageResponse(
        userId=owner_id,
        dailyLimit=int(os.getenv("MAX_DAILY_ANALYSIS", "3")),
        usedToday=used_today,
        remaining=remaining,
        resetAt=reset_at,
    )


def _enforce_daily_quota(owner_id: str):
    usage = _get_user_usage(owner_id)
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
