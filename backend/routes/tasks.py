"""
任务管理路由
"""
import asyncio
import json
import uuid
from pathlib import Path
from threading import Event
from typing import Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from config import settings
from backend.auth import _get_current_user
from backend.usage import _enforce_daily_quota
from backend.models import CurrentUser, TaskResponse
from backend.utils import (
    _cleanup_path,
    _extract_patent_number_from_outputs,
    _read_local_pdf_bytes,
    _build_r2_storage
)
from agents.patent_analysis.src.storage import get_pipeline_manager


router = APIRouter()
task_manager = get_pipeline_manager()

RUNNING_TASKS: Dict[str, Event] = {}


def _get_owned_task(task_id: str, owner_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if task.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return task


def _get_upload_path(task, runtime: Optional[Dict] = None) -> Optional[str]:
    if runtime and "upload_file_path" in runtime:
        return runtime["upload_file_path"]
    if task.metadata and isinstance(task.metadata, dict):
        upload_path = task.metadata.get("upload_path")
        if upload_path:
            return upload_path
    if task.raw_pdf_path:
        return task.raw_pdf_path
    return None


def _cleanup_task_resources(task, runtime: Optional[Dict] = None):
    output_dir = (runtime["output_dir"] if runtime and "output_dir" in runtime else None) or task.output_dir
    upload_path = _get_upload_path(task, runtime)

    _cleanup_path(output_dir)
    _cleanup_path(upload_path)


def _cleanup_upload_only(task, runtime: Optional[Dict] = None):
    upload_path = _get_upload_path(task, runtime)
    _cleanup_path(upload_path)


async def run_pipeline_task(
    task_id: str,
    pn: str,
    upload_file_path: Optional[str] = None,
    cancel_event: Optional[Event] = None,
):
    """后台执行分析流程，并在成功后按需写入对象存储缓存。"""
    try:
        print(f"[任务 {task_id}] 开始处理：{pn}")
        task_manager.start_task(task_id)
        task_manager.update_progress(task_id, 1, "任务已开始")

        loop = asyncio.get_event_loop()
        from main import PatentPipeline
        pipeline = PatentPipeline(pn, upload_file_path, cancel_event=cancel_event, task_id=task_id)

        def run_pipeline():
            return pipeline.run()

        pipeline_future = loop.run_in_executor(None, run_pipeline)

        progress = 5
        while not pipeline_future.done():
            if cancel_event and cancel_event.is_set():
                await asyncio.sleep(0.5)
                continue
            task_manager.update_progress(task_id, progress, "正在分析专利")
            progress = min(progress + 3, 90)
            await asyncio.sleep(2)

        result = await pipeline_future

        if cancel_event and cancel_event.is_set():
            task_manager.cancel_task(task_id, "任务已取消")
            print(f"[任务 {task_id}] 已取消")
            return

        if result.get("status") == "cancelled":
            task_manager.cancel_task(task_id, result.get("error") or "任务已取消")
            print(f"[任务 {task_id}] 已取消")
            return

        if result.get("status") == "success":
            output_pdf = result.get("output", "")
            task_manager.update_progress(task_id, 95, "正在整理报告")

            resolved_pn = await asyncio.to_thread(_extract_patent_number_from_outputs, output_pdf, pn)
            if resolved_pn and resolved_pn != pn:
                task_manager.storage.update_task(task_id, pn=resolved_pn)

            output_files = {
                "pdf": output_pdf,
                "pn": resolved_pn or pn,
            }

            pdf_bytes = await asyncio.to_thread(_read_local_pdf_bytes, output_pdf)
            if not pdf_bytes:
                error_msg = f"报告文件不存在或为空：{output_pdf}"
                task_manager.fail_task(task_id, error_msg)
                print(f"[任务 {task_id}] 失败：{error_msg}")
                return

            r2_storage = _build_r2_storage()
            if r2_storage.enabled:
                r2_key = r2_storage.build_patent_pdf_key(resolved_pn or pn)
                stored_in_r2 = await asyncio.to_thread(
                    r2_storage.put_bytes,
                    r2_key,
                    pdf_bytes,
                    "application/pdf",
                )
                if stored_in_r2:
                    output_files["r2_key"] = r2_key

            task_manager.complete_task(task_id, output_files=output_files)
            print(f"[任务 {task_id}] 已完成：{output_pdf}")
        else:
            error_msg = result.get("error", "未知流程错误")
            task_manager.fail_task(task_id, error_msg)
            print(f"[任务 {task_id}] 失败：{error_msg}")

    except asyncio.CancelledError:
        print(f"[任务 {task_id}] 已取消")
        task_manager.cancel_task(task_id, "任务已取消")
        raise
    except Exception as exc:
        print(f"[任务 {task_id}] 异常失败：{str(exc)}")
        task_manager.fail_task(task_id, str(exc))


@router.get("/api/tasks")
async def list_tasks(current_user: CurrentUser = Depends(_get_current_user)):
    """获取用户的任务列表"""
    tasks = task_manager.list_tasks(owner_id=current_user.user_id)
    return {
        "tasks": [
            {
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
            for task in tasks
        ],
        "total": len(tasks),
    }


@router.post("/api/tasks", response_model=TaskResponse)
async def create_task(
    patentNumber: str = Form(None),
    file: UploadFile = File(None),
    current_user: CurrentUser = Depends(_get_current_user),
):
    if not patentNumber and not file:
        raise HTTPException(status_code=400, detail="必须提供专利号或上传 PDF 文件。")

    _enforce_daily_quota(current_user.user_id)

    pn = patentNumber or str(uuid.uuid4())[:8]
    task = task_manager.create_task(
        owner_id=current_user.user_id,
        pn=pn,
        title=patentNumber or (file.filename if file else "未命名任务"),
        auto_create_steps=True,
    )

    upload_file_path = None
    task_metadata: Dict[str, str] = {}
    if file:
        upload_dir = settings.UPLOAD_DIR
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_filename = Path(file.filename or "upload.pdf").name
        upload_file_path = upload_dir / f"{task.id}_{safe_filename}"
        content = await file.read()
        with open(upload_file_path, "wb") as handle:
            handle.write(content)
        task_metadata["upload_path"] = str(upload_file_path)
        task_manager.storage.update_task(
            task.id,
            raw_pdf_path=str(upload_file_path),
            metadata=task_metadata or None,
        )

    cancel_event = Event()
    RUNNING_TASKS[task.id] = cancel_event
    pipeline_task = asyncio.create_task(
        run_pipeline_task(
            task.id,
            pn,
            str(upload_file_path) if upload_file_path else None,
            cancel_event=cancel_event,
        )
    )
    pipeline_task.add_done_callback(lambda _task: RUNNING_TASKS.pop(task.id, None))

    return TaskResponse(
        taskId=task.id,
        status="pending",
        message="任务已创建并开始处理。",
    )


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str, current_user: CurrentUser = Depends(_get_current_user)):
    task = _get_owned_task(task_id, current_user.user_id)
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


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, current_user: CurrentUser = Depends(_get_current_user)):
    task = _get_owned_task(task_id, current_user.user_id)
    runtime = RUNNING_TASKS.get(task_id)

    if task.status.value in {"processing", "pending"} and runtime:
        runtime.set()

    if task.status.value == "completed":
        _cleanup_upload_only(task)
    else:
        _cleanup_task_resources(task)

    task_manager.delete_task(task_id)
    RUNNING_TASKS.pop(task_id, None)
    return {"deleted": True}


@router.delete("/api/tasks")
async def clear_tasks(current_user: CurrentUser = Depends(_get_current_user)):
    tasks = task_manager.list_tasks(owner_id=current_user.user_id, limit=1000)
    deleted = 0
    for task in tasks:
        runtime = RUNNING_TASKS.get(task.id)
        if task.status.value in {"processing", "pending"} and runtime:
            runtime.set()

        if task.status.value == "completed":
            _cleanup_upload_only(task)
        else:
            _cleanup_task_resources(task)

        if task_manager.delete_task(task.id):
            deleted += 1
        RUNNING_TASKS.pop(task.id, None)

    return {"deleted": deleted}


@router.get("/api/tasks/{task_id}/progress")
async def get_task_progress(task_id: str, current_user: CurrentUser = Depends(_get_current_user)):
    _get_owned_task(task_id, current_user.user_id)

    async def event_stream():
        last_status = None
        last_progress = -1

        while True:
            try:
                current_task = task_manager.get_task(task_id)
                if not current_task or current_task.owner_id != current_user.user_id:
                    payload = {"status": "error", "error": "任务不存在。"}
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
                        "pn": current_task.pn or "",
                    }
                    if current_status == "completed":
                        progress_data["downloadUrl"] = f"/api/tasks/{task_id}/download"
                    elif current_status in ["failed", "cancelled", "error"]:
                        progress_data["error"] = current_task.error_message or "任务执行失败。"

                    yield f"data: {json.dumps(progress_data)}\n\n"
                    last_status = current_status
                    last_progress = current_progress

                if current_status in ["completed", "failed", "cancelled", "error"]:
                    break

                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                print(f"[进度流] 任务 {task_id} 已取消")
                break
            except Exception as exc:
                print(f"[进度流] 任务 {task_id} 推送异常：{str(exc)}")
                payload = {"status": "error", "error": str(exc)}
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


@router.get("/api/tasks/{task_id}/download")
async def download_result(task_id: str, current_user: CurrentUser = Depends(_get_current_user)):
    task = _get_owned_task(task_id, current_user.user_id)

    if task.status.value != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成。")

    output_files = task.metadata.get("output_files", {}) if task.metadata else {}
    filename = f"专利分析报告_{task.pn or task_id}.pdf"

    r2_key = output_files.get("r2_key")
    r2_storage = _build_r2_storage()
    if r2_key and r2_storage.enabled:
        r2_pdf = await asyncio.to_thread(r2_storage.get_bytes, r2_key)
        if r2_pdf:
            from io import BytesIO
            from urllib.parse import quote
            return StreamingResponse(
                BytesIO(r2_pdf),
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
                },
            )

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
                "error": "报告文件不存在",
                "message": f"未找到报告文件：{pdf_path}",
                "task_id": task_id,
                "suggestion": "请稍后重试或联系管理员。",
            },
        )

    return FileResponse(
        path=str(pdf_path),
        filename=filename,
        media_type="application/pdf",
    )
