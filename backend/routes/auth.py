"""
认证路由
"""
import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException

from backend.auth import (
    _build_authing_owner_id,
    _get_current_user,
    _hash_refresh_token,
    _issue_refresh_token,
    _issue_token,
    _verify_authing_id_token,
)
from backend.models import (
    AuthingAuthResponse,
    AuthingTokenExchangeRequest,
    GuestAuthRequest,
    GuestAuthResponse,
    LogoutRequest,
    RefreshTokenRequest,
    SessionAuthResponse,
    UserProfileResponse,
)
from backend.models import CurrentUser
from backend.storage import RefreshSession, User, get_pipeline_manager
from backend.time_utils import to_utc_z, utc_now


router = APIRouter()
task_manager = get_pipeline_manager()
USER_NAME_PREFIX = "用户"


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


def _normalize_display_name(value) -> str | None:
    text = _sanitize_profile_text(value)
    if text is None:
        return None
    if len(text) > 32:
        return text[:32]
    return text


def _is_name_taken(name: str, exclude_owner_id: str | None = None) -> bool:
    storage = task_manager.storage
    if not hasattr(storage, "get_user_by_name"):
        return False
    matched = storage.get_user_by_name(name)
    if not matched:
        return False
    if exclude_owner_id and str(matched.owner_id or "").strip() == str(exclude_owner_id or "").strip():
        return False
    return True


def _generate_unique_user_name(owner_id: str) -> str:
    for _ in range(100):
        candidate = f"{USER_NAME_PREFIX}{uuid.uuid4().hex[:6]}"
        if not _is_name_taken(candidate, exclude_owner_id=owner_id):
            return candidate
    raise RuntimeError("生成唯一用户名失败，请稍后重试。")


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


def _build_auth_session(owner_id: str, auth_type: str) -> Tuple[SessionAuthResponse, RefreshSession]:
    access_token, access_exp = _issue_token(owner_id)
    refresh_token, refresh_hash, refresh_exp = _issue_refresh_token()
    now = utc_now()
    refresh_session = RefreshSession(
        token_hash=refresh_hash,
        owner_id=owner_id,
        expires_at=datetime.fromtimestamp(refresh_exp, tz=timezone.utc),
        created_at=now,
        updated_at=now,
    )
    response = SessionAuthResponse(
        access_token=access_token,
        access_expires_at=datetime.fromtimestamp(access_exp, tz=timezone.utc).isoformat(),
        refresh_token=refresh_token,
        refresh_expires_at=datetime.fromtimestamp(refresh_exp, tz=timezone.utc).isoformat(),
        user_id=owner_id,
        auth_type="authing" if auth_type == "authing" else "guest",
    )
    return response, refresh_session


def _issue_auth_session(owner_id: str, auth_type: str) -> SessionAuthResponse:
    response, refresh_session = _build_auth_session(owner_id, auth_type)
    task_manager.storage.upsert_refresh_session(refresh_session)
    return response


@router.post("/api/auth/guest", response_model=GuestAuthResponse)
async def create_guest_auth(payload: GuestAuthRequest | None = None):
    """创建访客身份认证"""
    device_id = _normalize_guest_device_id(payload.deviceId if payload else None)
    device_hash = hashlib.sha256(device_id.encode("utf-8")).hexdigest()[:24]
    user_id = f"guest_{device_hash}"
    session = _issue_auth_session(user_id, auth_type="guest")
    return GuestAuthResponse(**session.model_dump())


@router.post("/api/auth/authing", response_model=AuthingAuthResponse)
async def exchange_authing_token(payload: AuthingTokenExchangeRequest):
    """使用 Authing ID Token 换取后端业务会话令牌。"""
    claims = _verify_authing_id_token(payload.idToken)
    sub = str(claims["sub"]).strip()
    owner_id = _build_authing_owner_id(sub)
    existing = task_manager.storage.get_user_by_owner_id(owner_id)
    existing_name = _normalize_display_name(existing.name) if existing else None
    claimed_name = _normalize_display_name(claims.get("name"))
    resolved_name = existing_name or claimed_name
    if not resolved_name or _is_name_taken(resolved_name, exclude_owner_id=owner_id):
        resolved_name = _generate_unique_user_name(owner_id)

    user_record = User(
        owner_id=owner_id,
        authing_sub=sub,
        role=_extract_primary_role(claims),
        name=resolved_name,
        nickname=_sanitize_profile_text(claims.get("nickname")),
        email=_sanitize_profile_text(claims.get("email")),
        phone=_sanitize_profile_text(claims.get("phone_number")),
        picture=_sanitize_profile_text(claims.get("picture")),
        raw_profile=claims,
    )
    saved_user = task_manager.storage.upsert_authing_user(user_record)

    session = _issue_auth_session(owner_id, auth_type="authing")
    return AuthingAuthResponse(
        access_token=session.access_token,
        access_expires_at=session.access_expires_at,
        refresh_token=session.refresh_token,
        refresh_expires_at=session.refresh_expires_at,
        user_id=owner_id,
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


@router.post("/api/auth/refresh", response_model=SessionAuthResponse)
async def refresh_auth_session(payload: RefreshTokenRequest):
    raw_refresh_token = str(payload.refresh_token or "").strip()
    if not raw_refresh_token:
        raise HTTPException(status_code=401, detail={"code": "REFRESH_TOKEN_INVALID", "message": "refresh token 不能为空。"})

    token_hash = _hash_refresh_token(raw_refresh_token)
    session = task_manager.storage.get_refresh_session(token_hash)
    if not session:
        raise HTTPException(status_code=401, detail={"code": "REFRESH_TOKEN_INVALID", "message": "refresh token 无效。"})

    now = utc_now()
    if session.revoked_at is not None or session.expires_at <= now:
        raise HTTPException(status_code=401, detail={"code": "REFRESH_TOKEN_INVALID", "message": "refresh token 已失效。"})

    next_session, next_refresh_session = _build_auth_session(
        session.owner_id,
        auth_type="authing" if session.owner_id.startswith("authing:") else "guest",
    )
    rotated = task_manager.storage.rotate_refresh_session(session, next_refresh_session)
    if not rotated:
        raise HTTPException(status_code=401, detail={"code": "REFRESH_TOKEN_INVALID", "message": "refresh token 已失效。"})
    return next_session


@router.post("/api/auth/logout")
async def logout_auth_session(
    payload: LogoutRequest | None = None,
    current_user: CurrentUser = Depends(_get_current_user),
):
    revoked_count = 0
    refresh_token = str(payload.refresh_token if payload else "").strip()
    if refresh_token:
        revoked = task_manager.storage.revoke_refresh_session(_hash_refresh_token(refresh_token))
        revoked_count = 1 if revoked else 0
    else:
        revoked_count = task_manager.storage.revoke_refresh_sessions_by_owner(current_user.user_id)
    return {
        "success": True,
        "revoked_count": int(revoked_count),
        "revoked_at": to_utc_z(utc_now(), naive_strategy="utc"),
    }
