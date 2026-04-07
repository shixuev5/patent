"""
健康检查路由
"""
from datetime import datetime

from fastapi import APIRouter

from config import VERSION


router = APIRouter()


@router.get("/api/health")
async def health_check():
    """健康检查"""
    return {
        "status": "正常",
        "timestamp": datetime.now().isoformat(),
        "version": VERSION,
    }
