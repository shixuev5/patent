"""
个人空间相关路由
"""
import io
import mimetypes
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from loguru import logger

from backend.auth import _get_current_user
from backend.models import (
    AccountAvatarUploadResponse,
    AccountDashboardResponse,
    AccountMonthTargetResponse,
    AccountMonthTargetUpsertRequest,
    AccountNotificationSettingsResponse,
    AccountNotificationSettingsUpdateRequest,
    AccountProfileResponse,
    AccountProfileUpdateRequest,
    CurrentUser,
    DailyActivityPoint,
    TaskWindowCounts,
    WeeklyActivityPoint,
)
from backend.utils import _build_r2_storage
from backend.storage import TaskType, get_pipeline_manager
from backend.time_utils import APP_TZ, local_day_start_end_to_utc, utc_now
from config import settings


router = APIRouter()
task_manager = get_pipeline_manager()
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
AVATAR_LOCAL_DIR = settings.UPLOAD_DIR / "avatars"
SAFE_AVATAR_FILE_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")
EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}$", re.IGNORECASE)
AVATAR_READ_PREFIX = "/api/account/profile/avatar/"
AVATAR_REF_R2_PREFIX = "r2/"
AVATAR_REF_LOCAL_PREFIX = "local/"


def _sanitize_profile_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in {"none", "null", "undefined"}:
        return None
    return text


def _normalize_profile_name(value, *, required: bool = False) -> str | None:
    text = _sanitize_profile_text(value)
    if text is None:
        if required:
            raise HTTPException(status_code=400, detail="显示名称不能为空。")
        return None
    if len(text) > 32:
        raise HTTPException(status_code=400, detail="显示名称不能超过 32 个字符。")
    return text


def _is_name_taken(name: str, owner_id: str) -> bool:
    storage = task_manager.storage
    if not hasattr(storage, "get_user_by_name"):
        return False
    matched = storage.get_user_by_name(name)
    if not matched:
        return False
    return str(matched.owner_id or "").strip() != str(owner_id or "").strip()


def _ensure_name_unique(name: str, owner_id: str):
    if _is_name_taken(name, owner_id):
        raise HTTPException(status_code=409, detail="显示名称已被占用，请更换后重试。")


def _normalize_profile_picture(value) -> str | None:
    text = _sanitize_profile_text(value)
    if text is None:
        return None
    if len(text) > 1024:
        raise HTTPException(status_code=400, detail="头像地址长度不能超过 1024。")

    path = urlparse(text).path or ""
    if not path.startswith(AVATAR_READ_PREFIX):
        raise HTTPException(status_code=400, detail="头像地址无效，请通过头像上传接口获取。")
    ref = path[len(AVATAR_READ_PREFIX):].strip()
    if not ref or (
        not ref.startswith(AVATAR_REF_R2_PREFIX)
        and not ref.startswith(AVATAR_REF_LOCAL_PREFIX)
    ):
        raise HTTPException(status_code=400, detail="头像地址无效，请通过头像上传接口获取。")
    return text


def _ensure_auth_user_or_404(owner_id: str):
    user = task_manager.storage.get_user_by_owner_id(owner_id)
    if not user:
        raise HTTPException(status_code=404, detail="未找到认证账号档案。")
    return user


def _ensure_authing_user_or_403(current_user: CurrentUser):
    owner_id = str(current_user.user_id or "").strip()
    if not owner_id or owner_id.startswith("guest"):
        raise HTTPException(status_code=403, detail="仅认证用户可配置邮件通知。")
    return _ensure_auth_user_or_404(owner_id)


def _normalize_notification_email(value) -> str | None:
    text = _sanitize_profile_text(value)
    if text is None:
        return None
    if len(text) > 254:
        raise HTTPException(status_code=400, detail="邮箱长度不能超过 254 个字符。")
    if not EMAIL_PATTERN.match(text):
        raise HTTPException(status_code=400, detail="邮箱格式无效，请检查后重试。")
    return text


def _build_notification_settings_response(user) -> AccountNotificationSettingsResponse:
    return AccountNotificationSettingsResponse(
        notificationEmailEnabled=bool(getattr(user, "notification_email_enabled", False)),
        workNotificationEmail=_sanitize_profile_text(getattr(user, "work_notification_email", None)),
        personalNotificationEmail=_sanitize_profile_text(getattr(user, "personal_notification_email", None)),
    )


def _build_avatar_filename(owner_id: str, suffix: str) -> str:
    owner_token = re.sub(r"[^a-zA-Z0-9_-]+", "-", owner_id).strip("-") or "user"
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    rand = uuid.uuid4().hex[:10]
    return f"{owner_token}_{ts}_{rand}{suffix}"


def _extract_avatar_ref(picture_url: str | None) -> Tuple[str, str] | None:
    text = _sanitize_profile_text(picture_url)
    if not text:
        return None
    path = urlparse(text).path or ""
    if not path.startswith(AVATAR_READ_PREFIX):
        return None
    ref = path[len(AVATAR_READ_PREFIX):].strip()
    if ref.startswith(AVATAR_REF_R2_PREFIX):
        key = ref[len(AVATAR_REF_R2_PREFIX):].strip()
        if key:
            return ("r2", key)
    if ref.startswith(AVATAR_REF_LOCAL_PREFIX):
        name = ref[len(AVATAR_REF_LOCAL_PREFIX):].strip()
        if name:
            return ("local", name)
    return None


def _cleanup_previous_avatar(picture_url: str | None):
    ref = _extract_avatar_ref(picture_url)
    if not ref:
        return
    storage_type, identifier = ref
    try:
        if storage_type == "r2":
            r2_storage = _build_r2_storage()
            if r2_storage.enabled:
                r2_storage.delete_key(identifier)
            return
        if storage_type == "local":
            safe_name = Path(identifier).name
            if safe_name != identifier or not SAFE_AVATAR_FILE_PATTERN.match(safe_name):
                return
            local_path = AVATAR_LOCAL_DIR / safe_name
            local_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning(f"清理旧头像失败: {exc}")


def _is_workday(day: date) -> bool:
    return day.weekday() < 5


def _iter_dates(start_day: date, end_day: date):
    cursor = start_day
    while cursor <= end_day:
        yield cursor
        cursor += timedelta(days=1)


def _month_start_end(year: int, month: int) -> Tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=APP_TZ)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=APP_TZ)
    else:
        end = datetime(year, month + 1, 1, tzinfo=APP_TZ)
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
    return local_day_start_end_to_utc(start_day, day_count=(end_day - start_day).days + 1)


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
        name=_normalize_profile_name(user.name),
        nickname=_sanitize_profile_text(user.nickname),
        email=_sanitize_profile_text(user.email),
        phone=_sanitize_profile_text(user.phone),
        picture=_sanitize_profile_text(user.picture),
    )


@router.post("/api/account/profile/avatar", response_model=AccountAvatarUploadResponse)
async def post_account_profile_avatar(
    request: Request,
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(_get_current_user),
):
    _ensure_auth_user_or_404(current_user.user_id)
    r2_storage = _build_r2_storage()

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in AVATAR_ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="头像仅支持 PNG/JPG/JPEG/WEBP/GIF。")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的头像文件为空。")
    if len(content) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="头像大小不能超过 2MB。")

    safe_name = Path(file.filename or f"avatar{suffix}").name
    content_type = (file.content_type or "").strip() or (mimetypes.guess_type(safe_name)[0] or "application/octet-stream")
    if r2_storage.enabled:
        avatar_key = r2_storage.build_avatar_key(current_user.user_id, safe_name)
        ok = r2_storage.put_bytes(avatar_key, content, content_type=content_type)
        if not ok:
            raise HTTPException(status_code=502, detail="头像上传到对象存储失败，请稍后重试。")
        avatar_ref = f"{AVATAR_REF_R2_PREFIX}{avatar_key}"
        access_url = f"{str(request.base_url).rstrip('/')}{AVATAR_READ_PREFIX}{avatar_ref}"
        return AccountAvatarUploadResponse(url=access_url)

    AVATAR_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    file_name = _build_avatar_filename(current_user.user_id, suffix)
    (AVATAR_LOCAL_DIR / file_name).write_bytes(content)
    avatar_ref = f"{AVATAR_REF_LOCAL_PREFIX}{file_name}"
    access_url = f"{str(request.base_url).rstrip('/')}{AVATAR_READ_PREFIX}{avatar_ref}"
    return AccountAvatarUploadResponse(url=access_url)


@router.get("/api/account/profile/avatar/{avatar_ref:path}")
async def get_account_profile_avatar(avatar_ref: str):
    ref = str(avatar_ref or "").strip()
    if not ref:
        raise HTTPException(status_code=404, detail="头像不存在。")

    if ref.startswith(AVATAR_REF_R2_PREFIX):
        key = ref[len(AVATAR_REF_R2_PREFIX):].strip()
        if not key:
            raise HTTPException(status_code=404, detail="头像不存在。")
        r2_storage = _build_r2_storage()
        if not r2_storage.enabled:
            raise HTTPException(status_code=404, detail="头像不存在。")
        payload = r2_storage.get_bytes(key)
        if not payload:
            raise HTTPException(status_code=404, detail="头像不存在。")

        media_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
        headers = {"Cache-Control": "public, max-age=86400"}
        return StreamingResponse(io.BytesIO(payload), media_type=media_type, headers=headers)

    if not ref.startswith(AVATAR_REF_LOCAL_PREFIX):
        raise HTTPException(status_code=404, detail="头像不存在。")
    file_name = ref[len(AVATAR_REF_LOCAL_PREFIX):].strip()

    safe_name = Path(file_name).name
    if safe_name != file_name or not SAFE_AVATAR_FILE_PATTERN.match(safe_name):
        raise HTTPException(status_code=404, detail="头像不存在。")

    path = AVATAR_LOCAL_DIR / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="头像不存在。")

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path=str(path), media_type=media_type)


@router.put("/api/account/profile", response_model=AccountProfileResponse)
async def put_account_profile(
    payload: AccountProfileUpdateRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    existing = _ensure_auth_user_or_404(current_user.user_id)
    previous_picture = _sanitize_profile_text(existing.picture)
    normalized_name = _normalize_profile_name(payload.name, required=True)
    _ensure_name_unique(normalized_name, current_user.user_id)
    normalized_picture = _normalize_profile_picture(payload.picture)
    saved = task_manager.storage.update_user_profile(
        current_user.user_id,
        name=normalized_name,
        picture=normalized_picture,
    )
    if not saved:
        raise HTTPException(status_code=404, detail="未找到可更新的认证账号档案。")
    current_picture = _sanitize_profile_text(saved.picture)
    if previous_picture and previous_picture != current_picture:
        _cleanup_previous_avatar(previous_picture)
    return AccountProfileResponse(
        ownerId=saved.owner_id,
        authType="authing",
        name=_sanitize_profile_text(saved.name),
        nickname=_sanitize_profile_text(saved.nickname),
        email=_sanitize_profile_text(saved.email),
        phone=_sanitize_profile_text(saved.phone),
        picture=_sanitize_profile_text(saved.picture),
    )


@router.get("/api/account/notification-settings", response_model=AccountNotificationSettingsResponse)
async def get_account_notification_settings(
    current_user: CurrentUser = Depends(_get_current_user),
):
    user = _ensure_authing_user_or_403(current_user)
    return _build_notification_settings_response(user)


@router.put("/api/account/notification-settings", response_model=AccountNotificationSettingsResponse)
async def put_account_notification_settings(
    payload: AccountNotificationSettingsUpdateRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    _ensure_authing_user_or_403(current_user)
    enabled = bool(payload.notificationEmailEnabled)
    work_email = _normalize_notification_email(payload.workNotificationEmail)
    personal_email = _normalize_notification_email(payload.personalNotificationEmail)
    if enabled and not work_email and not personal_email:
        raise HTTPException(status_code=400, detail="开启邮件通知后，工作邮箱和个人邮箱至少填写一个。")

    saved = task_manager.storage.update_user_notification_settings(
        current_user.user_id,
        notification_email_enabled=enabled,
        work_notification_email=work_email,
        personal_notification_email=personal_email,
    )
    if not saved:
        raise HTTPException(status_code=404, detail="未找到可更新的认证账号档案。")
    return _build_notification_settings_response(saved)


@router.get("/api/account/month-target", response_model=AccountMonthTargetResponse)
async def get_account_month_target(
    year: int = Query(default_factory=lambda: datetime.now().year),
    month: int = Query(default_factory=lambda: datetime.now().month),
    current_user: CurrentUser = Depends(_get_current_user),
):
    now = utc_now().astimezone(APP_TZ)
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
    now = utc_now().astimezone(APP_TZ)
    actual_year, actual_month = _normalize_year_month(year, month, now)

    month_start_dt, month_end_dt = _month_start_end(actual_year, actual_month)
    month_start_day = month_start_dt.date()
    month_end_day = (month_end_dt - timedelta(days=1)).date()

    week_start, week_end = _recent_workday_window(5, now.date())
    month_work_start, month_work_end = _recent_workday_window(22, now.date())

    work_week_analysis = _count_created(current_user.user_id, week_start, week_end, TaskType.PATENT_ANALYSIS.value)
    work_week_review = _count_created(current_user.user_id, week_start, week_end, TaskType.AI_REVIEW.value)
    work_week_reply = _count_created(current_user.user_id, week_start, week_end, TaskType.AI_REPLY.value)
    work_week_search = _count_created(current_user.user_id, week_start, week_end, TaskType.AI_SEARCH.value)
    work_month_analysis = _count_created(current_user.user_id, month_work_start, month_work_end, TaskType.PATENT_ANALYSIS.value)
    work_month_review = _count_created(current_user.user_id, month_work_start, month_work_end, TaskType.AI_REVIEW.value)
    work_month_reply = _count_created(current_user.user_id, month_work_start, month_work_end, TaskType.AI_REPLY.value)
    work_month_search = _count_created(current_user.user_id, month_work_start, month_work_end, TaskType.AI_SEARCH.value)

    created_rows = task_manager.storage.aggregate_user_created_tasks_daily(
        current_user.user_id,
        month_start_day,
        month_end_day,
    )
    daily_map: Dict[str, Dict[str, int]] = {}
    for row in created_rows:
        key = row["day"]
        item = daily_map.setdefault(key, {"analysis": 0, "review": 0, "reply": 0, "search": 0})
        if row["task_type"] == TaskType.PATENT_ANALYSIS.value:
            item["analysis"] += int(row["count"])
        elif row["task_type"] == TaskType.AI_REVIEW.value:
            item["review"] += int(row["count"])
        elif row["task_type"] == TaskType.AI_REPLY.value:
            item["reply"] += int(row["count"])
        elif row["task_type"] == TaskType.AI_SEARCH.value:
            item["search"] += int(row["count"])

    weekly_bucket = [
        {"analysis": 0, "review": 0, "reply": 0, "search": 0},
        {"analysis": 0, "review": 0, "reply": 0, "search": 0},
        {"analysis": 0, "review": 0, "reply": 0, "search": 0},
        {"analysis": 0, "review": 0, "reply": 0, "search": 0},
    ]
    daily_series: List[DailyActivityPoint] = []
    for day_item in _iter_dates(month_start_day, month_end_day):
        key = day_item.isoformat()
        row = daily_map.get(key, {"analysis": 0, "review": 0, "reply": 0, "search": 0})
        analysis = int(row["analysis"])
        review = int(row["review"])
        reply = int(row["reply"])
        search = int(row["search"])
        total = analysis + review + reply + search
        daily_series.append(
            DailyActivityPoint(
                date=key,
                analysisCreated=analysis,
                reviewCreated=review,
                replyCreated=reply,
                searchCreated=search,
                totalCreated=total,
            )
        )

        week_index = min(3, (day_item.day - 1) // 7)
        weekly_bucket[week_index]["analysis"] += analysis
        weekly_bucket[week_index]["review"] += review
        weekly_bucket[week_index]["reply"] += reply
        weekly_bucket[week_index]["search"] += search

    weekly_series: List[WeeklyActivityPoint] = []
    for idx in range(4):
        analysis = weekly_bucket[idx]["analysis"]
        review = weekly_bucket[idx]["review"]
        reply = weekly_bucket[idx]["reply"]
        search = weekly_bucket[idx]["search"]
        weekly_series.append(
            WeeklyActivityPoint(
                week=f"第{idx + 1}周",
                analysisCreated=analysis,
                reviewCreated=review,
                replyCreated=reply,
                searchCreated=search,
                totalCreated=analysis + review + reply + search,
            )
        )

    work_week = TaskWindowCounts(
        analysisCount=work_week_analysis,
        reviewCount=work_week_review,
        replyCount=work_week_reply,
        searchCount=work_week_search,
        totalCount=work_week_analysis + work_week_review + work_week_reply + work_week_search,
    )
    work_month = TaskWindowCounts(
        analysisCount=work_month_analysis,
        reviewCount=work_month_review,
        replyCount=work_month_reply,
        searchCount=work_month_search,
        totalCount=work_month_analysis + work_month_review + work_month_reply + work_month_search,
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
