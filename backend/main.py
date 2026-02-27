"""
专利分析后端 API 主应用入口
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from backend.routes import router as api_router


app = FastAPI(
    title="专利分析 API",
    description="提供任务创建、进度追踪和报告下载能力。",
    version="2.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.on_event("startup")
async def startup_event():
    """应用启动时的初始化操作"""
    # 确保必要的目录存在
    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    import uvicorn

    # 读取 PORT 环境变量，Hugging Face Spaces 默认使用 7860
    port = int(os.getenv("PORT", 7860))

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info",
    )
