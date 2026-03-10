"""
认证路由
"""
import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Set

from fastapi import APIRouter

from backend.auth import _build_authing_owner_id, _issue_token, _verify_authing_id_token
from backend.models import (
    AuthingAuthResponse,
    AuthingTokenExchangeRequest,
    GuestAuthRequest,
    GuestAuthResponse,
    UserProfileResponse,
)
from backend.storage import User, get_pipeline_manager


router = APIRouter()
task_manager = get_pipeline_manager()


def _sanitize_profile_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in {"none", "null", "undefined"}:
        return None
    return text


def _normalize_guest_device_id(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return uuid.uuid4().hex
    if len(text) > 128:
        text = text[:128]
    return text


def _parse_role_tokens(text: str) -> Set[str]:
    tokens = [item.strip().lower() for item in re.split(r"[\s,|;/]+", text or "") if item.strip()]
    return {item for item in tokens if item}


def _collect_roles(value: Any, output: Set[str]):
    if isinstance(value, str):
        output.update(_parse_role_tokens(value))
        return
    if isinstance(value, list):
        for item in value:
            _collect_roles(item, output)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        lower_key = str(key or "").strip().lower()
        if lower_key in {"code", "name", "value", "slug", "id"} and isinstance(item, str):
            output.update(_parse_role_tokens(item))
        if "role" in lower_key or isinstance(item, (list, dict)):
            _collect_roles(item, output)


def _extract_primary_role(claims: dict) -> str | None:
    roles: Set[str] = set()
    for key, value in claims.items():
        if "role" in str(key or "").strip().lower():
            _collect_roles(value, roles)
        elif isinstance(value, dict):
            for child_key, child_value in value.items():
                if "role" in str(child_key or "").strip().lower():
                    _collect_roles(child_value, roles)
    if not roles:
        return None
    if "admin" in roles:
        return "admin"
    return sorted(roles)[0]


@router.post("/api/auth/guest", response_model=GuestAuthResponse)
async def create_guest_auth(payload: GuestAuthRequest | None = None):
    """创建访客身份认证"""
    device_id = _normalize_guest_device_id(payload.deviceId if payload else None)
    device_hash = hashlib.sha256(device_id.encode("utf-8")).hexdigest()[:24]
    user_id = f"guest_{device_hash}"
    token, exp = _issue_token(user_id)
    return GuestAuthResponse(
        token=token,
        userId=user_id,
        expiresAt=datetime.fromtimestamp(exp, tz=timezone.utc).isoformat(),
    )


@router.post("/api/auth/authing", response_model=AuthingAuthResponse)
async def exchange_authing_token(payload: AuthingTokenExchangeRequest):
    """使用 Authing ID Token 换取后端业务会话令牌。"""
    claims = _verify_authing_id_token(payload.idToken)
    sub = str(claims["sub"]).strip()
    owner_id = _build_authing_owner_id(sub)

    user_record = User(
        owner_id=owner_id,
        authing_sub=sub,
        role=_extract_primary_role(claims),
        name=_sanitize_profile_text(claims.get("name")),
        nickname=_sanitize_profile_text(claims.get("nickname")),
        email=_sanitize_profile_text(claims.get("email")),
        phone=_sanitize_profile_text(claims.get("phone_number")),
        picture=_sanitize_profile_text(claims.get("picture")),
        raw_profile=claims,
    )
    saved_user = task_manager.storage.upsert_authing_user(user_record)

    token, exp = _issue_token(owner_id)
    return AuthingAuthResponse(
        token=token,
        userId=owner_id,
        expiresAt=datetime.fromtimestamp(exp, tz=timezone.utc).isoformat(),
        user=UserProfileResponse(
            ownerId=saved_user.owner_id,
            authingSub=saved_user.authing_sub,
            name=saved_user.name,
            nickname=saved_user.nickname,
            email=saved_user.email,
            phone=saved_user.phone,
            picture=saved_user.picture,
        ),
    )
