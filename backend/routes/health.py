"""
健康检查路由
"""
from datetime import datetime

from fastapi import APIRouter

from backend.auth import MAX_DAILY_ANALYSIS, AUTH_TOKEN_TTL_DAYS
from backend.utils import _build_r2_storage
from src.storage import TaskStatus, get_pipeline_manager


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
        "version": "1.0.0",
        "active_tasks": active_count,
        "statistics": {
            "total": stats.get("total", 0),
            "by_status": stats.get("by_status", {}),
            "today_created": stats.get("today_created", 0),
        },
        "cache": {
            "r2_enabled": r2_storage.enabled,
        },
        "storage": {
            "backend": task_manager.storage.__class__.__name__,
        },
        "auth": {
            "daily_limit": MAX_DAILY_ANALYSIS,
            "token_ttl_days": AUTH_TOKEN_TTL_DAYS,
        },
    }
