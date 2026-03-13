"""
管理员系统日志查询路由。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.admin_auth import ensure_admin_owner
from backend.auth import _get_current_user
from backend.models import (
    AdminSystemLogDetailResponse,
    AdminSystemLogItem,
    AdminSystemLogListResponse,
    AdminSystemLogSummaryResponse,
    CurrentUser,
)
from backend.storage import get_pipeline_manager
from backend.system_logs import resolve_payload_from_record


router = APIRouter()
task_manager = get_pipeline_manager()
APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))


def _norm_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _norm_optional_datetime(value: Any) -> Optional[str]:
    text = _norm_optional_text(value)
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except Exception:
        return text
    if parsed.tzinfo is None:
        return parsed.isoformat(timespec="seconds")
    local_dt = parsed.astimezone(APP_TZ).replace(tzinfo=None)
    return local_dt.isoformat(timespec="seconds")


def _to_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _to_item(row: Dict[str, Any]) -> AdminSystemLogItem:
    return AdminSystemLogItem(
        logId=str(row.get("log_id") or ""),
        timestamp=str(row.get("timestamp") or ""),
        category=str(row.get("category") or ""),
        eventName=str(row.get("event_name") or ""),
        level=str(row.get("level") or "INFO"),
        ownerId=row.get("owner_id"),
        userName=row.get("user_name"),
        taskId=row.get("task_id"),
        taskType=row.get("task_type"),
        requestId=row.get("request_id"),
        traceId=row.get("trace_id"),
        method=row.get("method"),
        path=row.get("path"),
        statusCode=row.get("status_code"),
        durationMs=row.get("duration_ms"),
        provider=row.get("provider"),
        targetHost=row.get("target_host"),
        success=bool(row.get("success")),
        message=row.get("message"),
        payloadBytes=int(row.get("payload_bytes") or 0),
        payloadOverflow=bool(row.get("payload_overflow")),
        createdAt=str(row.get("created_at") or ""),
    )


@router.get("/api/admin/logs/summary", response_model=AdminSystemLogSummaryResponse)
async def get_admin_system_log_summary(
    dateFrom: Optional[str] = None,
    dateTo: Optional[str] = None,
    current_user: CurrentUser = Depends(_get_current_user),
):
    ensure_admin_owner(current_user.user_id)
    normalized_date_from = _norm_optional_datetime(dateFrom)
    normalized_date_to = _norm_optional_datetime(dateTo)
    if hasattr(task_manager.storage, "summarize_system_logs"):
        summary = task_manager.storage.summarize_system_logs(
            date_from=normalized_date_from,
            date_to=normalized_date_to,
        )
    else:
        summary = {
            "totalLogs": 0,
            "failedLogs": 0,
            "failedRate": 0.0,
            "llmCallCount": 0,
            "byCategory": [],
        }
    return AdminSystemLogSummaryResponse(**summary)


@router.get("/api/admin/logs", response_model=AdminSystemLogListResponse)
async def get_admin_system_logs(
    category: Optional[str] = Query(default=None),
    eventName: Optional[str] = Query(default=None),
    ownerId: Optional[str] = Query(default=None),
    userName: Optional[str] = Query(default=None),
    taskId: Optional[str] = Query(default=None),
    requestId: Optional[str] = Query(default=None),
    traceId: Optional[str] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    success: Optional[str] = Query(default=None),
    dateFrom: Optional[str] = Query(default=None),
    dateTo: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=200),
    current_user: CurrentUser = Depends(_get_current_user),
):
    ensure_admin_owner(current_user.user_id)

    success_value = _to_bool(_norm_optional_text(success))
    if hasattr(task_manager.storage, "list_system_logs"):
        result = task_manager.storage.list_system_logs(
            category=_norm_optional_text(category),
            event_name=_norm_optional_text(eventName),
            owner_id=_norm_optional_text(ownerId),
            user_name=_norm_optional_text(userName),
            task_id=_norm_optional_text(taskId),
            request_id=_norm_optional_text(requestId),
            trace_id=_norm_optional_text(traceId),
            provider=_norm_optional_text(provider),
            success=success_value,
            date_from=_norm_optional_datetime(dateFrom),
            date_to=_norm_optional_datetime(dateTo),
            q=_norm_optional_text(q),
            page=page,
            page_size=pageSize,
        )
    else:
        result = {"total": 0, "items": []}

    rows = result.get("items") or []
    return AdminSystemLogListResponse(
        page=page,
        pageSize=pageSize,
        total=int(result.get("total") or 0),
        items=[_to_item(row) for row in rows],
    )


@router.get("/api/admin/logs/{log_id}", response_model=AdminSystemLogDetailResponse)
async def get_admin_system_log_detail(
    log_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    ensure_admin_owner(current_user.user_id)
    if not hasattr(task_manager.storage, "get_system_log"):
        raise HTTPException(status_code=404, detail="日志不存在。")

    row = task_manager.storage.get_system_log(log_id)
    if not row:
        raise HTTPException(status_code=404, detail="日志不存在。")
    owner_id = _norm_optional_text(row.get("owner_id"))
    if owner_id and not row.get("user_name") and hasattr(task_manager.storage, "get_user_by_owner_id"):
        user = task_manager.storage.get_user_by_owner_id(owner_id)
        if user:
            user_name = str(getattr(user, "name", "") or "").strip()
            if user_name:
                row = {**row, "user_name": user_name}

    payload = resolve_payload_from_record(row)
    item = _to_item(row)
    return AdminSystemLogDetailResponse(item=item, payload=payload)
