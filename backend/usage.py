"""
用户使用额度管理
"""
import os
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import HTTPException

from backend.auth import APP_TZ_OFFSET_HOURS, _quota_reset_utc
from backend.models import UsageResponse
from backend.storage import TaskType
from backend.storage import get_pipeline_manager


task_manager = get_pipeline_manager()
POINT_UNITS_PER_POINT = 2  # 0.5 分为最小精度单位
DEFAULT_DAILY_POINTS_GUEST = 3.0
DEFAULT_DAILY_POINTS_AUTHING = 10.0
TASK_POINT_COST_UNITS = {
    TaskType.PATENT_ANALYSIS.value: 2,  # 1.0
    TaskType.AI_REVIEW.value: 2,  # 1.0
    TaskType.AI_REPLY.value: 4,  # 2.0
    TaskType.AI_SEARCH.value: 3,  # 1.5
}
ALLOWED_TASK_TYPES = {
    TaskType.PATENT_ANALYSIS.value,
    TaskType.AI_REVIEW.value,
    TaskType.AI_REPLY.value,
    TaskType.AI_SEARCH.value,
}
POINT_OCCUPIED_STATUSES = ("pending", "processing", "paused", "completed")


def _parse_point_limit_units(value: Optional[str], fallback_points: float) -> int:
    fallback_units = int(fallback_points * POINT_UNITS_PER_POINT)
    if value is None:
        return fallback_units
    text = str(value).strip()
    if not text:
        return fallback_units
    try:
        parsed = Decimal(text)
    except (TypeError, ValueError, InvalidOperation):
        return fallback_units
    if parsed < 0:
        return fallback_units
    units = parsed * Decimal(POINT_UNITS_PER_POINT)
    if units % 1 != 0:
        return fallback_units
    return int(units)


def _normalize_task_type(raw: Optional[str]) -> str:
    task_type = (raw or TaskType.PATENT_ANALYSIS.value).strip().lower()
    if task_type not in ALLOWED_TASK_TYPES:
        return TaskType.PATENT_ANALYSIS.value
    return task_type


def _units_to_points(units: int) -> float:
    return units / POINT_UNITS_PER_POINT


def _auth_type_for_owner_id(owner_id: str) -> str:
    if str(owner_id or "").startswith("authing:"):
        return "authing"
    return "guest"


def _task_point_cost_units(task_type: str) -> int:
    normalized = _normalize_task_type(task_type)
    return TASK_POINT_COST_UNITS[normalized]


def _daily_point_limit_units_for_auth_type(auth_type: str) -> int:
    normalized = "authing" if str(auth_type or "").strip().lower() == "authing" else "guest"
    if normalized == "authing":
        return _parse_point_limit_units(
            os.getenv("MAX_DAILY_POINTS_AUTHING"),
            DEFAULT_DAILY_POINTS_AUTHING,
        )
    return _parse_point_limit_units(
        os.getenv("MAX_DAILY_POINTS_GUEST"),
        DEFAULT_DAILY_POINTS_GUEST,
    )


def _daily_point_limit_for_auth_type(auth_type: str) -> float:
    return _units_to_points(_daily_point_limit_units_for_auth_type(auth_type))


def _today_created_count(owner_id: str, task_type: str) -> int:
    return task_manager.storage.count_user_tasks_today(
        owner_id,
        tz_offset_hours=APP_TZ_OFFSET_HOURS,
        task_type=task_type,
        include_deleted=True,
    )


def _today_point_occupied_count(owner_id: str, task_type: str) -> int:
    return task_manager.storage.count_user_tasks_today(
        owner_id,
        tz_offset_hours=APP_TZ_OFFSET_HOURS,
        task_type=task_type,
        include_deleted=True,
        statuses=list(POINT_OCCUPIED_STATUSES),
    )


def _get_user_usage(owner_id: str, task_type: Optional[str] = None) -> UsageResponse:
    auth_type = _auth_type_for_owner_id(owner_id)
    daily_limit_units = _daily_point_limit_units_for_auth_type(auth_type)
    analysis_count = _today_created_count(owner_id, TaskType.PATENT_ANALYSIS.value)
    review_count = _today_created_count(owner_id, TaskType.AI_REVIEW.value)
    reply_count = _today_created_count(owner_id, TaskType.AI_REPLY.value)
    search_count = _today_created_count(owner_id, TaskType.AI_SEARCH.value)
    analysis_occupied_count = _today_point_occupied_count(owner_id, TaskType.PATENT_ANALYSIS.value)
    review_occupied_count = _today_point_occupied_count(owner_id, TaskType.AI_REVIEW.value)
    reply_occupied_count = _today_point_occupied_count(owner_id, TaskType.AI_REPLY.value)
    search_occupied_count = _today_point_occupied_count(owner_id, TaskType.AI_SEARCH.value)
    used_units = (
        analysis_occupied_count * TASK_POINT_COST_UNITS[TaskType.PATENT_ANALYSIS.value]
        + review_occupied_count * TASK_POINT_COST_UNITS[TaskType.AI_REVIEW.value]
        + reply_occupied_count * TASK_POINT_COST_UNITS[TaskType.AI_REPLY.value]
        + search_occupied_count * TASK_POINT_COST_UNITS[TaskType.AI_SEARCH.value]
    )
    remaining_units = max(0, daily_limit_units - used_units)

    requested_task_type = _normalize_task_type(task_type) if task_type is not None else None
    requested_task_units = _task_point_cost_units(requested_task_type) if requested_task_type else None
    can_create_requested = (remaining_units >= requested_task_units) if requested_task_units is not None else None

    reset_at = _quota_reset_utc().isoformat()
    return UsageResponse(
        userId=owner_id,
        authType=auth_type,
        dailyPointLimit=_units_to_points(daily_limit_units),
        usedPoints=_units_to_points(used_units),
        remainingPoints=_units_to_points(remaining_units),
        costPerTask={
            "patentAnalysis": _units_to_points(TASK_POINT_COST_UNITS[TaskType.PATENT_ANALYSIS.value]),
            "aiReview": _units_to_points(TASK_POINT_COST_UNITS[TaskType.AI_REVIEW.value]),
            "officeActionReply": _units_to_points(TASK_POINT_COST_UNITS[TaskType.AI_REPLY.value]),
            "aiSearch": _units_to_points(TASK_POINT_COST_UNITS[TaskType.AI_SEARCH.value]),
        },
        createdToday={
            "analysisCount": analysis_count,
            "reviewCount": review_count,
            "replyCount": reply_count,
            "searchCount": search_count,
            "totalCount": analysis_count + review_count + reply_count + search_count,
        },
        requestedTaskType=requested_task_type,
        requestedTaskPoints=_units_to_points(requested_task_units) if requested_task_units is not None else None,
        canCreateRequestedTask=can_create_requested,
        resetAt=reset_at,
    )


def _enforce_daily_quota(owner_id: str, task_type: Optional[str] = None):
    normalized_task_type = _normalize_task_type(task_type)
    usage = _get_user_usage(owner_id, task_type=normalized_task_type)
    if usage.canCreateRequestedTask is False:
        required_points = usage.requestedTaskPoints or _units_to_points(_task_point_cost_units(normalized_task_type))
        should_prompt_login = usage.authType == "guest"
        message = (
            "今日积分已用完，登录/注册后可获得更多每日积分。"
            if should_prompt_login
            else "今日积分已用完，请明日重置后再试。"
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "DAILY_POINTS_EXCEEDED",
                "message": message,
                "authType": usage.authType,
                "taskType": normalized_task_type,
                "requiredPoints": required_points,
                "dailyPointLimit": usage.dailyPointLimit,
                "usedPoints": usage.usedPoints,
                "remainingPoints": usage.remainingPoints,
                "resetAt": usage.resetAt,
                "shouldPromptLogin": should_prompt_login,
            },
        )
