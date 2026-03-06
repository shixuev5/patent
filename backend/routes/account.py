"""
个人空间相关路由
"""
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth import _get_current_user
from backend.models import (
    AccountDashboardResponse,
    AccountMonthTargetResponse,
    AccountMonthTargetUpsertRequest,
    AccountProfileResponse,
    CurrentUser,
    DailyActivityPoint,
    TaskWindowCounts,
    WeeklyActivityPoint,
)
from backend.storage import TaskType, get_pipeline_manager


router = APIRouter()
task_manager = get_pipeline_manager()


def _is_workday(day: date) -> bool:
    return day.weekday() < 5


def _iter_dates(start_day: date, end_day: date):
    cursor = start_day
    while cursor <= end_day:
        yield cursor
        cursor += timedelta(days=1)


def _month_start_end(year: int, month: int) -> Tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return start, end


def _recent_workday_window(days: int, today: date) -> Tuple[date, date]:
    picked: List[date] = []
    cursor = today
    while len(picked) < days:
        if _is_workday(cursor):
            picked.append(cursor)
        cursor -= timedelta(days=1)
    start = picked[-1]
    end = picked[0]
    return start, end


def _datetime_bounds(start_day: date, end_day: date) -> Tuple[str, str]:
    start_dt = datetime.combine(start_day, datetime.min.time())
    end_dt = datetime.combine(end_day + timedelta(days=1), datetime.min.time())
    return start_dt.isoformat(), end_dt.isoformat()


def _build_summary_text(work_week_total: int, work_month_total: int, weekly_series: List[WeeklyActivityPoint]) -> str:
    if work_month_total == 0:
        return "本月暂无任务创建记录，可以从 AI 分析任务开始沉淀个人节奏。"

    first_half = sum(item.totalCreated for item in weekly_series[:2])
    second_half = sum(item.totalCreated for item in weekly_series[2:])
    pace_estimate = work_week_total * 4

    if second_half > first_half * 1.1:
        return "最近两周活跃度高于月初，任务节奏在加速。"
    if second_half < first_half * 0.9:
        return "最近两周活跃度低于月初，建议适当补齐任务节奏。"
    if pace_estimate > work_month_total * 1.15:
        return "最近一个工作周创建节奏较快，建议持续优先处理高价值任务。"
    if pace_estimate < work_month_total * 0.85:
        return "最近一个工作周创建节奏偏缓，可优先安排高价值任务。"
    return "当前任务节奏整体稳定，可持续保持。"


def _count_created(owner_id: str, start_day: date, end_day: date, task_type: str) -> int:
    start_iso, end_iso = _datetime_bounds(start_day, end_day)
    return task_manager.storage.count_user_tasks_by_created_range(
        owner_id,
        start_iso,
        end_iso,
        task_type=task_type,
    )


def _resolve_effective_month_target(owner_id: str, year: int, month: int) -> Tuple[int, str]:
    explicit = task_manager.storage.get_account_month_target(owner_id, year, month)
    if explicit:
        return int(explicit.target_count), "explicit"

    latest_before = task_manager.storage.get_latest_account_month_target_before(owner_id, year, month)
    if latest_before:
        return int(latest_before.target_count), "carried"

    return 0, "empty"


def _normalize_year_month(year: int, month: int, now: datetime) -> Tuple[int, int]:
    actual_year = int(year or now.year)
    actual_month = int(month or now.month)
    if actual_month < 1 or actual_month > 12:
        actual_month = now.month
        actual_year = now.year
    return actual_year, actual_month


@router.get("/api/account/profile", response_model=AccountProfileResponse)
async def get_account_profile(current_user: CurrentUser = Depends(_get_current_user)):
    user = task_manager.storage.get_user_by_owner_id(current_user.user_id)
    if not user:
        return AccountProfileResponse(ownerId=current_user.user_id, authType="guest")
    return AccountProfileResponse(
        ownerId=user.owner_id,
        authType="authing",
        name=user.name,
        nickname=user.nickname,
        email=user.email,
        phone=user.phone,
        picture=user.picture,
    )


@router.get("/api/account/month-target", response_model=AccountMonthTargetResponse)
async def get_account_month_target(
    year: int = Query(default_factory=lambda: datetime.now().year),
    month: int = Query(default_factory=lambda: datetime.now().month),
    current_user: CurrentUser = Depends(_get_current_user),
):
    now = datetime.now()
    actual_year, actual_month = _normalize_year_month(year, month, now)
    target_count, source = _resolve_effective_month_target(current_user.user_id, actual_year, actual_month)
    return AccountMonthTargetResponse(
        year=actual_year,
        month=actual_month,
        targetCount=target_count,
        source=source,
    )


@router.put("/api/account/month-target", response_model=AccountMonthTargetResponse)
async def put_account_month_target(
    payload: AccountMonthTargetUpsertRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    if payload.month < 1 or payload.month > 12:
        raise HTTPException(status_code=400, detail="month 必须在 1-12 之间。")
    if payload.targetCount < 0:
        raise HTTPException(status_code=400, detail="targetCount 不能小于 0。")

    saved = task_manager.storage.upsert_account_month_target(
        current_user.user_id,
        int(payload.year),
        int(payload.month),
        int(payload.targetCount),
    )
    return AccountMonthTargetResponse(
        year=int(saved.year),
        month=int(saved.month),
        targetCount=int(saved.target_count),
        source="explicit",
    )


@router.get("/api/account/dashboard", response_model=AccountDashboardResponse)
async def get_account_dashboard(
    year: int = Query(default_factory=lambda: datetime.now().year),
    month: int = Query(default_factory=lambda: datetime.now().month),
    current_user: CurrentUser = Depends(_get_current_user),
):
    now = datetime.now()
    actual_year, actual_month = _normalize_year_month(year, month, now)

    month_start_dt, month_end_dt = _month_start_end(actual_year, actual_month)
    month_start_day = month_start_dt.date()
    month_end_day = (month_end_dt - timedelta(days=1)).date()

    week_start, week_end = _recent_workday_window(5, now.date())
    month_work_start, month_work_end = _recent_workday_window(22, now.date())

    work_week_analysis = _count_created(current_user.user_id, week_start, week_end, TaskType.PATENT_ANALYSIS.value)
    work_week_reply = _count_created(current_user.user_id, week_start, week_end, TaskType.OFFICE_ACTION_REPLY.value)
    work_month_analysis = _count_created(current_user.user_id, month_work_start, month_work_end, TaskType.PATENT_ANALYSIS.value)
    work_month_reply = _count_created(current_user.user_id, month_work_start, month_work_end, TaskType.OFFICE_ACTION_REPLY.value)

    created_rows = task_manager.storage.aggregate_user_created_tasks_daily(
        current_user.user_id,
        month_start_day,
        month_end_day,
    )
    daily_map: Dict[str, Dict[str, int]] = {}
    for row in created_rows:
        key = row["day"]
        item = daily_map.setdefault(key, {"analysis": 0, "reply": 0})
        if row["task_type"] == TaskType.PATENT_ANALYSIS.value:
            item["analysis"] += int(row["count"])
        elif row["task_type"] == TaskType.OFFICE_ACTION_REPLY.value:
            item["reply"] += int(row["count"])

    weekly_bucket = [
        {"analysis": 0, "reply": 0},
        {"analysis": 0, "reply": 0},
        {"analysis": 0, "reply": 0},
        {"analysis": 0, "reply": 0},
    ]
    daily_series: List[DailyActivityPoint] = []
    for day_item in _iter_dates(month_start_day, month_end_day):
        key = day_item.isoformat()
        row = daily_map.get(key, {"analysis": 0, "reply": 0})
        analysis = int(row["analysis"])
        reply = int(row["reply"])
        total = analysis + reply
        daily_series.append(
            DailyActivityPoint(
                date=key,
                analysisCreated=analysis,
                replyCreated=reply,
                totalCreated=total,
            )
        )

        week_index = min(3, (day_item.day - 1) // 7)
        weekly_bucket[week_index]["analysis"] += analysis
        weekly_bucket[week_index]["reply"] += reply

    weekly_series: List[WeeklyActivityPoint] = []
    for idx in range(4):
        analysis = weekly_bucket[idx]["analysis"]
        reply = weekly_bucket[idx]["reply"]
        weekly_series.append(
            WeeklyActivityPoint(
                week=f"第{idx + 1}周",
                analysisCreated=analysis,
                replyCreated=reply,
                totalCreated=analysis + reply,
            )
        )

    work_week = TaskWindowCounts(
        analysisCount=work_week_analysis,
        replyCount=work_week_reply,
        totalCount=work_week_analysis + work_week_reply,
    )
    work_month = TaskWindowCounts(
        analysisCount=work_month_analysis,
        replyCount=work_month_reply,
        totalCount=work_month_analysis + work_month_reply,
    )
    month_target, month_target_source = _resolve_effective_month_target(
        current_user.user_id,
        actual_year,
        actual_month,
    )

    return AccountDashboardResponse(
        year=actual_year,
        month=actual_month,
        monthTarget=int(month_target),
        monthTargetSource=month_target_source,
        workWeek=work_week,
        workMonth=work_month,
        summaryText=_build_summary_text(work_week.totalCount, work_month.totalCount, weekly_series),
        weeklySeries=weekly_series,
        dailySeries=daily_series,
    )
