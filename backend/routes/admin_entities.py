"""
管理员用户/任务只读列表路由。
"""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from config import settings
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
from backend.storage import TaskType, get_pipeline_manager
from backend.system_logs import emit_system_log
from backend.time_utils import parse_local_input_to_utc_z, to_utc_z
from backend.utils import _build_r2_storage


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
ALLOWED_TASK_TYPES = {
    TaskType.PATENT_ANALYSIS.value,
    TaskType.AI_REVIEW.value,
    TaskType.AI_REPLY.value,
    TaskType.AI_SEARCH.value,
}


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
        lastLoginAt=to_utc_z(row.get("last_login_at"), naive_strategy="utc", timespec="seconds"),
        createdAt=to_utc_z(row.get("created_at"), naive_strategy="utc", timespec="seconds"),
        taskCount=int(row.get("task_count") or 0),
        latestTaskAt=to_utc_z(row.get("latest_task_at"), naive_strategy="utc", timespec="seconds"),
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
        createdAt=to_utc_z(row.get("created_at"), naive_strategy="utc", timespec="seconds"),
        updatedAt=to_utc_z(row.get("updated_at"), naive_strategy="utc", timespec="seconds"),
        completedAt=to_utc_z(row.get("completed_at"), naive_strategy="utc", timespec="seconds"),
    )


def _normalize_task_type(value: Any) -> str:
    task_type = str(value or TaskType.PATENT_ANALYSIS.value).strip().lower()
    if task_type in ALLOWED_TASK_TYPES:
        return task_type
    return TaskType.PATENT_ANALYSIS.value


def _normalize_pn(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper()
    return text or None


def _norm_optional_local_datetime(value: Any, *, end_of_day: bool = False) -> Optional[str]:
    text = _norm_optional_text(value)
    if not text:
        return None
    try:
        return parse_local_input_to_utc_z(text, end_of_day=end_of_day, timespec="seconds")
    except Exception:
        return None


def _build_task_pdf_r2_key(task_type: str, pn: Optional[str], r2_storage: Any) -> Optional[str]:
    resolved_pn = _normalize_pn(pn)
    if not resolved_pn:
        return None
    if task_type == TaskType.AI_SEARCH.value:
        return None
    if task_type == TaskType.AI_REPLY.value:
        return r2_storage.build_ai_reply_pdf_key(resolved_pn)
    if task_type == TaskType.AI_REVIEW.value:
        return r2_storage.build_ai_review_pdf_key(resolved_pn)
    return r2_storage.build_patent_pdf_key(resolved_pn)


def _build_task_download_filename(task_type: str, pn: Optional[str], title: Optional[str], task_id: str) -> str:
    artifact_name = str(pn or title or task_id or "").strip() or task_id
    if task_type == TaskType.AI_SEARCH.value:
        return f"AI 检索结果_{artifact_name}.json"
    if task_type == TaskType.AI_REPLY.value:
        return f"AI 答复报告_{artifact_name}.pdf"
    if task_type == TaskType.AI_REVIEW.value:
        return f"AI 审查报告_{artifact_name}.pdf"
    return f"AI 分析报告_{artifact_name}.pdf"


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
        date_from=_norm_optional_local_datetime(dateFrom),
        date_to=_norm_optional_local_datetime(dateTo, end_of_day=True),
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
        "createdAt": to_utc_z(row.get("created_at"), naive_strategy="utc", timespec="seconds"),
        "updatedAt": to_utc_z(row.get("updated_at"), naive_strategy="utc", timespec="seconds"),
        "completedAt": to_utc_z(row.get("completed_at"), naive_strategy="utc", timespec="seconds"),
        "metadata": row.get("metadata"),
    }
    return AdminEntityTaskDetailResponse(item=item)


@router.get("/api/admin/entities/tasks/{task_id}/download")
async def download_admin_entity_task_result(
    task_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    ensure_admin_owner(current_user.user_id)

    if not hasattr(task_manager.storage, "get_admin_task_detail"):
        raise HTTPException(status_code=404, detail="任务不存在。")

    row = task_manager.storage.get_admin_task_detail(str(task_id or "").strip())
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在。")

    status = str(row.get("status") or "").strip().lower()
    if status != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成。")

    task_type = _normalize_task_type(row.get("task_type"))
    task_pn = str(row.get("pn") or "").strip() or None
    task_title = str(row.get("title") or "").strip() or None
    filename = _build_task_download_filename(task_type, task_pn, task_title, task_id)

    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    output_files = metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {}

    r2_storage = _build_r2_storage()
    r2_key = _build_task_pdf_r2_key(task_type, task_pn, r2_storage)
    if r2_key and r2_storage.enabled:
        r2_pdf = await asyncio.to_thread(r2_storage.get_bytes, r2_key)
        if r2_pdf:
            emit_system_log(
                category="task_execution",
                event_name="task_download",
                owner_id=current_user.user_id,
                task_id=task_id,
                task_type=task_type,
                success=True,
                message="管理员下载任务报告（R2）",
                payload={"filename": filename, "targetOwnerId": row.get("owner_id")},
            )
            return StreamingResponse(
                BytesIO(r2_pdf),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
                },
            )

    pdf_path_text = str(output_files.get("pdf") or "").strip()
    if pdf_path_text:
        pdf_path = Path(pdf_path_text)
    else:
        task_output_dir = Path(str(row.get("output_dir") or settings.OUTPUT_DIR / task_id))
        if task_type == TaskType.AI_REPLY.value:
            pdf_path = task_output_dir / "final_report.pdf"
        else:
            artifact_name = str(task_pn or task_id).strip() or task_id
            pdf_path = task_output_dir / f"{artifact_name}.pdf"

    if not pdf_path.exists():
        return JSONResponse(
            status_code=404,
            content={
                "error": "报告文件不存在",
                "message": f"未找到报告文件：{pdf_path}",
                "task_id": task_id,
                "suggestion": "请稍后重试或联系管理员。",
            },
        )

    emit_system_log(
        category="task_execution",
        event_name="task_download",
        owner_id=current_user.user_id,
        task_id=task_id,
        task_type=task_type,
        success=True,
        message="管理员下载任务报告",
        payload={"filename": filename, "targetOwnerId": row.get("owner_id")},
    )

    return FileResponse(
        path=str(pdf_path),
        filename=filename,
        media_type="application/pdf",
    )
