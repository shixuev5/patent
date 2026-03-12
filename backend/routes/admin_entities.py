"""
管理员用户/任务只读列表路由。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.admin_auth import ensure_admin_owner
from backend.auth import _get_current_user
from backend.models import (
    AdminEntityTaskStatsResponse,
    AdminEntityTaskDetailResponse,
    AdminEntityTaskItem,
    AdminEntityTaskListResponse,
    AdminEntityUserItem,
    AdminEntityUserListResponse,
    AdminEntityUserStatsResponse,
    CurrentUser,
)
from backend.storage import get_pipeline_manager


router = APIRouter()
task_manager = get_pipeline_manager()

DEFAULT_USER_STATS = {
    "totalUsers": 0,
    "registeredUsers": 0,
    "activeUsers1d": 0,
    "activeUsers7d": 0,
    "activeUsers30d": 0,
    "newUsers1d": 0,
    "newUsers7d": 0,
    "newUsers30d": 0,
}

DEFAULT_TASK_TYPE_WINDOWS: list[dict[str, Any]] = []


def _norm_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _norm_sort_order(value: Optional[str]) -> str:
    text = str(value or "desc").strip().lower()
    if text == "asc":
        return "asc"
    return "desc"


def _map_user_sort_by(value: Optional[str]) -> str:
    raw = str(value or "latestTaskAt").strip()
    mapping = {
        "ownerId": "owner_id",
        "userName": "user_name",
        "email": "email",
        "role": "role",
        "lastLoginAt": "last_login_at",
        "createdAt": "created_at",
        "taskCount": "task_count",
        "latestTaskAt": "latest_task_at",
    }
    return mapping.get(raw, "latest_task_at")


def _map_task_sort_by(value: Optional[str]) -> str:
    raw = str(value or "createdAt").strip()
    mapping = {
        "taskId": "task_id",
        "title": "title",
        "userName": "user_name",
        "taskType": "task_type",
        "status": "status",
        "createdAt": "created_at",
        "updatedAt": "updated_at",
        "completedAt": "completed_at",
    }
    return mapping.get(raw, "updated_at")


def _to_user_item(row: Dict[str, Any]) -> AdminEntityUserItem:
    return AdminEntityUserItem(
        ownerId=str(row.get("owner_id") or ""),
        userName=row.get("user_name"),
        email=row.get("email"),
        role=row.get("role"),
        lastLoginAt=row.get("last_login_at"),
        createdAt=row.get("created_at"),
        taskCount=int(row.get("task_count") or 0),
        latestTaskAt=row.get("latest_task_at"),
    )


def _to_task_item(row: Dict[str, Any]) -> AdminEntityTaskItem:
    return AdminEntityTaskItem(
        taskId=str(row.get("task_id") or ""),
        title=row.get("title"),
        ownerId=row.get("owner_id"),
        userName=row.get("user_name"),
        taskType=row.get("task_type"),
        status=row.get("status"),
        durationSeconds=row.get("duration_seconds"),
        createdAt=row.get("created_at"),
        updatedAt=row.get("updated_at"),
        completedAt=row.get("completed_at"),
    )


@router.get("/api/admin/entities/users", response_model=AdminEntityUserListResponse)
async def get_admin_entity_users(
    q: Optional[str] = Query(default=None),
    role: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=200),
    sortBy: Optional[str] = Query(default="latestTaskAt"),
    sortOrder: Optional[str] = Query(default="desc"),
    current_user: CurrentUser = Depends(_get_current_user),
):
    ensure_admin_owner(current_user.user_id)

    if not hasattr(task_manager.storage, "list_admin_users"):
        return AdminEntityUserListResponse(page=page, pageSize=pageSize, total=0, items=[], meta=None)

    result = task_manager.storage.list_admin_users(
        q=_norm_optional_text(q),
        role=_norm_optional_text(role),
        page=page,
        page_size=pageSize,
        sort_by=_map_user_sort_by(sortBy),
        sort_order=_norm_sort_order(sortOrder),
    )
    rows = result.get("items") or []
    return AdminEntityUserListResponse(
        page=page,
        pageSize=pageSize,
        total=int(result.get("total") or 0),
        items=[_to_user_item(row) for row in rows],
        meta=result.get("meta"),
    )


@router.get("/api/admin/entities/users/stats", response_model=AdminEntityUserStatsResponse)
async def get_admin_entity_user_stats(
    current_user: CurrentUser = Depends(_get_current_user),
):
    ensure_admin_owner(current_user.user_id)

    if not hasattr(task_manager.storage, "summarize_admin_users"):
        return AdminEntityUserStatsResponse(userStats=DEFAULT_USER_STATS)

    result = task_manager.storage.summarize_admin_users()
    user_stats = result.get("userStats") if isinstance(result, dict) else None
    if not isinstance(user_stats, dict):
        user_stats = {}
    merged = {**DEFAULT_USER_STATS, **user_stats}
    return AdminEntityUserStatsResponse(userStats=merged)


@router.get("/api/admin/entities/tasks", response_model=AdminEntityTaskListResponse)
async def get_admin_entity_tasks(
    q: Optional[str] = Query(default=None),
    userName: Optional[str] = Query(default=None),
    taskType: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    dateFrom: Optional[str] = Query(default=None),
    dateTo: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=200),
    sortBy: Optional[str] = Query(default="createdAt"),
    sortOrder: Optional[str] = Query(default="desc"),
    current_user: CurrentUser = Depends(_get_current_user),
):
    ensure_admin_owner(current_user.user_id)

    if not hasattr(task_manager.storage, "list_admin_tasks"):
        return AdminEntityTaskListResponse(page=page, pageSize=pageSize, total=0, items=[], meta=None)

    result = task_manager.storage.list_admin_tasks(
        q=_norm_optional_text(q),
        user_name=_norm_optional_text(userName),
        task_type=_norm_optional_text(taskType),
        status=_norm_optional_text(status),
        date_from=_norm_optional_text(dateFrom),
        date_to=_norm_optional_text(dateTo),
        page=page,
        page_size=pageSize,
        sort_by=_map_task_sort_by(sortBy),
        sort_order=_norm_sort_order(sortOrder),
    )
    rows = result.get("items") or []
    return AdminEntityTaskListResponse(
        page=page,
        pageSize=pageSize,
        total=int(result.get("total") or 0),
        items=[_to_task_item(row) for row in rows],
        meta=result.get("meta"),
    )


@router.get("/api/admin/entities/tasks/stats", response_model=AdminEntityTaskStatsResponse)
async def get_admin_entity_task_stats(
    current_user: CurrentUser = Depends(_get_current_user),
):
    ensure_admin_owner(current_user.user_id)

    if not hasattr(task_manager.storage, "summarize_admin_tasks"):
        return AdminEntityTaskStatsResponse(taskTypeWindows=DEFAULT_TASK_TYPE_WINDOWS)

    result = task_manager.storage.summarize_admin_tasks()
    task_type_windows = result.get("taskTypeWindows") if isinstance(result, dict) else None
    if not isinstance(task_type_windows, list):
        task_type_windows = DEFAULT_TASK_TYPE_WINDOWS
    return AdminEntityTaskStatsResponse(taskTypeWindows=task_type_windows)


@router.get("/api/admin/entities/tasks/{task_id}", response_model=AdminEntityTaskDetailResponse)
async def get_admin_entity_task_detail(
    task_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    ensure_admin_owner(current_user.user_id)

    if not hasattr(task_manager.storage, "get_admin_task_detail"):
        raise HTTPException(status_code=404, detail="任务不存在。")

    row = task_manager.storage.get_admin_task_detail(str(task_id or "").strip())
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在。")

    item = {
        "taskId": row.get("task_id"),
        "ownerId": row.get("owner_id"),
        "userName": row.get("user_name"),
        "taskType": row.get("task_type"),
        "pn": row.get("pn"),
        "title": row.get("title"),
        "status": row.get("status"),
        "progress": row.get("progress"),
        "currentStep": row.get("current_step"),
        "outputDir": row.get("output_dir"),
        "errorMessage": row.get("error_message"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "completedAt": row.get("completed_at"),
        "metadata": row.get("metadata"),
    }
    return AdminEntityTaskDetailResponse(item=item)
