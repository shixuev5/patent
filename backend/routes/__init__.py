"""
API 路由聚合
"""
from fastapi import APIRouter

from backend.routes.auth import router as auth_router
from backend.routes.tasks import router as tasks_router
from backend.routes.usage import router as usage_router
from backend.routes.health import router as health_router


router = APIRouter()

router.include_router(auth_router)
router.include_router(usage_router)
router.include_router(tasks_router)
router.include_router(health_router)
