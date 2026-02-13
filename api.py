"""
专利分析后端 API

功能：
- 匿名令牌鉴权与任务归属校验
- 按用户每日配额限制
- 创建任务与进度追踪
- 报告下载
- 基于 R2 的跨用户结果复用缓存
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from config import settings
from src.storage import TaskStatus, get_pipeline_manager
from src.storage.r2_storage import R2Config, R2Storage


class TaskResponse(BaseModel):
    taskId: str
    status: str
    message: str


class GuestAuthResponse(BaseModel):
    token: str
    userId: str
    expiresAt: str


class UsageResponse(BaseModel):
    userId: str
    dailyLimit: int
    usedToday: int
    remaining: int
    resetAt: str


@dataclass
class CurrentUser:
    user_id: str


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

task_manager = get_pipeline_manager()


def _parse_bool(value: Optional[str]) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _build_r2_storage() -> R2Storage:
    config = R2Config(
        endpoint_url=os.getenv("R2_ENDPOINT_URL", ""),
        access_key_id=os.getenv("R2_ACCESS_KEY_ID", ""),
        secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY", ""),
        bucket=os.getenv("R2_BUCKET", ""),
        enabled=_parse_bool(os.getenv("R2_ENABLED", "false")),
        region=os.getenv("R2_REGION", "auto"),
        key_prefix=os.getenv("R2_KEY_PREFIX", "patent"),
    )
    return R2Storage(config)


AUTH_SECRET = os.getenv("AUTH_SECRET", "change-this-secret-in-production")
AUTH_TOKEN_TTL_DAYS = _parse_int(os.getenv("AUTH_TOKEN_TTL_DAYS"), 30)
MAX_DAILY_ANALYSIS = _parse_int(os.getenv("MAX_DAILY_ANALYSIS"), 3)
APP_TZ_OFFSET_HOURS = _parse_int(os.getenv("APP_TZ_OFFSET_HOURS"), 8)

r2_storage = _build_r2_storage()


def _read_local_pdf_bytes(pdf_path: str) -> Optional[bytes]:
    if not pdf_path:
        return None

    path = Path(pdf_path)
    if not path.exists() or not path.is_file():
        return None
    return path.read_bytes()


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def _sign_payload(payload_b64: str) -> str:
    return hmac.new(AUTH_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def _issue_token(user_id: str) -> tuple[str, int]:
    now = int(time.time())
    exp = now + AUTH_TOKEN_TTL_DAYS * 24 * 60 * 60
    payload = {
        "uid": user_id,
        "iat": now,
        "exp": exp,
    }
    payload_b64 = _b64_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    signature = _sign_payload(payload_b64)
    return f"{payload_b64}.{signature}", exp


def _verify_token(token: str) -> Optional[dict]:
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None

    expected = _sign_payload(payload_b64)
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(_b64_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None

    exp = payload.get("exp")
    uid = payload.get("uid")
    if not uid or not isinstance(uid, str):
        return None
    if not isinstance(exp, int) or exp <= int(time.time()):
        return None
    return payload


def _extract_token_from_request(
    authorization: Optional[str],
    query_token: Optional[str],
) -> Optional[str]:
    if authorization:
        value = authorization.strip()
        if value.lower().startswith("bearer "):
            token = value[7:].strip()
            if token:
                return token
    if query_token:
        return query_token.strip()
    return None


def _get_current_user(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> CurrentUser:
    raw_token = _extract_token_from_request(authorization, token)
    if not raw_token:
        raise HTTPException(status_code=401, detail="需要身份认证。")

    payload = _verify_token(raw_token)
    if not payload:
        raise HTTPException(status_code=401, detail="令牌无效或已过期。")

    return CurrentUser(user_id=payload["uid"])


def _quota_reset_utc() -> datetime:
    local_now = datetime.now(timezone.utc) + timedelta(hours=APP_TZ_OFFSET_HOURS)
    next_local_day = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_local_day - timedelta(hours=APP_TZ_OFFSET_HOURS)


def _get_user_usage(owner_id: str) -> UsageResponse:
    used_today = task_manager.storage.count_user_tasks_today(owner_id, tz_offset_hours=APP_TZ_OFFSET_HOURS)
    remaining = max(0, MAX_DAILY_ANALYSIS - used_today)
    reset_at = _quota_reset_utc().isoformat()
    return UsageResponse(
        userId=owner_id,
        dailyLimit=MAX_DAILY_ANALYSIS,
        usedToday=used_today,
        remaining=remaining,
        resetAt=reset_at,
    )


def _enforce_daily_quota(owner_id: str):
    usage = _get_user_usage(owner_id)
    if usage.usedToday >= usage.dailyLimit:
        raise HTTPException(
            status_code=429,
            detail={
                "message": "已达到每日分析上限。",
                "dailyLimit": usage.dailyLimit,
                "usedToday": usage.usedToday,
                "remaining": usage.remaining,
                "resetAt": usage.resetAt,
            },
        )


def _get_owned_task(task_id: str, owner_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if task.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return task


PATENT_NUMBER_REGEX = re.compile(r"^[A-Z]{2}\d{6,}[A-Z0-9]*$")
APPLICATION_NUMBER_REGEX = re.compile(r"^\d{8,}\.?\d*$")
RAW_TEXT_PATENT_REGEX = re.compile(r"\b[A-Z]{2}\s*\d{6,}\s*[A-Z0-9]?\b")


def _normalize_patent_candidate(value: Optional[str]) -> str:
    if not value:
        return ""
    normalized = "".join(ch for ch in str(value).upper().strip() if ch.isalnum() or ch in {".", "-", "_"})
    return normalized


def _score_patent_candidate(candidate: str) -> int:
    if not candidate:
        return 0
    if PATENT_NUMBER_REGEX.match(candidate):
        return 100
    if APPLICATION_NUMBER_REGEX.match(candidate):
        return 70
    if any(ch.isalpha() for ch in candidate) and any(ch.isdigit() for ch in candidate):
        return 40
    if any(ch.isdigit() for ch in candidate):
        return 20
    return 1


def _extract_patent_number_from_outputs(
    output_pdf: str,
    fallback_pn: Optional[str] = None,
) -> Optional[str]:
    candidates = []
    if fallback_pn:
        candidates.append(_normalize_patent_candidate(fallback_pn))

    pdf_path = Path(output_pdf)
    output_dir = pdf_path.parent

    patent_json_path = output_dir / "patent.json"
    if patent_json_path.exists():
        try:
            patent_data = json.loads(patent_json_path.read_text(encoding="utf-8"))
            biblio = patent_data.get("bibliographic_data", {}) or {}
            candidates.append(_normalize_patent_candidate(biblio.get("publication_number")))
            candidates.append(_normalize_patent_candidate(biblio.get("application_number")))
        except Exception:
            pass

    raw_md_path = output_dir / settings.MINERU_TEMP_FOLDER / "raw.md"
    if raw_md_path.exists():
        try:
            raw_text = raw_md_path.read_text(encoding="utf-8", errors="ignore")
            match = RAW_TEXT_PATENT_REGEX.search(raw_text.upper())
            if match:
                candidates.append(_normalize_patent_candidate(match.group(0)))
        except Exception:
            pass

    best_candidate = ""
    best_score = 0
    for candidate in candidates:
        if not candidate:
            continue
        score = _score_patent_candidate(candidate)
        if score > best_score:
            best_candidate = candidate
            best_score = score

    return best_candidate or None


async def run_pipeline_task(
    task_id: str,
    pn: str,
    upload_file_path: Optional[str] = None,
):
    """后台执行分析流程，并在成功后按需写入对象存储缓存。"""
    try:
        print(f"[任务 {task_id}] 开始处理：{pn}")
        task_manager.start_task(task_id)
        task_manager.update_progress(task_id, 1, "任务已开始")

        loop = asyncio.get_event_loop()
        # 延迟导入，避免启动时加载重型依赖导致端口迟迟无法绑定
        from main import PatentPipeline
        pipeline = PatentPipeline(pn, upload_file_path)

        def run_pipeline():
            return pipeline.run()

        pipeline_future = loop.run_in_executor(None, run_pipeline)

        progress = 5
        while not pipeline_future.done():
            task_manager.update_progress(task_id, progress, "正在分析专利")
            progress = min(progress + 3, 90)
            await asyncio.sleep(2)

        result = await pipeline_future

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
            if pdf_bytes and r2_storage.enabled:
                r2_key = r2_storage.build_patent_pdf_cache_key(resolved_pn or pn)
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
        task_manager.fail_task(task_id, "任务已取消")
        raise
    except Exception as exc:
        print(f"[任务 {task_id}] 异常失败：{str(exc)}")
        task_manager.fail_task(task_id, str(exc))


@app.post("/api/auth/guest", response_model=GuestAuthResponse)
async def create_guest_auth(request: Request):
    # 基于IP地址生成用户ID
    client_ip = request.client.host
    # 使用哈希函数对IP地址进行处理，确保生成固定长度的用户ID
    import hashlib
    ip_hash = hashlib.sha256(client_ip.encode('utf-8')).hexdigest()[:16]
    user_id = f"ip_{ip_hash}"
    token, exp = _issue_token(user_id)
    return GuestAuthResponse(
        token=token,
        userId=user_id,
        expiresAt=datetime.fromtimestamp(exp, tz=timezone.utc).isoformat(),
    )


@app.get("/api/usage", response_model=UsageResponse)
async def get_usage(current_user: CurrentUser = Depends(_get_current_user)):
    return _get_user_usage(current_user.user_id)


@app.get("/api/tasks")
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


@app.post("/api/tasks", response_model=TaskResponse)
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
    if file:
        upload_dir = settings.UPLOAD_DIR
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_filename = Path(file.filename or "upload.pdf").name
        upload_file_path = upload_dir / f"{task.id}_{safe_filename}"
        content = await file.read()
        with open(upload_file_path, "wb") as handle:
            handle.write(content)
        if r2_storage.enabled and content:
            upload_r2_key = r2_storage.build_upload_key(task.id, safe_filename)
            stored_upload = await asyncio.to_thread(
                r2_storage.put_bytes,
                upload_r2_key,
                content,
                file.content_type or "application/pdf",
            )
            if stored_upload:
                task_manager.storage.update_task(
                    task.id,
                    metadata=json.dumps({"upload_r2_key": upload_r2_key}, ensure_ascii=False),
                )

    # 仅在“专利号提交”场景进行预先缓存命中；上传文件需先完成解析后才能确定专利号。
    if patentNumber and not file:
        cached_pdf = None
        r2_cache_key = None
        r2_hit = False
        if r2_storage.enabled:
            r2_cache_key = r2_storage.build_patent_pdf_cache_key(pn)
            cached_pdf = await asyncio.to_thread(r2_storage.get_bytes, r2_cache_key)
            r2_hit = cached_pdf is not None

        if cached_pdf:
            task_manager.start_task(task.id)
            task_manager.update_progress(task.id, 100, "命中缓存，直接复用")
            output_files = {"pn": pn}
            if r2_hit and r2_cache_key:
                output_files["r2_key"] = r2_cache_key
                output_files["source"] = "r2_cache"
            task_manager.complete_task(
                task.id,
                output_files=output_files,
            )
            return TaskResponse(
                taskId=task.id,
                status="completed",
                message="已复用历史分析结果。",
            )

    asyncio.create_task(
        run_pipeline_task(
            task.id,
            pn,
            str(upload_file_path) if upload_file_path else None,
        )
    )

    return TaskResponse(
        taskId=task.id,
        status="pending",
        message="任务已创建并开始处理。",
    )


@app.get("/api/tasks/{task_id}")
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


@app.get("/api/tasks/{task_id}/progress")
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


@app.get("/api/tasks/{task_id}/download")
async def download_result(task_id: str, current_user: CurrentUser = Depends(_get_current_user)):
    task = _get_owned_task(task_id, current_user.user_id)

    if task.status.value != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成。")

    output_files = task.metadata.get("output_files", {}) if task.metadata else {}
    filename = f"专利分析报告_{task.pn or task_id}.pdf"

    r2_key = output_files.get("r2_key")
    if r2_key and r2_storage.enabled:
        r2_pdf = await asyncio.to_thread(r2_storage.get_bytes, r2_key)
        if r2_pdf:
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


@app.get("/api/health")
async def health_check():
    active_count = len(task_manager.list_tasks(status=TaskStatus.PROCESSING, limit=1000))
    stats = task_manager.storage.get_statistics()
    return {
        "status": "正常",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
        "active_tasks": active_count,
        "statistics": {
            "total": stats.get("total", 0),
            "by_status": stats.get("by_status", {}),
            "today_created": stats.get("today_created", 0),
        },
        "cache": {
            "r2_enabled": r2_storage.enabled,
        },
        "storage": {
            "backend": task_manager.storage.__class__.__name__,
        },
        "auth": {
            "daily_limit": MAX_DAILY_ANALYSIS,
            "token_ttl_days": AUTH_TOKEN_TTL_DAYS,
        },
    }


if __name__ == "__main__":
    import uvicorn
    import os

    settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 读取 PORT 环境变量，Hugging Face Spaces 默认使用 7860
    port = int(os.getenv("PORT", 7860))

    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=port,
        reload=True,
        log_level="info",
    )
