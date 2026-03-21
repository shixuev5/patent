"""
Shared timestamp helpers.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Optional, Union
from zoneinfo import ZoneInfo


UTC = timezone.utc
APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))
NaiveStrategy = Literal["utc", "local"]
DateLike = Union[str, date, datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_z(*, timespec: str = "microseconds") -> str:
    return _datetime_to_z(utc_now(), timespec=timespec)


def parse_storage_ts(
    value: Optional[Union[str, datetime]],
    *,
    naive_strategy: NaiveStrategy,
) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        base_tz = APP_TZ if naive_strategy == "local" else UTC
        parsed = parsed.replace(tzinfo=base_tz)
    return parsed.astimezone(UTC)


def to_utc_z(
    value: Optional[Union[str, datetime]],
    *,
    naive_strategy: NaiveStrategy,
    timespec: str = "microseconds",
) -> Optional[str]:
    parsed = parse_storage_ts(value, naive_strategy=naive_strategy)
    if parsed is None:
        return None
    return _datetime_to_z(parsed, timespec=timespec)


def parse_local_input_to_utc_z(
    value: Optional[DateLike],
    *,
    end_of_day: bool = False,
    timespec: str = "seconds",
) -> Optional[str]:
    parsed = _parse_local_input(value, end_of_day=end_of_day)
    if parsed is None:
        return None
    return _datetime_to_z(parsed.astimezone(UTC), timespec=timespec)


def local_day_start_end_to_utc(
    value: DateLike,
    *,
    day_count: int = 1,
    timespec: str = "seconds",
) -> tuple[str, str]:
    day = _normalize_local_day(value)
    start = datetime.combine(day, time.min, APP_TZ)
    end = start + timedelta(days=max(1, int(day_count or 1)))
    return (
        _datetime_to_z(start.astimezone(UTC), timespec=timespec),
        _datetime_to_z(end.astimezone(UTC), timespec=timespec),
    )


def local_recent_day_window_to_utc(
    day_count: int,
    *,
    now: Optional[datetime] = None,
    timespec: str = "seconds",
) -> tuple[str, str]:
    resolved_now = now.astimezone(APP_TZ) if now else utc_now().astimezone(APP_TZ)
    end = datetime.combine(resolved_now.date() + timedelta(days=1), time.min, APP_TZ)
    start = datetime.combine(
        resolved_now.date() - timedelta(days=max(1, int(day_count or 1)) - 1),
        time.min,
        APP_TZ,
    )
    return (
        _datetime_to_z(start.astimezone(UTC), timespec=timespec),
        _datetime_to_z(end.astimezone(UTC), timespec=timespec),
    )


def format_for_admin_local(
    value: Optional[Union[str, datetime]],
    *,
    naive_strategy: NaiveStrategy,
    timespec: str = "seconds",
) -> Optional[str]:
    parsed = parse_storage_ts(value, naive_strategy=naive_strategy)
    if parsed is None:
        return None
    return parsed.astimezone(APP_TZ).replace(tzinfo=None).isoformat(timespec=timespec)


def utc_to_local_day(
    value: Optional[Union[str, datetime]],
    *,
    naive_strategy: NaiveStrategy,
) -> Optional[str]:
    parsed = parse_storage_ts(value, naive_strategy=naive_strategy)
    if parsed is None:
        return None
    return parsed.astimezone(APP_TZ).date().isoformat()


def _datetime_to_z(value: datetime, *, timespec: str) -> str:
    return value.astimezone(UTC).isoformat(timespec=timespec).replace("+00:00", "Z")


def _normalize_local_day(value: DateLike) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=APP_TZ)
        return parsed.astimezone(APP_TZ).date()
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"Invalid local day: {value}") from exc


def _parse_local_input(value: Optional[DateLike], *, end_of_day: bool) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        local_time = time.max.replace(microsecond=0) if end_of_day else time.min
        return datetime.combine(value, local_time, APP_TZ)
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=APP_TZ)
        return parsed.astimezone(APP_TZ)
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 10:
        return datetime.combine(
            date.fromisoformat(text),
            time.max.replace(microsecond=0) if end_of_day else time.min,
            APP_TZ,
        )
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if end_of_day and parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0 and parsed.microsecond == 0:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        return parsed.replace(tzinfo=APP_TZ)
    return parsed.astimezone(APP_TZ)
