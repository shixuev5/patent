"""
任务管理路由
"""
import asyncio
import json
import os
import uuid
from pathlib import Path
from threading import Event
from typing import Any, Dict, List, Optional

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
    _build_r2_storage,
)
from backend.storage import TaskType, get_pipeline_manager


router = APIRouter()
task_manager = get_pipeline_manager()

RUNNING_TASKS: Dict[str, Event] = {}

ALLOWED_TASK_TYPES = {
    TaskType.PATENT_ANALYSIS.value,
    TaskType.OFFICE_ACTION_REPLY.value,
}

NODE_LABELS = {
    "document_processing": "正在解析文档",
    "patent_retrieval": "正在检索专利",
    "data_preparation": "正在整理材料",
    "amendment_tracking": "正在分析修改差异",
    "support_basis_check": "正在进行支持依据核查",
    "amendment_strategy": "正在生成修改策略",
    "dispute_extraction": "正在提取争议焦点",
    "evidence_verification": "正在核验证据",
    "common_knowledge_verification": "正在核查公知常识",
    "topup_search_verification": "正在补充检索",
    "verification_join": "正在汇总核查结果",
    "report_generation": "正在生成报告内容",
    "final_report_render": "正在渲染最终报告",
}


def _to_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def _normalize_task_type(raw: Optional[str]) -> str:
    task_type = (raw or TaskType.PATENT_ANALYSIS.value).strip().lower()
    if task_type not in ALLOWED_TASK_TYPES:
        raise HTTPException(status_code=400, detail="不支持的任务类型。")
    return task_type


def _task_type(task: Any) -> str:
    if getattr(task, "task_type", "") in ALLOWED_TASK_TYPES:
        return str(task.task_type)
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    metadata_type = str(metadata.get("task_type", "")).strip().lower()
    if metadata_type in ALLOWED_TASK_TYPES:
        return metadata_type
    return TaskType.PATENT_ANALYSIS.value


def _task_to_response(task: Any) -> Dict[str, Any]:
    return {
        "id": task.id,
        "pn": task.pn,
        "title": task.title,
        "taskType": _task_type(task),
        "status": task.status.value,
        "progress": task.progress,
        "step": task.current_step,
        "error": task.error_message,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def _validate_file_suffix(upload: UploadFile, allowed: set[str], label: str):
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed:
        allowed_text = "/".join(sorted(allowed))
        raise HTTPException(status_code=400, detail=f"{label}仅支持 {allowed_text} 格式。")


async def _save_upload_file(task_id: str, upload: UploadFile, subdir: str, prefix: str) -> str:
    safe_name = Path(upload.filename or f"{prefix}.dat").name
    upload_dir = settings.UPLOAD_DIR / task_id / subdir
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"{prefix}_{safe_name}"
    content = await upload.read()
    with open(path, "wb") as handle:
        handle.write(content)
    return str(path)


def _collect_upload_paths(task: Any) -> List[str]:
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    paths: List[str] = []

    input_files = metadata.get("input_files")
    if isinstance(input_files, list):
        for item in input_files:
            if not isinstance(item, dict):
                continue
            stored_path = str(item.get("stored_path", "")).strip()
            if stored_path:
                paths.append(stored_path)

    legacy_upload = str(metadata.get("upload_path", "")).strip()
    if legacy_upload:
        paths.append(legacy_upload)

    if task.raw_pdf_path:
        paths.append(str(task.raw_pdf_path))

    dedup: List[str] = []
    seen = set()
    for path in paths:
        if path and path not in seen:
            dedup.append(path)
            seen.add(path)
    return dedup


def _cleanup_task_resources(task: Any):
    _cleanup_path(task.output_dir)
    for path in _collect_upload_paths(task):
        _cleanup_path(path)


def _cleanup_upload_only(task: Any):
    for path in _collect_upload_paths(task):
        _cleanup_path(path)


def _get_owned_task(task_id: str, owner_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if task.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return task


async def run_pipeline_task(
    task_id: str,
    pn: str,
    upload_file_path: Optional[str] = None,
    cancel_event: Optional[Event] = None,
):
    """后台执行专利分析流程，并在成功后按需写入对象存储缓存。"""
    try:
        print(f"[任务 {task_id}] 开始处理：{pn}")
        task_manager.start_task(task_id)
        task_manager.update_progress(task_id, 1, "任务已开始")

        loop = asyncio.get_event_loop()
        from agents.patent_analysis.main import PatentPipeline

        pipeline = PatentPipeline(pn, upload_file_path, cancel_event=cancel_event, task_id=task_id)

        def run_pipeline():
            return pipeline.run()

        pipeline_future = loop.run_in_executor(None, run_pipeline)
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


async def run_office_action_reply_task(
    task_id: str,
    input_files: List[Dict[str, str]],
    cancel_event: Optional[Event] = None,
):
    """后台执行审查意见答复流程。"""
    try:
        print(f"[任务 {task_id}] 开始处理：office_action_reply")
        task_manager.start_task(task_id)
        task_manager.update_progress(task_id, 5, "正在准备材料")

        loop = asyncio.get_event_loop()

        def run_workflow() -> Dict[str, Any]:
            from agents.office_action_reply.main import create_workflow
            from agents.office_action_reply.src.state import WorkflowConfig, WorkflowState, InputFile

            output_dir = settings.OUTPUT_DIR / task_id
            output_dir.mkdir(parents=True, exist_ok=True)

            config = WorkflowConfig(
                cache_dir=str(output_dir / ".cache"),
                pdf_parser=os.getenv("PDF_PARSER", "local"),
            )

            initial_state = WorkflowState(
                input_files=[
                    InputFile(
                        file_path=item["stored_path"],
                        file_type=item["file_type"],
                        file_name=item["original_name"],
                    )
                    for item in input_files
                ],
                output_dir=str(output_dir),
                task_id=task_id,
                current_node="start",
                status="pending",
                progress=0.0,
            )

            workflow = create_workflow(config)
            result = workflow.invoke(initial_state)
            return _to_dict(result)

        task_manager.update_progress(task_id, 20, "正在解析文档")
        result = await loop.run_in_executor(None, run_workflow)

        if cancel_event and cancel_event.is_set():
            task_manager.cancel_task(task_id, "任务已取消")
            print(f"[任务 {task_id}] 已取消")
            return

        status = str(result.get("status", "failed")).strip().lower()
        if status == "failed":
            errors = result.get("errors") or []
            first_error = ""
            if isinstance(errors, list) and errors:
                first_error = str(_to_dict(errors[0]).get("error_message", "")).strip()
            error_msg = first_error or "审查意见答复任务执行失败"
            task_manager.fail_task(task_id, error_msg)
            print(f"[任务 {task_id}] 失败：{error_msg}")
            return

        task_manager.update_progress(task_id, 95, "正在整理报告")

        artifacts = _to_dict(result.get("final_report_artifacts"))
        output_dir = settings.OUTPUT_DIR / task_id
        pdf_path = artifacts.get("pdf_path") or str(output_dir / "final_report.pdf")
        md_path = artifacts.get("markdown_path") or str(output_dir / "final_report.md")
        json_path = str(output_dir / "final_report.json")

        pdf_bytes = await asyncio.to_thread(_read_local_pdf_bytes, pdf_path)
        if not pdf_bytes:
            error_msg = f"报告文件不存在或为空：{pdf_path}"
            task_manager.fail_task(task_id, error_msg)
            print(f"[任务 {task_id}] 失败：{error_msg}")
            return

        output_files: Dict[str, str] = {"pdf": pdf_path}
        if Path(md_path).exists():
            output_files["md"] = md_path
        if Path(json_path).exists():
            output_files["json"] = json_path

        task_manager.complete_task(task_id, output_files=output_files)
        print(f"[任务 {task_id}] 已完成：{pdf_path}")

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
        "tasks": [_task_to_response(task) for task in tasks],
        "total": len(tasks),
    }


@router.post("/api/tasks", response_model=TaskResponse)
async def create_task(
    taskType: Optional[str] = Form(None),
    patentNumber: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    officeActionFile: Optional[UploadFile] = File(None),
    responseFile: Optional[UploadFile] = File(None),
    claimsFile: Optional[UploadFile] = File(None),
    comparisonDocs: Optional[List[UploadFile]] = File(None),
    current_user: CurrentUser = Depends(_get_current_user),
):
    task_type = _normalize_task_type(taskType)
    _enforce_daily_quota(current_user.user_id)

    if task_type == TaskType.PATENT_ANALYSIS.value:
        if not patentNumber and not file:
            raise HTTPException(status_code=400, detail="必须提供专利号或上传 PDF 文件。")

        if file:
            _validate_file_suffix(file, {".pdf"}, "专利文档")

        pn = patentNumber or str(uuid.uuid4())[:8]
        task = task_manager.create_task(
            owner_id=current_user.user_id,
            task_type=task_type,
            pn=pn,
            title=patentNumber or (file.filename if file else "未命名任务"),
        )

        task_metadata: Dict[str, Any] = {"task_type": task_type, "input_files": []}
        upload_file_path: Optional[str] = None

        if file:
            upload_file_path = await _save_upload_file(task.id, file, "patent", "source")
            task_metadata["input_files"].append(
                {
                    "file_type": "patent_pdf",
                    "original_name": file.filename or "upload.pdf",
                    "stored_path": upload_file_path,
                }
            )
            task_manager.storage.update_task(
                task.id,
                raw_pdf_path=upload_file_path,
                metadata=task_metadata,
            )
        else:
            task_manager.storage.update_task(task.id, metadata=task_metadata)

        cancel_event = Event()
        RUNNING_TASKS[task.id] = cancel_event
        pipeline_task = asyncio.create_task(
            run_pipeline_task(
                task.id,
                pn,
                upload_file_path,
                cancel_event=cancel_event,
            )
        )
        pipeline_task.add_done_callback(lambda _task: RUNNING_TASKS.pop(task.id, None))

        return TaskResponse(
            taskId=task.id,
            status="pending",
            message="任务已创建并开始处理。",
        )

    if not officeActionFile or not responseFile:
        raise HTTPException(status_code=400, detail="审查意见答复任务必须上传审查意见通知书和意见陈述书。")

    _validate_file_suffix(officeActionFile, {".pdf", ".docx"}, "审查意见通知书")
    _validate_file_suffix(responseFile, {".pdf", ".docx"}, "意见陈述书")
    if claimsFile:
        _validate_file_suffix(claimsFile, {".pdf", ".docx"}, "权利要求书")
    for doc in comparisonDocs or []:
        _validate_file_suffix(doc, {".pdf", ".docx"}, "对比文件")

    task = task_manager.create_task(
        owner_id=current_user.user_id,
        task_type=task_type,
        pn=None,
        title=f"审查意见答复任务 - {officeActionFile.filename or '未命名文件'}",
    )

    input_files: List[Dict[str, str]] = []

    office_action_path = await _save_upload_file(task.id, officeActionFile, "office_action", "office_action")
    input_files.append(
        {
            "file_type": "office_action",
            "original_name": officeActionFile.filename or "office_action.pdf",
            "stored_path": office_action_path,
        }
    )

    response_path = await _save_upload_file(task.id, responseFile, "office_action", "response")
    input_files.append(
        {
            "file_type": "response",
            "original_name": responseFile.filename or "response.pdf",
            "stored_path": response_path,
        }
    )

    if claimsFile:
        claims_path = await _save_upload_file(task.id, claimsFile, "office_action", "claims")
        input_files.append(
            {
                "file_type": "claims",
                "original_name": claimsFile.filename or "claims.pdf",
                "stored_path": claims_path,
            }
        )

    for index, doc in enumerate(comparisonDocs or []):
        doc_path = await _save_upload_file(task.id, doc, "office_action", f"comparison_{index + 1}")
        input_files.append(
            {
                "file_type": "comparison_doc",
                "original_name": doc.filename or f"comparison_{index + 1}.pdf",
                "stored_path": doc_path,
            }
        )

    task_metadata: Dict[str, Any] = {
        "task_type": task_type,
        "input_files": input_files,
    }
    task_manager.storage.update_task(task.id, metadata=task_metadata)

    cancel_event = Event()
    RUNNING_TASKS[task.id] = cancel_event
    workflow_task = asyncio.create_task(
        run_office_action_reply_task(
            task.id,
            input_files,
            cancel_event=cancel_event,
        )
    )
    workflow_task.add_done_callback(lambda _task: RUNNING_TASKS.pop(task.id, None))

    return TaskResponse(
        taskId=task.id,
        status="pending",
        message="审查意见答复任务已创建并开始处理。",
    )


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str, current_user: CurrentUser = Depends(_get_current_user)):
    task = _get_owned_task(task_id, current_user.user_id)
    return _task_to_response(task)


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
                    yield f"data: {json.dumps(payload)}\\n\\n"
                    break

                current_status = current_task.status.value
                current_progress = current_task.progress

                frontend_status = current_status
                if current_status in ["failed", "cancelled"]:
                    frontend_status = "error"

                if current_status != last_status or current_progress != last_progress:
                    progress_data = {
                        "taskType": _task_type(current_task),
                        "progress": current_progress,
                        "step": current_task.current_step or "",
                        "status": frontend_status,
                        "pn": current_task.pn or "",
                    }
                    if current_status == "completed":
                        progress_data["downloadUrl"] = f"/api/tasks/{task_id}/download"
                    elif current_status in ["failed", "cancelled", "error"]:
                        progress_data["error"] = current_task.error_message or "任务执行失败。"

                    yield f"data: {json.dumps(progress_data)}\\n\\n"
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
                yield f"data: {json.dumps(payload)}\\n\\n"
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

    task_type = _task_type(task)
    output_files = task.metadata.get("output_files", {}) if task.metadata else {}

    if task_type == TaskType.OFFICE_ACTION_REPLY.value:
        filename = f"审查意见答复报告_{task_id}.pdf"
    else:
        filename = f"专利分析报告_{task.pn or task_id}.pdf"

    if task_type == TaskType.PATENT_ANALYSIS.value:
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
    if pdf_path_str:
        pdf_path = Path(pdf_path_str)
    elif task_type == TaskType.OFFICE_ACTION_REPLY.value:
        pdf_path = Path(task.output_dir or settings.OUTPUT_DIR / task_id) / "final_report.pdf"
    else:
        patent_number = task.pn or task_id
        pdf_path = settings.OUTPUT_DIR / patent_number / f"{patent_number}.pdf"

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
