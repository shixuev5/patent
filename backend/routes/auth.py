"""
认证路由
"""
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from backend.auth import _build_authing_owner_id, _issue_token, _verify_authing_id_token
from backend.models import (
    AuthingAuthResponse,
    AuthingTokenExchangeRequest,
    GuestAuthResponse,
    UserProfileResponse,
)
from backend.storage import User, get_pipeline_manager


router = APIRouter()
task_manager = get_pipeline_manager()


@router.post("/api/auth/guest", response_model=GuestAuthResponse)
async def create_guest_auth(request: Request):
    """创建访客身份认证"""
    client_ip = request.client.host if request.client else "unknown"
    ip_hash = hashlib.sha256(client_ip.encode('utf-8')).hexdigest()[:16]
    user_id = f"ip_{ip_hash}"
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
        name=str(claims.get("name", "")).strip() or None,
        nickname=str(claims.get("nickname", "")).strip() or None,
        email=str(claims.get("email", "")).strip() or None,
        phone=str(claims.get("phone_number", "")).strip() or None,
        picture=str(claims.get("picture", "")).strip() or None,
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
