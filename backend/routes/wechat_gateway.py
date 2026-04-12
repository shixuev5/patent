"""Internal routes for WeChat gateway runtime."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from backend.models import (
    InternalWeChatInboundMessageRequest,
    InternalWeChatInboundMessageResponse,
)
from backend.routes.account import (
    _ensure_internal_gateway_token,
    _ensure_wechat_integration_enabled,
)
from backend.routes.tasks import (
    _build_task_download_filename,
    _build_task_pdf_r2_key,
    _task_type,
    task_manager,
)
from backend.storage import TaskType
from backend.utils import _build_r2_storage
from backend.wechat_runtime import WeChatRuntimeService
from config import settings


router = APIRouter()
runtime_service = WeChatRuntimeService(task_manager=task_manager)


@router.post("/api/internal/wechat/messages", response_model=InternalWeChatInboundMessageResponse)
async def post_internal_wechat_message(
    payload: InternalWeChatInboundMessageRequest,
    _token: str = Depends(_ensure_internal_gateway_token),
):
    _ensure_wechat_integration_enabled()
    return await runtime_service.handle_inbound_message(
        bot_account_id=payload.botAccountId,
        wechat_peer_id=payload.wechatPeerId,
        wechat_peer_name=payload.wechatPeerName,
        text=payload.text,
        attachments=payload.attachments,
    )


@router.get("/api/internal/wechat/tasks/{task_id}/download")
async def get_internal_wechat_task_download(
    task_id: str,
    _token: str = Depends(_ensure_internal_gateway_token),
):
    _ensure_wechat_integration_enabled()
    task = task_manager.storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    task_status = getattr(getattr(task, "status", None), "value", getattr(task, "status", None))
    if str(task_status or "").strip() != "completed":
        raise HTTPException(status_code=400, detail="task is not completed")

    task_type = _task_type(task)
    output_files = task.metadata.get("output_files", {}) if isinstance(getattr(task, "metadata", None), dict) else {}
    filename = _build_task_download_filename(task_type, task)

    if task_type == TaskType.AI_SEARCH.value:
        bundle_path_text = str(output_files.get("bundle_zip") or "").strip()
        bundle_path = Path(bundle_path_text) if bundle_path_text else Path(task.output_dir or settings.OUTPUT_DIR / task_id) / "ai_search_result_bundle.zip"
        if not bundle_path.exists():
            raise HTTPException(status_code=404, detail="ai search bundle not found")
        return FileResponse(path=str(bundle_path), filename=filename, media_type="application/zip")

    r2_storage = _build_r2_storage()
    r2_key = _build_task_pdf_r2_key(task_type, task.pn, r2_storage)
    if r2_key and r2_storage.enabled:
        r2_pdf = await asyncio.to_thread(r2_storage.get_bytes, r2_key)
        if r2_pdf:
            return StreamingResponse(
                BytesIO(r2_pdf),
                media_type="application/pdf",
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
            )

    pdf_path_str = str(output_files.get("pdf") or "").strip()
    if pdf_path_str:
        pdf_path = Path(pdf_path_str)
    elif task_type == TaskType.AI_REPLY.value:
        pdf_path = Path(task.output_dir or settings.OUTPUT_DIR / task_id) / "final_report.pdf"
    else:
        artifact_name = task.pn or task_id
        pdf_path = Path(task.output_dir or settings.OUTPUT_DIR / task_id) / f"{artifact_name}.pdf"

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="pdf artifact not found")
    return FileResponse(path=str(pdf_path), filename=filename, media_type="application/pdf")
