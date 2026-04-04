"""
API 路由聚合
"""
from fastapi import APIRouter

from backend.routes.auth import router as auth_router
from backend.routes.account import router as account_router
from backend.routes.ai_search import router as ai_search_router
from backend.routes.admin_entities import router as admin_entities_router
from backend.routes.admin_usage import router as admin_usage_router
from backend.routes.admin_logs import router as admin_logs_router
from backend.routes.changelog import router as changelog_router
from backend.routes.tasks import router as tasks_router
from backend.routes.usage import router as usage_router
from backend.routes.health import router as health_router


router = APIRouter()

router.include_router(auth_router)
router.include_router(account_router)
router.include_router(ai_search_router)
router.include_router(admin_entities_router)
router.include_router(admin_usage_router)
router.include_router(admin_logs_router)
router.include_router(usage_router)
router.include_router(tasks_router)
router.include_router(health_router)
router.include_router(changelog_router)
