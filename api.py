"""
专利分析系统 FastAPI 后端
使用 SQLite 存储任务状态，集成真实的 PatentPipeline 处理流程
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from pydantic import BaseModel

# 导入任务存储模块
from src.storage import get_pipeline_manager, TaskStatus

# 导入真实的处理流程
from main import PatentPipeline

# ==================== 数据模型 ====================

class TaskStatusResponse(BaseModel):
    """任务状态模型"""
    id: str
    status: str
    progress: int
    step: str
    download_url: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str


class TaskResponse(BaseModel):
    """任务创建响应"""
    taskId: str
    status: str
    message: str


# ==================== FastAPI 应用 ====================

app = FastAPI(
    title="专利智能分析平台 API",
    description="提供专利分析任务创建、进度追踪和结果下载功能",
    version="2.0.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局任务管理器实例（使用 SQLite 存储）
task_manager = get_pipeline_manager()


# ==================== 后台处理任务 ====================

async def run_pipeline_task(task_id: str, pn: str, upload_file_path: str = None):
    """
    在后台执行真实的专利分析流程

    Args:
        task_id: 任务ID
        pn: 专利号（或任务ID作为标识）
        upload_file_path: 上传文件的路径（可选）
    """
    try:
        print(f"[Task {task_id}] 开始处理专利: {pn}")

        # 更新任务状态为处理中
        task_manager.start_task(task_id)

        # 执行真实的 PatentPipeline
        loop = asyncio.get_event_loop()

        # 创建 Pipeline 实例
        pipeline = PatentPipeline(pn, upload_file_path)

        # 在单独的线程中运行（避免阻塞事件循环）
        def run_pipeline():
            return pipeline.run()

        # 执行完整的处理流程
        result = await loop.run_in_executor(None, run_pipeline)

        # 检查处理结果
        if result.get("status") == "success":
            # 任务完成
            output_pdf = result.get("output", "")
            task_manager.complete_task(
                task_id,
                output_files={
                    "pdf": output_pdf,
                    "pn": pn,
                }
            )
            print(f"[Task {task_id}] 任务完成: {output_pdf}")
        else:
            # 处理失败
            error_msg = result.get("error", "未知错误")
            task_manager.fail_task(task_id, error_msg)
            print(f"[Task {task_id}] 任务失败: {error_msg}")

    except asyncio.CancelledError:
        print(f"[Task {task_id}] 任务被取消")
        task_manager.fail_task(task_id, "任务被用户取消")
        raise
    except Exception as e:
        print(f"[Task {task_id}] 处理失败: {str(e)}")
        task_manager.fail_task(task_id, str(e))


# ==================== API 路由 ====================

@app.post("/api/tasks", response_model=TaskResponse)
async def create_task(
    patentNumber: str = Form(None),
    file: UploadFile = File(None)
):
    """创建新的专利分析任务"""
    if not patentNumber and not file:
        raise HTTPException(
            status_code=400,
            detail="必须提供专利号或上传PDF文件"
        )

    # 生成任务ID（用于文件上传时作为目录名）
    import uuid
    task_id = str(uuid.uuid4())[:8]

    # 如果没有专利号，使用任务ID作为目录标识
    pn = patentNumber or task_id

    # 创建任务（使用 SQLite 存储）
    task = task_manager.create_task(
        pn=pn,
        title=patentNumber or (file.filename if file else "未命名任务"),
        auto_create_steps=True
    )

    # 保存上传的文件（如果有）
    upload_file_path = None
    if file:
        upload_dir = Path("uploads")
        upload_dir.mkdir(exist_ok=True)
        upload_file_path = upload_dir / f"{task.id}_{file.filename}"

        content = await file.read()
        with open(upload_file_path, "wb") as f:
            f.write(content)

    # 启动后台任务执行真实的处理流程
    asyncio.create_task(
        run_pipeline_task(task.id, pn, str(upload_file_path) if upload_file_path else None)
    )

    return TaskResponse(
        taskId=task.id,
        status="pending",
        message="任务已创建并开始处理"
    )


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """获取任务详情"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 转换为字典格式返回
    return {
        "id": task.id,
        "pn": task.pn,
        "title": task.title,
        "status": task.status.value,
        "progress": task.progress,
        "step": task.current_step,
        "error": task.error_message,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


@app.get("/api/tasks/{task_id}/progress")
async def get_task_progress(task_id: str):
    """
    获取任务进度（SSE 流）

    返回 Server-Sent Events 流，实时推送任务进度更新
    """
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_stream():
        """SSE 事件流生成器"""
        last_status = None
        last_progress = -1

        while True:
            try:
                # 获取最新任务状态
                current_task = task_manager.get_task(task_id)
                if not current_task:
                    yield f"data: {json.dumps({'status': 'error', 'error': '任务已删除'})}\n\n"
                    break

                # 检查状态是否有变化
                current_status = current_task.status.value
                current_progress = current_task.progress

                if current_status != last_status or current_progress != last_progress:
                    # 构建进度数据
                    progress_data = {
                        "progress": current_progress,
                        "step": current_task.current_step or "",
                        "status": current_status,
                    }

                    # 添加额外信息
                    if current_status == "completed":
                        progress_data["downloadUrl"] = f"/api/tasks/{task_id}/download"
                    elif current_status == "error":
                        progress_data["error"] = current_task.error_message or "未知错误"

                    # 发送 SSE 事件
                    yield f"data: {json.dumps(progress_data)}\n\n"

                    last_status = current_status
                    last_progress = current_progress

                # 检查是否完成或出错
                if current_status in ["completed", "error"]:
                    break

                # 等待一段时间后再次检查
                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                print(f"[SSE] Task {task_id} stream cancelled")
                break
            except Exception as e:
                print(f"[SSE] Error streaming task {task_id}: {str(e)}")
                yield f"data: {json.dumps({'status': 'error', 'error': str(e)})}\n\n"
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/api/tasks/{task_id}/download")
async def download_result(task_id: str):
    """下载任务结果报告"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status.value != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    # 从 metadata 获取输出文件路径
    output_files = task.metadata.get("output_files", {}) if task.metadata else {}
    pdf_path_str = output_files.get("pdf")

    if not pdf_path_str:
        # 使用默认路径
        patent_number = task.pn or task_id
        pdf_path = Path(f"output/{patent_number}/{patent_number}.pdf")
    else:
        pdf_path = Path(pdf_path_str)

    # 检查文件是否存在
    if not pdf_path.exists():
        return JSONResponse(
            status_code=404,
            content={
                "error": "报告文件尚未生成",
                "message": f"找不到报告文件: {pdf_path}",
                "task_id": task_id,
                "suggestion": "请稍后再试或联系管理员"
            }
        )

    # 返回 PDF 文件
    return FileResponse(
        path=str(pdf_path),
        filename=f"专利分析报告_{task.pn or task_id}.pdf",
        media_type="application/pdf"
    )


@app.get("/api/health")
async def health_check():
    """健康检查接口"""
    # 获取进行中任务数量
    active_count = len(
        task_manager.list_tasks(status=TaskStatus.PROCESSING, limit=1000)
    )

    # 获取统计信息
    stats = task_manager.storage.get_statistics()

    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
        "active_tasks": active_count,
        "statistics": {
            "total": stats.get("total", 0),
            "by_status": stats.get("by_status", {}),
            "today_created": stats.get("today_created", 0),
        }
    }


# ==================== 主入口 ====================

if __name__ == "__main__":
    import uvicorn

    # 确保上传目录存在
    Path("uploads").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)

    # 启动服务
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
