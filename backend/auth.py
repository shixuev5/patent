"""
认证与授权功能
"""
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Header, HTTPException, Query

from backend.models import CurrentUser


AUTH_SECRET = os.getenv("AUTH_SECRET", "change-this-secret-in-production")
AUTH_TOKEN_TTL_DAYS = int(os.getenv("AUTH_TOKEN_TTL_DAYS", "30"))
MAX_DAILY_ANALYSIS = int(os.getenv("MAX_DAILY_ANALYSIS", "3"))
APP_TZ_OFFSET_HOURS = int(os.getenv("APP_TZ_OFFSET_HOURS", "8"))


def _sign_payload(payload_b64: str) -> str:
    return hmac.new(AUTH_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def _issue_token(user_id: str) -> tuple[str, int]:
    now = int(time.time())
    exp = now + AUTH_TOKEN_TTL_DAYS * 24 * 60 * 60
    payload = {
        "uid": user_id,
        "iat": now,
        "exp": exp,
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).decode("ascii").rstrip("=")
    signature = _sign_payload(payload_b64)
    return f"{payload_b64}.{signature}", exp


def _verify_token(token: str) -> Optional[dict]:
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None

    expected = _sign_payload(payload_b64)
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8"))
    except Exception:
        return None

    exp = payload.get("exp")
    uid = payload.get("uid")
    if not uid or not isinstance(uid, str):
        return None
    if not isinstance(exp, int) or exp <= int(time.time()):
        return None
    return payload


def _extract_token_from_request(
    authorization: Optional[str],
    query_token: Optional[str],
) -> Optional[str]:
    if authorization:
        value = authorization.strip()
        if value.lower().startswith("bearer "):
            token = value[7:].strip()
            if token:
                return token
    if query_token:
        return query_token.strip()
    return None


def _get_current_user(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> CurrentUser:
    raw_token = _extract_token_from_request(authorization, token)
    if not raw_token:
        raise HTTPException(status_code=401, detail="需要身份认证。")

    payload = _verify_token(raw_token)
    if not payload:
        raise HTTPException(status_code=401, detail="令牌无效或已过期。")

    return CurrentUser(user_id=payload["uid"])


def _quota_reset_utc() -> datetime:
    local_now = datetime.now(timezone.utc) + timedelta(hours=APP_TZ_OFFSET_HOURS)
    next_local_day = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_local_day - timedelta(hours=APP_TZ_OFFSET_HOURS)
