"""
管理员统计中心路由。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, Query

from backend.admin_auth import ensure_admin_owner, is_admin_owner
from backend.auth import _get_current_user
from backend.models import (
    AdminAccessResponse,
    AdminUsageDashboardResponse,
    AdminUsageOverview,
    AdminUsageSummary,
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


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)


@router.get("/api/admin/access", response_model=AdminAccessResponse)
async def get_admin_access(current_user: CurrentUser = Depends(_get_current_user)):
    return AdminAccessResponse(isAdmin=is_admin_owner(current_user.user_id))


@router.get("/api/admin/usage/dashboard", response_model=AdminUsageDashboardResponse)
async def get_admin_usage_dashboard(
    rangeType: Optional[str] = Query(default=DAY),
    anchor: Optional[str] = Query(default=None),
    current_user: CurrentUser = Depends(_get_current_user),
):
    ensure_admin_owner(current_user.user_id)
    normalized_range = _normalize_range_type(rangeType)
    normalized_anchor, start, end, rows = _load_rows(normalized_range, anchor)
    owner_ids: set[str] = set()
    total_tokens = 0
    total_cost = 0.0
    price_missing = False

    for row in rows:
        row_total_tokens = int(row.get("total_tokens") or 0)
        cost = float(row.get("estimated_cost_cny") or 0)
        owner_id = str(row.get("owner_id") or "").strip()
        if owner_id:
            owner_ids.add(owner_id)
        total_tokens += row_total_tokens
        total_cost += cost
        price_missing = price_missing or bool(row.get("price_missing"))

    task_count = len(rows)
    overview = AdminUsageOverview(
        totalTasks=task_count,
        totalUsers=len(owner_ids),
        totalTokens=total_tokens,
        totalEstimatedCostCny=round(total_cost, 6),
        avgTokensPerTask=round((total_tokens / task_count), 3) if task_count else 0.0,
        avgCostPerTaskCny=round((total_cost / task_count), 6) if task_count else 0.0,
        priceMissing=price_missing,
    )

    return AdminUsageDashboardResponse(
        rangeType=normalized_range,
        anchor=normalized_anchor,
        startAt=start.isoformat(),
        endAt=end.isoformat(),
        currency=TOKEN_PRICING_CURRENCY,
        overview=overview,
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
    normalized_anchor, start, end = _resolve_time_window(normalized_range, anchor)
    normalized_scope = str(scope or TASK).strip().lower()
    if normalized_scope not in {TASK, USER, ALL}:
        normalized_scope = TASK

    result = task_manager.storage.list_admin_usage_table(
        start_iso=start.isoformat(),
        end_iso=end.isoformat(),
        scope=normalized_scope,
        q=str(q or "").strip() or None,
        task_type=str(taskType or "").strip() or None,
        task_status=str(status or "").strip() or None,
        model=str(model or "").strip() or None,
        page=page,
        page_size=pageSize,
        sort_by=sortBy or ("lastUsageAt" if normalized_scope == TASK else "totalTokens"),
        sort_order=sortOrder or "desc",
    )

    summary_raw = result.get("summary") or {}
    summary = AdminUsageSummary(
        totalTasks=int(summary_raw.get("total_tasks") or 0),
        totalUsers=int(summary_raw.get("total_users") or 0),
        totalTokens=int(summary_raw.get("total_tokens") or 0),
        totalEstimatedCostCny=round(float(summary_raw.get("total_estimated_cost_cny") or 0), 6),
        totalLlmCallCount=int(summary_raw.get("total_llm_call_count") or 0),
        avgTokensPerEntity=round(float(summary_raw.get("avg_tokens_per_entity") or 0), 3),
        avgCostPerEntityCny=round(float(summary_raw.get("avg_cost_per_entity_cny") or 0), 6),
        entityType=str(summary_raw.get("entity_type") or normalized_scope),
        priceMissing=_to_bool(summary_raw.get("price_missing")),
    )

    items: List[Dict[str, Any]] = []
    for row in list(result.get("items") or []):
        if normalized_scope == TASK:
            items.append(
                {
                    "taskId": str(row.get("task_id") or ""),
                    "ownerId": str(row.get("owner_id") or "-"),
                    "userName": _normalize_user_name(row.get("user_name")),
                    "taskType": str(row.get("task_type") or ""),
                    "taskStatus": str(row.get("task_status") or ""),
                    "totalTokens": int(row.get("total_tokens") or 0),
                    "llmCallCount": int(row.get("llm_call_count") or 0),
                    "estimatedCostCny": round(float(row.get("estimated_cost_cny") or 0), 6),
                    "priceMissing": _to_bool(row.get("price_missing")),
                    "models": list(row.get("models") or []),
                    "lastUsageAt": row.get("last_usage_at"),
                }
            )
            continue
        if normalized_scope == USER:
            items.append(
                {
                    "ownerId": str(row.get("owner_id") or "-"),
                    "userName": _normalize_user_name(row.get("user_name")),
                    "taskCount": int(row.get("task_count") or 0),
                    "totalTokens": int(row.get("total_tokens") or 0),
                    "llmCallCount": int(row.get("llm_call_count") or 0),
                    "estimatedCostCny": round(float(row.get("estimated_cost_cny") or 0), 6),
                    "priceMissing": _to_bool(row.get("price_missing")),
                    "latestUsageAt": row.get("latest_usage_at"),
                }
            )
            continue
        items.append(
            {
                "taskCount": int(row.get("task_count") or 0),
                "userCount": int(row.get("user_count") or 0),
                "totalTokens": int(row.get("total_tokens") or 0),
                "llmCallCount": int(row.get("llm_call_count") or 0),
                "estimatedCostCny": round(float(row.get("estimated_cost_cny") or 0), 6),
                "priceMissing": _to_bool(row.get("price_missing")),
            }
        )

    return AdminUsageTableResponse(
        scope=normalized_scope,
        rangeType=normalized_range,
        anchor=normalized_anchor,
        currency=TOKEN_PRICING_CURRENCY,
        page=page if normalized_scope != ALL else 1,
        pageSize=pageSize if normalized_scope != ALL else 1,
        total=int(result.get("total") or 0),
        priceMissing=_to_bool(result.get("price_missing")),
        summary=summary,
        items=items,
    )
