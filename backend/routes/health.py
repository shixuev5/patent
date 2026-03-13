"""
健康检查路由
"""
from datetime import datetime

from fastapi import APIRouter

from config import VERSION
from backend.auth import AUTH_TOKEN_TTL_DAYS
from backend.usage import (
    TASK_POINT_COST_UNITS,
    _daily_point_limit_for_auth_type,
    _units_to_points,
)
from backend.utils import _build_r2_storage
from backend.storage import TaskStatus, TaskType, get_pipeline_manager


router = APIRouter()
task_manager = get_pipeline_manager()


@router.get("/api/health")
async def health_check():
    """健康检查"""
    active_count = len(task_manager.list_tasks(status=TaskStatus.PROCESSING, limit=1000))
    stats = task_manager.storage.get_statistics()
    r2_storage = _build_r2_storage()
    return {
        "status": "正常",
        "timestamp": datetime.now().isoformat(),
        "version": VERSION,
        "active_tasks": active_count,
        "statistics": stats,
        "cache": {
            "r2_enabled": r2_storage.enabled,
        },
        "storage": {
            "backend": task_manager.storage.__class__.__name__,
        },
        "auth": {
            "daily_points_guest": _daily_point_limit_for_auth_type("guest"),
            "daily_points_authing": _daily_point_limit_for_auth_type("authing"),
            "cost_patent_analysis_points": _units_to_points(TASK_POINT_COST_UNITS[TaskType.PATENT_ANALYSIS.value]),
            "cost_ai_review_points": _units_to_points(TASK_POINT_COST_UNITS[TaskType.AI_REVIEW.value]),
            "cost_office_action_reply_points": _units_to_points(TASK_POINT_COST_UNITS[TaskType.OFFICE_ACTION_REPLY.value]),
            "token_ttl_days": AUTH_TOKEN_TTL_DAYS,
        },
    }
