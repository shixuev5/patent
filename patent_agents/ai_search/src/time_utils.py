"""Timestamp helpers used by the AI Search runtime."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Literal, Optional, Union
from zoneinfo import ZoneInfo


UTC = timezone.utc
APP_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))
NaiveStrategy = Literal["utc", "local"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_z(*, timespec: str = "microseconds") -> str:
    return utc_now().isoformat(timespec=timespec).replace("+00:00", "Z")


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
