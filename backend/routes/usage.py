"""
使用额度查询路由
"""
from fastapi import APIRouter, Depends

from backend.auth import _get_current_user
from backend.models import CurrentUser, UsageResponse
from backend.usage import _get_user_usage


router = APIRouter()


@router.get("/api/usage", response_model=UsageResponse)
async def get_usage(current_user: CurrentUser = Depends(_get_current_user)):
    """获取用户的每日使用额度信息"""
    return _get_user_usage(current_user.user_id)
