"""
认证路由
"""
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from backend.auth import _issue_token
from backend.models import GuestAuthResponse


router = APIRouter()


@router.post("/api/auth/guest", response_model=GuestAuthResponse)
async def create_guest_auth(request: Request):
    """创建访客身份认证"""
    client_ip = request.client.host
    ip_hash = hashlib.sha256(client_ip.encode('utf-8')).hexdigest()[:16]
    user_id = f"ip_{ip_hash}"
    token, exp = _issue_token(user_id)
    return GuestAuthResponse(
        token=token,
        userId=user_id,
        expiresAt=datetime.fromtimestamp(exp, tz=timezone.utc).isoformat(),
    )
