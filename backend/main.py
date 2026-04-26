"""
AI 分析后端 API 主应用入口
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from config import settings
from backend.error_handlers import register_exception_handlers
from backend.logging_setup import configure_uvicorn_access_log_filter, setup_logging_utc8
from backend.system_logs import (
    LazySystemLogStorageProxy,
    configure_system_log_storage,
    initialize_system_logging,
    request_logging_middleware,
    set_system_log_db_persistence_ready,
    start_system_log_cleanup_loop,
    stop_system_log_cleanup_loop,
)
from backend.token_pricing import configure_pricing_storage, schedule_background_refresh

_app_log_file = settings.DATA_DIR / "logs" / "app.log"
_app_log_file.parent.mkdir(parents=True, exist_ok=True)
setup_logging_utc8(
    level=os.getenv("APP_LOG_LEVEL", "INFO"),
    log_file=str(_app_log_file),
    file_level="DEBUG",
    rotation="10 MB",
    retention="14 days",
    compression="zip",
)
configure_uvicorn_access_log_filter()
initialize_system_logging()
from backend.routes import router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时的初始化操作
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    from backend.storage import get_pipeline_manager

    configure_system_log_storage(
        LazySystemLogStorageProxy(lambda: get_pipeline_manager().storage)
    )
    configure_pricing_storage(lambda: get_pipeline_manager().storage)
    set_system_log_db_persistence_ready(True)
    start_system_log_cleanup_loop()
    schedule_background_refresh(force=False)
    yield
    # 关闭时的清理操作（如果需要）
    await stop_system_log_cleanup_loop()


from config import VERSION

app = FastAPI(
    title="AI 分析 API",
    description="提供任务创建、进度追踪和报告下载能力。",
    version=VERSION,
    lifespan=lifespan,
)
register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(request_logging_middleware)

app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn

    # 读取 PORT 环境变量，Hugging Face Spaces 默认使用 7860
    port = int(os.getenv("PORT", 7860))

    # 根据环境判断是否启用热重载，生产环境禁用
    reload = os.getenv("ENVIRONMENT") != "production"
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
        log_level="info",
    )
