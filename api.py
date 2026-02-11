"""
专利分析系统 FastAPI 后端
使用 SQLite 存储任务状态，集成真实 PatentPipeline 处理流程。
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from pydantic import BaseModel

from config import settings
from src.storage import get_pipeline_manager, TaskStatus
from main import PatentPipeline


class TaskResponse(BaseModel):
    taskId: str
    status: str
    message: str


app = FastAPI(
    title="专利智能分析平台 API",
    description="提供任务创建、进度追踪和结果下载能力",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

task_manager = get_pipeline_manager()


async def run_pipeline_task(task_id: str, pn: str, upload_file_path: str = None):
    """Run pipeline in background and keep emitting progress updates."""
    try:
        print(f"[Task {task_id}] start processing: {pn}")

        task_manager.start_task(task_id)
        task_manager.update_progress(task_id, 1, "任务已开始")

        loop = asyncio.get_event_loop()
        pipeline = PatentPipeline(pn, upload_file_path)

        def run_pipeline():
            return pipeline.run()

        pipeline_future = loop.run_in_executor(None, run_pipeline)

        progress = 5
        while not pipeline_future.done():
            task_manager.update_progress(task_id, progress, "正在分析，请稍候")
            progress = min(progress + 3, 90)
            await asyncio.sleep(2)

        result = await pipeline_future

        if result.get("status") == "success":
            output_pdf = result.get("output", "")
            task_manager.update_progress(task_id, 95, "分析完成，正在整理报告")
            task_manager.complete_task(
                task_id,
                output_files={
                    "pdf": output_pdf,
                    "pn": pn,
                },
            )
            print(f"[Task {task_id}] completed: {output_pdf}")
        else:
            error_msg = result.get("error", "未知错误")
            task_manager.fail_task(task_id, error_msg)
            print(f"[Task {task_id}] failed: {error_msg}")

    except asyncio.CancelledError:
        print(f"[Task {task_id}] cancelled")
        task_manager.fail_task(task_id, "任务已取消")
        raise
    except Exception as e:
        print(f"[Task {task_id}] failed: {str(e)}")
        task_manager.fail_task(task_id, str(e))


@app.post("/api/tasks", response_model=TaskResponse)
async def create_task(
    patentNumber: str = Form(None),
    file: UploadFile = File(None),
):
    if not patentNumber and not file:
        raise HTTPException(status_code=400, detail="必须提供专利号或上传 PDF 文件")

    import uuid

    pn = patentNumber or str(uuid.uuid4())[:8]

    task = task_manager.create_task(
        pn=pn,
        title=patentNumber or (file.filename if file else "未命名任务"),
        auto_create_steps=True,
    )

    upload_file_path = None
    if file:
        upload_dir = settings.UPLOAD_DIR
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_file_path = upload_dir / f"{task.id}_{file.filename}"
        content = await file.read()
        with open(upload_file_path, "wb") as f:
            f.write(content)

    asyncio.create_task(
        run_pipeline_task(task.id, pn, str(upload_file_path) if upload_file_path else None)
    )

    return TaskResponse(
        taskId=task.id,
        status="pending",
        message="任务已创建并开始处理",
    )


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

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
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_stream():
        last_status = None
        last_progress = -1

        while True:
            try:
                current_task = task_manager.get_task(task_id)
                if not current_task:
                    payload = {"status": "error", "error": "任务已删除"}
                    yield f"data: {json.dumps(payload)}\n\n"
                    break

                current_status = current_task.status.value
                current_progress = current_task.progress

                frontend_status = current_status
                if current_status in ["failed", "cancelled"]:
                    frontend_status = "error"

                if current_status != last_status or current_progress != last_progress:
                    progress_data = {
                        "progress": current_progress,
                        "step": current_task.current_step or "",
                        "status": frontend_status,
                    }

                    if current_status == "completed":
                        progress_data["downloadUrl"] = f"/api/tasks/{task_id}/download"
                    elif current_status in ["failed", "cancelled", "error"]:
                        progress_data["error"] = current_task.error_message or "任务执行失败"

                    yield f"data: {json.dumps(progress_data)}\n\n"

                    last_status = current_status
                    last_progress = current_progress

                if current_status in ["completed", "failed", "cancelled", "error"]:
                    break

                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                print(f"[SSE] Task {task_id} stream cancelled")
                break
            except Exception as e:
                print(f"[SSE] Error streaming task {task_id}: {str(e)}")
                payload = {"status": "error", "error": str(e)}
                yield f"data: {json.dumps(payload)}\n\n"
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/tasks/{task_id}/download")
async def download_result(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.status.value != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    output_files = task.metadata.get("output_files", {}) if task.metadata else {}
    pdf_path_str = output_files.get("pdf")

    if not pdf_path_str:
        patent_number = task.pn or task_id
        pdf_path = settings.OUTPUT_DIR / patent_number / f"{patent_number}.pdf"
    else:
        pdf_path = Path(pdf_path_str)

    if not pdf_path.exists():
        return JSONResponse(
            status_code=404,
            content={
                "error": "报告文件尚未生成",
                "message": f"未找到报告文件: {pdf_path}",
                "task_id": task_id,
                "suggestion": "请稍后重试或联系管理员",
            },
        )

    return FileResponse(
        path=str(pdf_path),
        filename=f"专利分析报告_{task.pn or task_id}.pdf",
        media_type="application/pdf",
    )


@app.get("/api/health")
async def health_check():
    active_count = len(task_manager.list_tasks(status=TaskStatus.PROCESSING, limit=1000))
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
        },
    }


if __name__ == "__main__":
    import uvicorn

    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
