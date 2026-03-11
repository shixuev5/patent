"""
管理员统计中心路由。
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, Query

from backend.admin_auth import ensure_admin_owner, is_admin_owner
from backend.auth import _get_current_user
from backend.models import (
    AdminAccessResponse,
    AdminUsageDashboardResponse,
    AdminUsageOverview,
    AdminUsageTableResponse,
)
from backend.models import CurrentUser
from backend.token_pricing import TOKEN_PRICING_CURRENCY
from backend.storage import get_pipeline_manager


router = APIRouter()
task_manager = get_pipeline_manager()
RangeType = Literal["day", "month", "year"]
ScopeType = Literal["task", "user", "all"]
DAY = "day"
MONTH = "month"
YEAR = "year"
TASK = "task"
USER = "user"
ALL = "all"


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _normalize_range_type(raw: Optional[str]) -> RangeType:
    text = str(raw or DAY).strip().lower()
    if text in {DAY, MONTH, YEAR}:
        return text  # type: ignore[return-value]
    return DAY


def _resolve_time_window(range_type: RangeType, anchor: Optional[str]) -> Tuple[str, datetime, datetime]:
    now = datetime.now()
    raw_anchor = str(anchor or "").strip()

    if range_type == DAY:
        if raw_anchor:
            try:
                day = date.fromisoformat(raw_anchor)
            except ValueError:
                day = now.date()
        else:
            day = now.date()
        start = datetime.combine(day, datetime.min.time())
        end = start + timedelta(days=1)
        return day.isoformat(), start, end

    if range_type == MONTH:
        if raw_anchor:
            parts = raw_anchor.split("-")
            try:
                year = int(parts[0])
                month = int(parts[1])
            except Exception:
                year = now.year
                month = now.month
        else:
            year = now.year
            month = now.month
        if month < 1 or month > 12:
            month = now.month
            year = now.year
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)
        return f"{year:04d}-{month:02d}", start, end

    if raw_anchor:
        try:
            target_year = int(raw_anchor)
        except ValueError:
            target_year = now.year
    else:
        target_year = now.year
    start = datetime(target_year, 1, 1)
    end = datetime(target_year + 1, 1, 1)
    return str(target_year), start, end


def _build_series_labels(range_type: RangeType, anchor: str) -> List[str]:
    if range_type == DAY:
        return [f"{hour:02d}:00" for hour in range(24)]
    if range_type == MONTH:
        year, month = map(int, anchor.split("-"))
        days = calendar.monthrange(year, month)[1]
        return [f"{day:02d}" for day in range(1, days + 1)]
    return [f"{month:02d}" for month in range(1, 13)]


def _resolve_bucket_label(range_type: RangeType, dt: datetime) -> str:
    if range_type == DAY:
        return f"{dt.hour:02d}:00"
    if range_type == MONTH:
        return f"{dt.day:02d}"
    return f"{dt.month:02d}"


def _load_rows(range_type: RangeType, anchor: Optional[str]) -> Tuple[str, datetime, datetime, List[Dict[str, Any]]]:
    normalized_anchor, start, end = _resolve_time_window(range_type, anchor)
    rows = task_manager.storage.list_task_llm_usage_by_last_usage_range(
        start_iso=start.isoformat(),
        end_iso=end.isoformat(),
    )
    return normalized_anchor, start, end, rows


def _row_models(row: Dict[str, Any]) -> List[str]:
    breakdown = row.get("model_breakdown_json")
    if isinstance(breakdown, dict):
        return [str(key) for key in breakdown.keys()]
    return []


def _normalize_user_name(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.lower() in {"none", "null", "undefined"}:
        return None
    return text


def _resolve_user_name(owner_id: str, cache: Dict[str, Optional[str]]) -> Optional[str]:
    key = str(owner_id or "").strip()
    if not key:
        return None
    if key in cache:
        return cache[key]
    name: Optional[str] = None
    if hasattr(task_manager.storage, "get_user_by_owner_id"):
        try:
            user = task_manager.storage.get_user_by_owner_id(key)
            if user:
                name = _normalize_user_name(getattr(user, "name", None))
        except Exception:
            name = None
    cache[key] = name
    return name


def _to_task_table_item(row: Dict[str, Any], user_name: Optional[str]) -> Dict[str, Any]:
    return {
        "taskId": row.get("task_id", ""),
        "ownerId": row.get("owner_id", ""),
        "userName": user_name,
        "taskType": row.get("task_type", ""),
        "taskStatus": row.get("task_status", ""),
        "promptTokens": int(row.get("prompt_tokens") or 0),
        "completionTokens": int(row.get("completion_tokens") or 0),
        "totalTokens": int(row.get("total_tokens") or 0),
        "reasoningTokens": int(row.get("reasoning_tokens") or 0),
        "llmCallCount": int(row.get("llm_call_count") or 0),
        "estimatedCostCny": float(row.get("estimated_cost_cny") or 0),
        "priceMissing": bool(row.get("price_missing")),
        "models": _row_models(row),
        "firstUsageAt": row.get("first_usage_at"),
        "lastUsageAt": row.get("last_usage_at"),
        "updatedAt": row.get("updated_at"),
    }


def _sort_items(items: List[Dict[str, Any]], sort_by: str, sort_order: str) -> List[Dict[str, Any]]:
    order = str(sort_order or "desc").strip().lower()
    reverse = order != "asc"

    def key_func(item: Dict[str, Any]):
        return item.get(sort_by)

    try:
        return sorted(items, key=key_func, reverse=reverse)
    except TypeError:
        return items


@router.get("/api/admin/access", response_model=AdminAccessResponse)
async def get_admin_access(current_user: CurrentUser = Depends(_get_current_user)):
    return AdminAccessResponse(isAdmin=is_admin_owner(current_user.user_id))


@router.get("/api/admin/usage/dashboard", response_model=AdminUsageDashboardResponse)
async def get_admin_usage_dashboard(
    rangeType: Optional[str] = Query(default=DAY),
    anchor: Optional[str] = Query(default=None),
    topN: int = Query(default=10, ge=1, le=50),
    current_user: CurrentUser = Depends(_get_current_user),
):
    ensure_admin_owner(current_user.user_id)
    normalized_range = _normalize_range_type(rangeType)
    normalized_anchor, start, end, rows = _load_rows(normalized_range, anchor)
    labels = _build_series_labels(normalized_range, normalized_anchor)

    trend_map: Dict[str, Dict[str, Any]] = {
        label: {
            "label": label,
            "promptTokens": 0,
            "completionTokens": 0,
            "totalTokens": 0,
            "estimatedCostCny": 0.0,
        }
        for label in labels
    }
    task_type_map: Dict[str, Dict[str, Any]] = {}
    user_map: Dict[str, Dict[str, Any]] = {}
    user_name_cache: Dict[str, Optional[str]] = {}
    total_prompt = 0
    total_completion = 0
    total_tokens = 0
    total_reasoning = 0
    total_cost = 0.0
    price_missing = False

    for row in rows:
        prompt_tokens = int(row.get("prompt_tokens") or 0)
        completion_tokens = int(row.get("completion_tokens") or 0)
        row_total_tokens = int(row.get("total_tokens") or 0)
        reasoning_tokens = int(row.get("reasoning_tokens") or 0)
        cost = float(row.get("estimated_cost_cny") or 0)
        task_type = str(row.get("task_type") or "unknown")
        owner_id = str(row.get("owner_id") or "")
        user_name = _resolve_user_name(owner_id, user_name_cache)
        status = str(row.get("task_status") or "")

        total_prompt += prompt_tokens
        total_completion += completion_tokens
        total_tokens += row_total_tokens
        total_reasoning += reasoning_tokens
        total_cost += cost
        price_missing = price_missing or bool(row.get("price_missing"))

        dt = _parse_datetime(row.get("last_usage_at"))
        if dt:
            label = _resolve_bucket_label(normalized_range, dt)
            if label in trend_map:
                trend_map[label]["promptTokens"] += prompt_tokens
                trend_map[label]["completionTokens"] += completion_tokens
                trend_map[label]["totalTokens"] += row_total_tokens
                trend_map[label]["estimatedCostCny"] += cost

        task_item = task_type_map.setdefault(
            task_type,
            {
                "taskType": task_type,
                "taskCount": 0,
                "totalTokens": 0,
                "estimatedCostCny": 0.0,
            },
        )
        task_item["taskCount"] += 1
        task_item["totalTokens"] += row_total_tokens
        task_item["estimatedCostCny"] += cost

        user_item = user_map.setdefault(
            owner_id or "-",
            {
                "ownerId": owner_id or "-",
                "userName": user_name,
                "taskCount": 0,
                "totalTokens": 0,
                "estimatedCostCny": 0.0,
                "priceMissing": False,
                "latestTaskStatus": status,
            },
        )
        if user_name and not user_item.get("userName"):
            user_item["userName"] = user_name
        user_item["taskCount"] += 1
        user_item["totalTokens"] += row_total_tokens
        user_item["estimatedCostCny"] += cost
        user_item["priceMissing"] = user_item["priceMissing"] or bool(row.get("price_missing"))
        if status:
            user_item["latestTaskStatus"] = status

    top_users = sorted(
        user_map.values(),
        key=lambda item: (int(item["totalTokens"]), float(item["estimatedCostCny"])),
        reverse=True,
    )[:topN]

    task_count = len(rows)
    overview = AdminUsageOverview(
        totalTasks=task_count,
        totalUsers=len([owner for owner in user_map.keys() if owner and owner != "-"]),
        totalPromptTokens=total_prompt,
        totalCompletionTokens=total_completion,
        totalTokens=total_tokens,
        totalReasoningTokens=total_reasoning,
        totalEstimatedCostCny=round(total_cost, 6),
        avgTokensPerTask=round((total_tokens / task_count), 3) if task_count else 0.0,
        avgCostPerTaskCny=round((total_cost / task_count), 6) if task_count else 0.0,
        priceMissing=price_missing,
    )

    trend = [trend_map[label] for label in labels]
    by_task_type = sorted(task_type_map.values(), key=lambda item: int(item["totalTokens"]), reverse=True)

    return AdminUsageDashboardResponse(
        rangeType=normalized_range,
        anchor=normalized_anchor,
        startAt=start.isoformat(),
        endAt=end.isoformat(),
        currency=TOKEN_PRICING_CURRENCY,
        overview=overview,
        trend=trend,
        byTaskType=by_task_type,
        topUsers=top_users,
        priceMissing=price_missing,
    )


@router.get("/api/admin/usage/table", response_model=AdminUsageTableResponse)
async def get_admin_usage_table(
    rangeType: Optional[str] = Query(default=DAY),
    anchor: Optional[str] = Query(default=None),
    scope: Optional[str] = Query(default=TASK),
    q: Optional[str] = Query(default=None),
    taskType: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=10, ge=1, le=200),
    sortBy: Optional[str] = Query(default="lastUsageAt"),
    sortOrder: Optional[str] = Query(default="desc"),
    current_user: CurrentUser = Depends(_get_current_user),
):
    ensure_admin_owner(current_user.user_id)
    normalized_range = _normalize_range_type(rangeType)
    normalized_anchor, _, _, rows = _load_rows(normalized_range, anchor)
    normalized_scope = str(scope or TASK).strip().lower()
    if normalized_scope not in {TASK, USER, ALL}:
        normalized_scope = TASK

    normalized_q = str(q or "").strip().lower()
    normalized_task_type = str(taskType or "").strip().lower()
    normalized_status = str(status or "").strip().lower()
    normalized_model = str(model or "").strip().lower()
    user_name_cache: Dict[str, Optional[str]] = {}

    filtered_task_items: List[Dict[str, Any]] = []
    for row in rows:
        owner_id = str(row.get("owner_id") or "")
        task_item = _to_task_table_item(row, _resolve_user_name(owner_id, user_name_cache))
        row_task_type = str(task_item["taskType"]).strip().lower()
        row_status = str(task_item["taskStatus"]).strip().lower()
        row_models = [str(item or "") for item in task_item["models"]]
        row_models_lower = [item.lower() for item in row_models]
        row_model_text = " ".join(row_models).lower()

        if normalized_task_type and row_task_type != normalized_task_type:
            continue
        if normalized_status and row_status != normalized_status:
            continue
        if normalized_model and normalized_model not in row_models_lower:
            continue

        if normalized_q:
            haystack = " ".join(
                [
                    str(task_item["taskId"]),
                    str(task_item.get("userName") or ""),
                    str(task_item["taskType"]),
                    str(task_item["taskStatus"]),
                    row_model_text,
                ]
            ).lower()
            if normalized_q not in haystack:
                continue

        filtered_task_items.append(task_item)

    price_missing = any(bool(item.get("priceMissing")) for item in filtered_task_items)

    if normalized_scope == TASK:
        sortable = _sort_items(filtered_task_items, sortBy or "lastUsageAt", sortOrder or "desc")
        total = len(sortable)
        start = (page - 1) * pageSize
        end = start + pageSize
        items = sortable[start:end]
        return AdminUsageTableResponse(
            scope=TASK,
            rangeType=normalized_range,
            anchor=normalized_anchor,
            currency=TOKEN_PRICING_CURRENCY,
            page=page,
            pageSize=pageSize,
            total=total,
            priceMissing=price_missing,
            items=items,
        )

    if normalized_scope == USER:
        user_map: Dict[str, Dict[str, Any]] = {}
        for item in filtered_task_items:
            owner_id = str(item.get("ownerId") or "-")
            target = user_map.setdefault(
                owner_id,
                {
                    "ownerId": owner_id,
                    "userName": item.get("userName"),
                    "taskCount": 0,
                    "promptTokens": 0,
                    "completionTokens": 0,
                    "totalTokens": 0,
                    "reasoningTokens": 0,
                    "llmCallCount": 0,
                    "estimatedCostCny": 0.0,
                    "priceMissing": False,
                    "latestUsageAt": item.get("lastUsageAt"),
                },
            )
            if item.get("userName") and not target.get("userName"):
                target["userName"] = item.get("userName")
            target["taskCount"] += 1
            target["promptTokens"] += int(item.get("promptTokens") or 0)
            target["completionTokens"] += int(item.get("completionTokens") or 0)
            target["totalTokens"] += int(item.get("totalTokens") or 0)
            target["reasoningTokens"] += int(item.get("reasoningTokens") or 0)
            target["llmCallCount"] += int(item.get("llmCallCount") or 0)
            target["estimatedCostCny"] += float(item.get("estimatedCostCny") or 0)
            target["priceMissing"] = target["priceMissing"] or bool(item.get("priceMissing"))
            latest = _parse_datetime(target.get("latestUsageAt"))
            candidate = _parse_datetime(item.get("lastUsageAt"))
            if candidate and (not latest or candidate > latest):
                target["latestUsageAt"] = item.get("lastUsageAt")

        user_items = list(user_map.values())
        sortable = _sort_items(user_items, sortBy or "totalTokens", sortOrder or "desc")
        total = len(sortable)
        start = (page - 1) * pageSize
        end = start + pageSize
        items = sortable[start:end]
        return AdminUsageTableResponse(
            scope=USER,
            rangeType=normalized_range,
            anchor=normalized_anchor,
            currency=TOKEN_PRICING_CURRENCY,
            page=page,
            pageSize=pageSize,
            total=total,
            priceMissing=any(bool(item.get("priceMissing")) for item in user_items),
            items=items,
        )

    all_summary = {
        "taskCount": len(filtered_task_items),
        "userCount": len({str(item.get("ownerId") or "-") for item in filtered_task_items}),
        "promptTokens": sum(int(item.get("promptTokens") or 0) for item in filtered_task_items),
        "completionTokens": sum(int(item.get("completionTokens") or 0) for item in filtered_task_items),
        "totalTokens": sum(int(item.get("totalTokens") or 0) for item in filtered_task_items),
        "reasoningTokens": sum(int(item.get("reasoningTokens") or 0) for item in filtered_task_items),
        "llmCallCount": sum(int(item.get("llmCallCount") or 0) for item in filtered_task_items),
        "estimatedCostCny": round(sum(float(item.get("estimatedCostCny") or 0) for item in filtered_task_items), 6),
        "priceMissing": price_missing,
    }
    items: List[Dict[str, Any]] = [all_summary] if filtered_task_items else []
    return AdminUsageTableResponse(
        scope=ALL,
        rangeType=normalized_range,
        anchor=normalized_anchor,
        currency=TOKEN_PRICING_CURRENCY,
        page=1,
        pageSize=1,
        total=len(items),
        priceMissing=price_missing,
        items=items,
    )
