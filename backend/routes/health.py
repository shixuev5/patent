"""
健康检查路由
"""
from datetime import datetime

from fastapi import APIRouter

from config import VERSION


router = APIRouter()


@router.get("/")
async def root():
    """根路径服务状态，避免部署平台或用户直接访问时返回 404。"""
    return {
        "status": "正常",
        "service": "AI 分析 API",
        "version": VERSION,
        "health": "/api/health",
    }


@router.get("/api/health")
async def health_check():
    """健康检查"""
    return {
        "status": "正常",
        "timestamp": datetime.now().isoformat(),
        "version": VERSION,
    }
