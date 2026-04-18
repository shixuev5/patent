"""
任务管理路由
"""
import asyncio
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from threading import Event
from time import perf_counter
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from loguru import logger

from config import VERSION, settings
from backend.auth import _get_current_user
from backend.log_context import bind_task_logger, task_log_context
from backend.notifications import build_task_notification_dispatcher
from backend.system_logs import emit_system_log
from backend.usage import _enforce_daily_quota
from backend.task_usage_tracking import (
    create_task_usage_collector,
    persist_task_usage,
    task_usage_collection,
)
from backend.models import CurrentUser, PatentNumberValidationResponse, TaskResponse
from backend.utils import (
    _cleanup_path,
    _read_local_pdf_bytes,
    _build_r2_storage,
)
from backend.storage import TaskType, get_pipeline_manager
from backend.time_utils import to_utc_z, utc_now_z

from langgraph.checkpoint.memory import InMemorySaver

router = APIRouter()
task_manager = get_pipeline_manager()

RUNNING_TASKS: Dict[str, Event] = {}
PATENT_CHECKPOINTERS: Dict[str, InMemorySaver] = {}
AI_REVIEW_CHECKPOINTERS: Dict[str, InMemorySaver] = {}
OAR_CHECKPOINTERS: Dict[str, InMemorySaver] = {}

PROGRESS_WRITE_THROTTLE_SECONDS = 3.0

ALLOWED_TASK_TYPES = {
    TaskType.PATENT_ANALYSIS.value,
    TaskType.AI_REVIEW.value,
    TaskType.AI_REPLY.value,
}
KNOWN_TASK_TYPES = ALLOWED_TASK_TYPES | {
    TaskType.AI_SEARCH.value,
}
VISIBLE_TASK_TYPES = {
    TaskType.PATENT_ANALYSIS.value,
    TaskType.AI_REVIEW.value,
    TaskType.AI_REPLY.value,
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

PATENT_NODE_LABELS = {
    "download": "下载专利文档",
    "parse": "解析 PDF 文件",
    "transform": "专利结构化转换",
    "extract": "知识提取",
    "vision": "视觉处理",
    "vision_extract": "视觉提取",
    "vision_annotate": "视觉标注",
    "generate": "报告内容生成",
    "generate_core": "生成报告核心内容",
    "generate_figures": "生成图解说明",
    "search_matrix": "生成检索要素",
    "search_semantic": "生成语义检索",
    "search_join": "汇总检索策略",
    "render": "渲染报告",
    "handle_error": "处理异常",
}

AI_REVIEW_NODE_LABELS = {
    "hydrate": "加载复用数据",
    "download": "下载专利文档",
    "parse": "解析 PDF 文件",
    "transform": "专利结构化转换",
    "extract": "知识提取",
    "vision_extract": "视觉提取",
    "check": "AI 审查",
    "render": "渲染报告",
    "handle_error": "处理异常",
}


def _to_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def _notify_task_terminal_email_sync(
    task_id: str,
    terminal_status: str,
    *,
    task_type: Optional[str] = None,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    service = build_task_notification_dispatcher(
        storage=task_manager.storage,
        system_log_emitter=emit_system_log,
    )
    return service.notify_task_terminal_status(
        task_id,
        terminal_status=terminal_status,
        task_type=task_type,
        error_message=error_message,
    )


async def _notify_task_terminal_email(
    task_id: str,
    terminal_status: str,
    *,
    task_type: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    try:
        await asyncio.to_thread(
            _notify_task_terminal_email_sync,
            task_id,
            terminal_status,
            task_type=task_type,
            error_message=error_message,
        )
    except Exception as exc:
        logger.exception(f"任务终态通知失败：task_id={task_id} status={terminal_status} error={exc}")


def _should_persist_progress_update(
    *,
    previous_step: str,
    next_step: str,
    last_persist_at: float,
    now: float,
    throttle_seconds: float = PROGRESS_WRITE_THROTTLE_SECONDS,
) -> bool:
    if str(next_step or "") != str(previous_step or ""):
        return True
    if last_persist_at <= 0:
        return True
    return (now - last_persist_at) >= max(0.1, float(throttle_seconds))


def _get_patent_checkpointer(task_id: str) -> InMemorySaver:
    checkpointer = PATENT_CHECKPOINTERS.get(task_id)
    if checkpointer is None:
        checkpointer = InMemorySaver()
        PATENT_CHECKPOINTERS[task_id] = checkpointer
    return checkpointer


def _get_oar_checkpointer(task_id: str) -> InMemorySaver:
    checkpointer = OAR_CHECKPOINTERS.get(task_id)
    if checkpointer is None:
        checkpointer = InMemorySaver()
        OAR_CHECKPOINTERS[task_id] = checkpointer
    return checkpointer


def _get_ai_review_checkpointer(task_id: str) -> InMemorySaver:
    checkpointer = AI_REVIEW_CHECKPOINTERS.get(task_id)
    if checkpointer is None:
        checkpointer = InMemorySaver()
        AI_REVIEW_CHECKPOINTERS[task_id] = checkpointer
    return checkpointer


def _normalize_task_type(raw: Optional[str]) -> str:
    task_type = (raw or TaskType.PATENT_ANALYSIS.value).strip().lower()
    if task_type not in ALLOWED_TASK_TYPES:
        raise HTTPException(status_code=400, detail="不支持的任务类型。")
    return task_type


def _task_type(task: Any) -> str:
    raw_task_type = str(getattr(task, "task_type", "")).strip().lower()
    if raw_task_type in KNOWN_TASK_TYPES:
        return raw_task_type
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    metadata_type = str(metadata.get("task_type", "")).strip().lower()
    if metadata_type in KNOWN_TASK_TYPES:
        return metadata_type
    return TaskType.PATENT_ANALYSIS.value


def _task_to_response(task: Any) -> Dict[str, Any]:
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    task_type = _task_type(task)
    seed_available = False
    if task_type == TaskType.PATENT_ANALYSIS.value:
        seed_available = str(getattr(task.status, "value", task.status) or "") == "completed"
    elif task_type == TaskType.AI_REPLY.value:
        seed_available = bool(metadata.get("search_followup_needed"))
    return {
        "id": task.id,
        "pn": task.pn,
        "title": task.title,
        "taskType": task_type,
        "status": task.status.value,
        "progress": task.progress,
        "step": task.current_step,
        "error": task.error_message,
        "aiSearchSeedAvailable": seed_available,
        "created_at": to_utc_z(task.created_at, naive_strategy="utc"),
        "updated_at": to_utc_z(task.updated_at, naive_strategy="utc"),
        "completed_at": to_utc_z(task.completed_at, naive_strategy="utc") if task.completed_at else None,
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

    dedup: List[str] = []
    seen = set()
    for path in paths:
        if path and path not in seen:
            dedup.append(path)
            seen.add(path)
    return dedup


def _task_status_value(task: Any) -> str:
    status = getattr(task, "status", "")
    if hasattr(status, "value"):
        status = status.value
    return str(status or "").strip().lower()


def _is_running_task(task: Any) -> bool:
    return _task_status_value(task) in {"pending", "processing"}


def _is_retryable_task(task: Any) -> bool:
    return _task_status_value(task) in {"failed", "cancelled"}


def _task_input_files(task: Any) -> List[Dict[str, Any]]:
    metadata = task.metadata if isinstance(task.metadata, dict) else {}
    raw_items = metadata.get("input_files")
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _copy_existing_upload_file(
    task_id: str,
    *,
    source_path: str,
    subdir: str,
    prefix: str,
    original_name: Optional[str],
) -> str:
    source = Path(str(source_path or "").strip())
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=409, detail=f"原任务输入文件不存在，无法重试：{source}")

    safe_name = Path(str(original_name or source.name or f"{prefix}.dat")).name
    upload_dir = settings.UPLOAD_DIR / task_id / subdir
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / f"{prefix}_{safe_name}"
    shutil.copy2(source, destination)
    return str(destination)


def _copy_retry_input_item(
    *,
    new_task_id: str,
    task_type: str,
    item: Dict[str, Any],
    comparison_index: int = 0,
) -> Dict[str, Any]:
    file_type = str(item.get("file_type") or "").strip()
    original_name = str(item.get("original_name") or "").strip() or None
    source_path = str(item.get("stored_path") or "").strip()

    if task_type in {TaskType.PATENT_ANALYSIS.value, TaskType.AI_REVIEW.value}:
        stored_path = _copy_existing_upload_file(
            new_task_id,
            source_path=source_path,
            subdir="patent",
            prefix="source",
            original_name=original_name,
        )
        copied: Dict[str, Any] = {
            "file_type": "patent_pdf",
            "original_name": original_name or Path(stored_path).name,
            "stored_path": stored_path,
        }
        sha256 = _compute_file_sha256(stored_path)
        if sha256:
            copied["sha256"] = sha256
        return copied

    prefix = {
        "office_action": "office_action",
        "response": "response",
        "claims_previous": "claims_previous",
        "claims_current": "claims_current",
    }.get(file_type)
    if not prefix and file_type == "comparison_doc":
        prefix = f"comparison_{max(1, comparison_index)}"
    if not prefix:
        raise HTTPException(status_code=409, detail=f"原任务输入文件类型不支持重试：{file_type or 'unknown'}")

    stored_path = _copy_existing_upload_file(
        new_task_id,
        source_path=source_path,
        subdir="office_action",
        prefix=prefix,
        original_name=original_name,
    )
    return {
        "file_type": file_type,
        "original_name": original_name or Path(stored_path).name,
        "stored_path": stored_path,
    }


def _build_retry_title(task: Any, task_type: str) -> str:
    input_files = _task_input_files(task)
    preferred_filename: Optional[str] = None
    if task_type == TaskType.AI_REPLY.value:
        preferred_filename = next(
            (
                str(item.get("original_name") or "").strip()
                for item in input_files
                if str(item.get("file_type") or "").strip() == "office_action"
                and str(item.get("original_name") or "").strip()
            ),
            None,
        )
    elif input_files:
        preferred_filename = next(
            (
                str(item.get("original_name") or "").strip()
                for item in input_files
                if str(item.get("original_name") or "").strip()
            ),
            None,
        )
    return _build_task_title(
        task_type,
        pn=getattr(task, "pn", None) if task_type != TaskType.AI_REPLY.value else None,
        filename=preferred_filename or getattr(task, "title", None),
    )


def _enqueue_pipeline_task(
    task: Any,
    *,
    upload_file_path: Optional[str] = None,
    input_sha256: Optional[str] = None,
    input_files: Optional[List[Dict[str, str]]] = None,
) -> None:
    task_type = _task_type(task)
    cancel_event = Event()
    RUNNING_TASKS[task.id] = cancel_event
    try:
        if task_type == TaskType.PATENT_ANALYSIS.value:
            pipeline_task = asyncio.create_task(
                run_patent_analysis_task(
                    task.id,
                    getattr(task, "pn", None),
                    upload_file_path,
                    input_sha256=input_sha256,
                    cancel_event=cancel_event,
                )
            )
        elif task_type == TaskType.AI_REVIEW.value:
            pipeline_task = asyncio.create_task(
                run_ai_review_task(
                    task.id,
                    getattr(task, "pn", None),
                    upload_file_path,
                    input_sha256=input_sha256,
                    cancel_event=cancel_event,
                )
            )
        else:
            pipeline_task = asyncio.create_task(
                run_ai_reply_task(
                    task.id,
                    input_files or [],
                    cancel_event=cancel_event,
                )
            )
    except Exception:
        RUNNING_TASKS.pop(task.id, None)
        raise

    pipeline_task.add_done_callback(lambda _task, task_id=task.id: RUNNING_TASKS.pop(task_id, None))


def _prepare_retry_task(
    source_task: Any,
    *,
    current_user: CurrentUser,
) -> tuple[Any, Optional[str], Optional[str], Optional[List[Dict[str, str]]]]:
    task_type = _task_type(source_task)
    source_input_files = _task_input_files(source_task)
    title = _build_retry_title(source_task, task_type)
    pn = getattr(source_task, "pn", None) if task_type != TaskType.AI_REPLY.value else None
    retry_task = task_manager.create_task(
        owner_id=current_user.user_id,
        task_type=task_type,
        pn=pn,
        title=title,
    )

    retry_metadata: Dict[str, Any] = {
        "task_type": task_type,
        "retry_of": source_task.id,
    }

    upload_file_path: Optional[str] = None
    input_sha256: Optional[str] = None
    input_files: Optional[List[Dict[str, str]]] = None

    if task_type == TaskType.AI_REPLY.value:
        if not source_input_files:
            raise HTTPException(status_code=409, detail="原任务缺少可复用输入文件，无法重试。")
        copied_inputs: List[Dict[str, str]] = []
        comparison_index = 0
        for item in source_input_files:
            if str(item.get("file_type") or "").strip() == "comparison_doc":
                comparison_index += 1
            copied_inputs.append(
                _copy_retry_input_item(
                    new_task_id=retry_task.id,
                    task_type=task_type,
                    item=item,
                    comparison_index=comparison_index,
                )
            )
        retry_metadata["input_files"] = copied_inputs
        input_files = copied_inputs
    else:
        retry_metadata["input_files"] = []
        patent_source = next(
            (
                item for item in source_input_files
                if str(item.get("file_type") or "").strip() == "patent_pdf"
            ),
            None,
        )
        if patent_source:
            copied_item = _copy_retry_input_item(
                new_task_id=retry_task.id,
                task_type=task_type,
                item=patent_source,
            )
            retry_metadata["input_files"] = [copied_item]
            upload_file_path = copied_item["stored_path"]
            input_sha256 = str(copied_item.get("sha256") or "").strip() or None
        elif not getattr(source_task, "pn", None):
            raise HTTPException(status_code=409, detail="原任务缺少可复用文件或专利号，无法重试。")

    task_manager.storage.update_task(retry_task.id, metadata=retry_metadata)
    return retry_task, upload_file_path, input_sha256, input_files


def _cleanup_task_resources(task: Any):
    _cleanup_path(task.output_dir)
    for path in _collect_upload_paths(task):
        _cleanup_path(path)


def _cleanup_upload_only(task: Any):
    for path in _collect_upload_paths(task):
        _cleanup_path(path)


def _compute_file_sha256(file_path: Optional[str]) -> Optional[str]:
    path = Path(str(file_path or "").strip())
    if not path.exists() or not path.is_file():
        return None

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_input_sha256(input_sha256: Optional[str], fallback_file_path: Optional[str] = None) -> Optional[str]:
    normalized = str(input_sha256 or "").strip().lower()
    if normalized:
        return normalized
    return _compute_file_sha256(fallback_file_path)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_pn(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().upper()
    return normalized or None


def _extract_patent_title(patent_info: Any) -> Optional[str]:
    if not isinstance(patent_info, dict):
        return None

    title_value = patent_info.get("TITLE")
    if isinstance(title_value, dict):
        for key in ("CN", "ZH", "EN"):
            cleaned = str(title_value.get(key) or "").strip()
            if cleaned:
                return cleaned
        for value in title_value.values():
            cleaned = str(value or "").strip()
            if cleaned:
                return cleaned
    elif isinstance(title_value, str):
        cleaned = title_value.strip()
        if cleaned:
            return cleaned

    for key in ("TITLE_LANG", "INVENTION_TITLE", "patent_title", "title"):
        cleaned = str(patent_info.get(key) or "").strip()
        if cleaned:
            return cleaned
    return None


def _query_patent_info_for_publication_number(patent_number: str) -> Optional[Dict[str, Any]]:
    normalized_pn = _normalize_pn(patent_number)
    if not normalized_pn:
        return None

    try:
        from agents.common.search_clients.factory import SearchClientFactory

        client = SearchClientFactory.get_client("zhihuiya")
        if not hasattr(client, "_query_patent_info_by_count"):
            raise RuntimeError("智慧芽客户端未提供 count 查询能力")
        patent_info = client._query_patent_info_by_count(f"PN:({normalized_pn})")  # type: ignore[attr-defined]
        return patent_info if isinstance(patent_info, dict) else None
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"按公开号查询专利失败，patent_number={normalized_pn} error={exc}")
        raise HTTPException(status_code=503, detail="专利校验服务暂不可用，请稍后重试。") from exc


def _validate_patent_analysis_patent_number(patent_number: str) -> Dict[str, Any]:
    normalized_pn = _normalize_pn(patent_number)
    if not normalized_pn:
        raise HTTPException(status_code=400, detail="缺少专利公开号。")

    patent_info = _query_patent_info_for_publication_number(normalized_pn)
    if not patent_info:
        raise HTTPException(status_code=404, detail="未在智慧芽检索到该专利公开号，无法创建 AI 分析任务。")

    resolved_pn = _normalize_pn(patent_info.get("PN")) or normalized_pn
    patent_title = _extract_patent_title(patent_info)
    return {
        "patentNumber": resolved_pn,
        "patentTitle": patent_title,
        "patentInfo": patent_info,
    }


def _strip_filename_suffix(filename: Any) -> Optional[str]:
    cleaned = str(filename or "").strip()
    if not cleaned:
        return None
    suffix = Path(cleaned).suffix
    if suffix:
        cleaned = cleaned[: -len(suffix)]
    cleaned = cleaned.strip()
    return cleaned or None


def _build_task_title(
    task_type: str,
    *,
    pn: Any = None,
    filename: Any = None,
) -> str:
    normalized_pn = _normalize_pn(pn)
    if normalized_pn:
        return normalized_pn

    cleaned_filename = _strip_filename_suffix(filename)
    if task_type == TaskType.AI_REPLY.value:
        return cleaned_filename or "AI 答复任务"
    return cleaned_filename or "未命名任务"


def _build_task_pdf_r2_key(task_type: str, pn: Optional[str], r2_storage: Any) -> Optional[str]:
    resolved_pn = _normalize_pn(pn)
    if not resolved_pn:
        return None
    if task_type == TaskType.AI_SEARCH.value:
        return None
    if task_type == TaskType.AI_REPLY.value:
        return r2_storage.build_ai_reply_pdf_key(resolved_pn)
    if task_type == TaskType.AI_REVIEW.value:
        return r2_storage.build_ai_review_pdf_key(resolved_pn)
    return r2_storage.build_patent_pdf_key(resolved_pn)


def _build_task_download_filename(task_type: str, task: Any) -> str:
    artifact_name = str(getattr(task, "pn", None) or getattr(task, "title", None) or getattr(task, "id", "")).strip()
    artifact_name = artifact_name or str(getattr(task, "id", ""))
    if task_type == TaskType.AI_SEARCH.value:
        return f"AI 检索结果_{artifact_name}.zip"
    if task_type == TaskType.AI_REPLY.value:
        return f"AI 答复报告_{artifact_name}.pdf"
    if task_type == TaskType.AI_REVIEW.value:
        return f"AI 审查报告_{artifact_name}.pdf"
    return f"AI 分析报告_{artifact_name}.pdf"


def _extract_ai_reply_application_number(result: Dict[str, Any]) -> Optional[str]:
    result_dict = _to_dict(result)
    prepared = _to_dict(result_dict.get("prepared_materials"))
    original_patent = _to_dict(prepared.get("original_patent"))
    office_action = _to_dict(prepared.get("office_action"))
    root_office_action = _to_dict(result_dict.get("office_action"))
    return (
        str(original_patent.get("application_number") or "").strip()
        or str(office_action.get("application_number") or "").strip()
        or str(root_office_action.get("application_number") or "").strip()
        or None
    )


def _extract_ai_reply_publication_number(result: Dict[str, Any]) -> Optional[str]:
    result_dict = _to_dict(result)
    prepared = _to_dict(result_dict.get("prepared_materials"))
    original_patent = _to_dict(prepared.get("original_patent"))
    original_patent_data = _to_dict(original_patent.get("data"))
    biblio = _to_dict(original_patent_data.get("bibliographic_data"))
    publication_number = _normalize_pn(
        biblio.get("publication_number") or original_patent_data.get("pn")
    )
    if publication_number:
        return publication_number

    appno = _extract_ai_reply_application_number(result_dict)
    if not appno:
        return None

    search_results = result_dict.get("search_results")
    if not isinstance(search_results, list):
        return None
    for item in search_results:
        item_dict = _to_dict(item)
        structured = _to_dict(item_dict.get(appno))
        if not structured:
            continue
        structured_biblio = _to_dict(structured.get("bibliographic_data"))
        publication_number = _normalize_pn(
            structured_biblio.get("publication_number") or structured.get("pn")
        )
        if publication_number:
            return publication_number
    return None


def _resolve_ai_reply_publication_number_by_application_number(
    application_number: Optional[str],
) -> Optional[str]:
    appno = str(application_number or "").strip()
    if not appno:
        return None
    try:
        from agents.common.search_clients.factory import SearchClientFactory

        client = SearchClientFactory.get_client("zhihuiya")
        if not hasattr(client, "get_publication_number_by_application_number"):
            return None
        pn = client.get_publication_number_by_application_number(appno)
        return _normalize_pn(pn)
    except Exception as exc:
        logger.warning(f"按申请号查询公开号失败，application_number={appno} error={exc}")
        return None


def _iso_now() -> str:
    return utc_now_z(timespec="microseconds")


def _build_analysis_json_payload(
    *,
    resolved_pn: str,
    task_id: str,
    input_sha256: Optional[str],
    report_core_json: Optional[Dict[str, Any]],
    analysis_json: Optional[Dict[str, Any]],
    search_json: Optional[Dict[str, Any]],
    parts_db: Optional[Dict[str, Any]],
    image_parts: Optional[Dict[str, Any]],
    output_pdf: Optional[str],
    output_md: Optional[str],
) -> Dict[str, Any]:
    return {
        "metadata": {
            "版本号": "analysis.v1",
            "created_at": _iso_now(),
            "app_version": str(VERSION),
            "task_type": TaskType.PATENT_ANALYSIS.value,
            "task_id": task_id,
            "resolved_pn": resolved_pn,
            "input_sha256": str(input_sha256 or "").strip() or None,
        },
        "report_core": report_core_json or {},
        "report": analysis_json or {},
        "search_strategy": search_json or {},
        "parts": parts_db or {},
        "image_parts": image_parts or {},
        "artifact_refs": {
            "pdf": str(output_pdf or "").strip() or None,
            "md": str(output_md or "").strip() or None,
        },
    }


def _build_ai_review_json_payload(
    *,
    resolved_pn: str,
    task_id: str,
    input_sha256: Optional[str],
    check_result: Optional[Dict[str, Any]],
    output_pdf: Optional[str],
    output_md: Optional[str],
) -> Dict[str, Any]:
    return {
        "metadata": {
            "schema_version": "ai_review.v1",
            "created_at": _iso_now(),
            "task_type": TaskType.AI_REVIEW.value,
            "task_id": task_id,
            "resolved_pn": resolved_pn,
            "input_sha256": str(input_sha256 or "").strip() or None,
        },
        "check_result": check_result or {},
        "artifact_refs": {
            "pdf": str(output_pdf or "").strip() or None,
            "md": str(output_md or "").strip() or None,
        },
    }


def _get_cached_analysis_payload(
    *,
    pn: Optional[str],
    input_sha256: Optional[str],
) -> Optional[Dict[str, Any]]:
    storage = task_manager.storage
    r2_storage = _build_r2_storage()
    if not r2_storage.enabled:
        return None

    resolved_pn = str(pn or "").strip().upper() or None
    if not resolved_pn and input_sha256 and hasattr(storage, "get_patent_analysis_by_sha256"):
        try:
            row = storage.get_patent_analysis_by_sha256(input_sha256)
        except Exception:
            row = None
        if isinstance(row, dict):
            resolved_pn = str(row.get("pn") or "").strip().upper() or None

    if not resolved_pn:
        return None

    analysis_key = r2_storage.build_analysis_json_key(resolved_pn)
    analysis_bytes = r2_storage.get_bytes(analysis_key)
    if not analysis_bytes:
        return None

    try:
        payload = json.loads(analysis_bytes.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _best_effort_fail_task(task_id: str, message: str):
    try:
        task_manager.fail_task(task_id, message)
    except Exception:
        pass


def _get_owned_task(task_id: str, owner_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if task.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return task


async def run_patent_analysis_task(
    task_id: str,
    pn: Optional[str],
    upload_file_path: Optional[str] = None,
    input_sha256: Optional[str] = None,
    cancel_event: Optional[Event] = None,
):
    """后台执行 AI 分析 LangGraph 流程，并在成功后按需写入对象存储缓存。"""
    task_logger = bind_task_logger(task_id, TaskType.PATENT_ANALYSIS.value, pn=pn, stage="run_patent_analysis_task")
    task_snapshot = task_manager.get_task(task_id)
    owner_id = getattr(task_snapshot, "owner_id", "") or ""
    usage_collector = create_task_usage_collector(
        task_id=task_id,
        owner_id=owner_id,
        task_type=TaskType.PATENT_ANALYSIS.value,
    )
    normalized_input_sha256 = _resolve_input_sha256(input_sha256)
    try:
        task_logger.info("开始处理任务")
        task_manager.start_task(task_id)
        r2_storage = _build_r2_storage()

        cached_analysis_payload = _get_cached_analysis_payload(pn=pn, input_sha256=normalized_input_sha256)
        if cached_analysis_payload:
            metadata = cached_analysis_payload.get("metadata", {})
            resolved_pn = str(metadata.get("resolved_pn") or pn or "").strip().upper()
            if resolved_pn and r2_storage.enabled:
                pdf_key = r2_storage.build_patent_pdf_key(resolved_pn)
                has_pdf = await asyncio.to_thread(r2_storage.key_exists, pdf_key)
                if has_pdf:
                    output_files = {
                        "pn": resolved_pn,
                        "r2_key": pdf_key,
                        "analysis_r2_key": r2_storage.build_analysis_json_key(resolved_pn),
                    }
                    task_manager.complete_task(task_id, output_files=output_files)
                    if resolved_pn and resolved_pn != (pn or ""):
                        task_manager.storage.update_task(
                            task_id,
                            pn=resolved_pn,
                            title=_build_task_title(TaskType.PATENT_ANALYSIS.value, pn=resolved_pn),
                        )
                    if hasattr(task_manager.storage, "record_patent_analysis"):
                        try:
                            task_manager.storage.record_patent_analysis(resolved_pn, normalized_input_sha256)
                        except TypeError:
                            task_manager.storage.record_patent_analysis(resolved_pn)
                    task_logger.bind(stage="reuse").success(f"命中 R2 复用：{resolved_pn}")
                    emit_system_log(
                        category="task_execution",
                        event_name="task_completed",
                        owner_id=owner_id,
                        task_id=task_id,
                        task_type=TaskType.PATENT_ANALYSIS.value,
                        success=True,
                        message="命中历史分析结果，直接复用",
                        payload={"pn": resolved_pn, "reuse": True},
                    )
                    await _notify_task_terminal_email(
                        task_id,
                        "completed",
                        task_type=TaskType.PATENT_ANALYSIS.value,
                    )
                    return

        emit_system_log(
            category="task_execution",
            event_name="task_started",
            owner_id=owner_id,
            task_id=task_id,
            task_type=TaskType.PATENT_ANALYSIS.value,
            success=True,
            message="AI 分析任务开始执行",
            payload={"pn": pn or None},
        )
        task_manager.update_progress(task_id, 5, "正在准备材料")

        loop = asyncio.get_event_loop()

        def run_workflow() -> Dict[str, Any]:
            workflow_start = perf_counter()
            from agents.patent_analysis.main import create_workflow, build_runtime_config
            from agents.patent_analysis.src.state import WorkflowConfig, WorkflowState

            output_dir = settings.OUTPUT_DIR / task_id
            output_dir.mkdir(parents=True, exist_ok=True)

            config = WorkflowConfig(
                cache_dir=str(output_dir / ".cache"),
                pdf_parser=os.getenv("PDF_PARSER", "local"),
                cancel_event=cancel_event,
                enable_checkpoint=True,
                checkpoint_ns="patent_analysis",
                checkpointer=_get_patent_checkpointer(task_id),
            )
            initial_state = WorkflowState(
                pn=str(pn or "").strip(),
                upload_file_path=upload_file_path,
                output_dir=str(output_dir),
                task_id=task_id,
                current_node="start",
                status="pending",
                progress=0.0,
            )

            workflow = create_workflow(config)
            runtime_config = build_runtime_config(task_id, checkpoint_ns=config.checkpoint_ns)
            last_state: Dict[str, Any] = _to_dict(initial_state)
            try:
                with task_log_context(task_id, TaskType.PATENT_ANALYSIS.value, pn=pn or "-"), task_usage_collection(usage_collector):
                    last_progress = -1
                    last_step = ""
                    last_persist_at = 0.0
                    for state_value in workflow.stream(initial_state, config=runtime_config, stream_mode="values"):
                        if cancel_event and cancel_event.is_set():
                            raise RuntimeError("任务已取消")
                        state_dict = _to_dict(state_value)
                        if not state_dict:
                            continue
                        last_state = state_dict
                        node_name = str(state_dict.get("current_node", "")).strip()
                        if not node_name or node_name == "start":
                            continue
                        step_label = PATENT_NODE_LABELS.get(node_name, "处理中")
                        progress_raw = state_dict.get("progress")
                        try:
                            progress = int(float(progress_raw))
                        except Exception:
                            continue
                        progress = max(0, min(95, progress))
                        if progress <= 0:
                            continue
                        if progress != last_progress or step_label != last_step:
                            resolved_pn = str(state_dict.get("resolved_pn") or pn or "")
                            now = perf_counter()
                            if _should_persist_progress_update(
                                previous_step=last_step,
                                next_step=step_label,
                                last_persist_at=last_persist_at,
                                now=now,
                            ):
                                task_manager.update_progress(task_id, progress, step_label)
                                emit_system_log(
                                    category="task_execution",
                                    event_name="task_progress",
                                    owner_id=owner_id,
                                    task_id=task_id,
                                    task_type=TaskType.PATENT_ANALYSIS.value,
                                    success=True,
                                    message=f"progress={progress} step={step_label}",
                                    payload={
                                        "progress": progress,
                                        "step": step_label,
                                        "node": node_name,
                                    },
                                )
                                last_persist_at = now
                            last_progress = progress
                            last_step = step_label
                return last_state
            finally:
                workflow_elapsed = perf_counter() - workflow_start
                status = str(last_state.get("status", "unknown")).strip().lower()
                task_logger.info(
                    f"patent_analysis workflow 总耗时: {workflow_elapsed:.3f}s status={status}"
                )

        result = await loop.run_in_executor(None, run_workflow)

        if cancel_event and cancel_event.is_set():
            task_manager.cancel_task(task_id, "任务已取消")
            latest_task = task_manager.get_task(task_id)
            task_logger.warning("任务已取消")
            emit_system_log(
                category="task_execution",
                event_name="task_cancelled",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.PATENT_ANALYSIS.value,
                success=False,
                message="任务已取消",
            )
            return

        status = str(result.get("status", "failed")).strip().lower()
        if status == "cancelled":
            task_manager.cancel_task(task_id, "任务已取消")
            latest_task = task_manager.get_task(task_id)
            task_logger.warning("任务已取消")
            emit_system_log(
                category="task_execution",
                event_name="task_cancelled",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.PATENT_ANALYSIS.value,
                success=False,
                message="流程返回 cancelled",
            )
            return

        if status == "failed":
            errors = result.get("errors") or []
            first_error = ""
            if isinstance(errors, list) and errors:
                first_error = str(_to_dict(errors[0]).get("error_message", "")).strip()
            error_msg = first_error or "AI 分析任务执行失败"
            task_manager.fail_task(task_id, error_msg)
            latest_task = task_manager.get_task(task_id)
            task_logger.error(f"任务失败：{error_msg}")
            emit_system_log(
                category="task_execution",
                event_name="task_failed",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.PATENT_ANALYSIS.value,
                success=False,
                message=error_msg,
                payload={"status": status},
            )
            await _notify_task_terminal_email(
                task_id,
                "failed",
                task_type=TaskType.PATENT_ANALYSIS.value,
                error_message=error_msg,
            )
            return

        if status == "completed":
            task_manager.update_progress(task_id, 95, "正在整理报告")
            output_pdf = str(result.get("final_output_pdf", "")).strip()
            if not output_pdf:
                output_pdf = str((settings.OUTPUT_DIR / task_id) / "final.pdf")
            output_md = str(result.get("final_output_md", "")).strip()
            if not output_md:
                output_md = str((settings.OUTPUT_DIR / task_id) / "final.md")

            pipeline_pn = str(result.get("resolved_pn", "")).strip()
            final_pn = pipeline_pn or (pn or "") or task_id
            if final_pn and final_pn != (pn or ""):
                task_manager.storage.update_task(
                    task_id,
                    pn=final_pn,
                    title=_build_task_title(TaskType.PATENT_ANALYSIS.value, pn=final_pn),
                )

            output_files = {
                "pdf": output_pdf,
                "md": output_md,
                "pn": final_pn,
            }

            pdf_bytes = await asyncio.to_thread(_read_local_pdf_bytes, output_pdf)
            if not pdf_bytes:
                error_msg = f"报告文件不存在或为空：{output_pdf}"
                task_manager.fail_task(task_id, error_msg)
                latest_task = task_manager.get_task(task_id)
                task_logger.bind(stage="finalize_report").error(error_msg)
                await _notify_task_terminal_email(
                    task_id,
                    "failed",
                    task_type=TaskType.PATENT_ANALYSIS.value,
                    error_message=error_msg,
                )
                return

            output_dir = settings.OUTPUT_DIR / task_id
            analysis_json_path = output_dir / "analysis.json"
            patent_json_path = output_dir / "patent.json"
            patent_json_payload = _load_json(patent_json_path) or {}
            resolved_input_sha256 = _resolve_input_sha256(
                normalized_input_sha256,
                str(output_dir / "raw.pdf"),
            )

            analysis_payload = _build_analysis_json_payload(
                resolved_pn=final_pn,
                task_id=task_id,
                input_sha256=resolved_input_sha256,
                report_core_json=_to_dict(result.get("report_core_json")),
                analysis_json=_to_dict(result.get("analysis_json")),
                search_json=_to_dict(result.get("search_json")),
                parts_db=_to_dict(result.get("parts_db")),
                image_parts=_to_dict(result.get("image_parts")),
                output_pdf=output_pdf,
                output_md=output_md,
            )
            analysis_json_path.write_text(
                json.dumps(analysis_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            output_files["json"] = str(analysis_json_path)

            if r2_storage.enabled:
                pdf_key = r2_storage.build_patent_pdf_key(final_pn)
                stored_pdf = await asyncio.to_thread(
                    r2_storage.put_bytes,
                    pdf_key,
                    pdf_bytes,
                    "application/pdf",
                )
                analysis_key = r2_storage.build_analysis_json_key(final_pn)
                stored_analysis = await asyncio.to_thread(
                    r2_storage.put_bytes,
                    analysis_key,
                    json.dumps(analysis_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                    "application/json",
                )
                patent_key = r2_storage.build_patent_json_key(final_pn)
                stored_patent = await asyncio.to_thread(
                    r2_storage.put_bytes,
                    patent_key,
                    json.dumps(patent_json_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                    "application/json",
                )

                if stored_pdf:
                    output_files["r2_key"] = pdf_key
                if stored_analysis:
                    output_files["analysis_r2_key"] = analysis_key
                if stored_patent:
                    output_files["patent_r2_key"] = patent_key

            task_manager.complete_task(task_id, output_files=output_files)
            if hasattr(task_manager.storage, "record_patent_analysis"):
                try:
                    task_manager.storage.record_patent_analysis(final_pn, resolved_input_sha256)
                except TypeError:
                    task_manager.storage.record_patent_analysis(final_pn)
            task_logger.bind(stage="finalize_report").success(f"任务已完成：{output_pdf}")
            emit_system_log(
                category="task_execution",
                event_name="task_completed",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.PATENT_ANALYSIS.value,
                success=True,
                message="任务执行完成",
                payload={"output_pdf": output_pdf, "pn": final_pn},
            )
            await _notify_task_terminal_email(
                task_id,
                "completed",
                task_type=TaskType.PATENT_ANALYSIS.value,
            )
        else:
            error_msg = f"未知流程状态: {status}"
            task_manager.fail_task(task_id, error_msg)
            latest_task = task_manager.get_task(task_id)
            task_logger.error(f"任务失败：{error_msg}")
            emit_system_log(
                category="task_execution",
                event_name="task_failed",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.PATENT_ANALYSIS.value,
                success=False,
                message=error_msg,
                payload={"status": status},
            )
            await _notify_task_terminal_email(
                task_id,
                "failed",
                task_type=TaskType.PATENT_ANALYSIS.value,
                error_message=error_msg,
            )

    except asyncio.CancelledError:
        task_logger.warning("任务已取消")
        task_manager.cancel_task(task_id, "任务已取消")
        latest_task = task_manager.get_task(task_id)
        emit_system_log(
            category="task_execution",
            event_name="task_cancelled",
            owner_id=owner_id,
            task_id=task_id,
            task_type=TaskType.PATENT_ANALYSIS.value,
            success=False,
            message="任务被 asyncio 取消",
        )
        raise
    except Exception as exc:
        if cancel_event and cancel_event.is_set():
            task_logger.warning("任务已取消")
            task_manager.cancel_task(task_id, "任务已取消")
            latest_task = task_manager.get_task(task_id)
            emit_system_log(
                category="task_execution",
                event_name="task_cancelled",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.PATENT_ANALYSIS.value,
                success=False,
                message="异常分支检测到任务已取消",
            )
            return
        task_logger.exception(f"任务异常失败：{str(exc)}")
        task_manager.fail_task(task_id, str(exc))
        latest_task = task_manager.get_task(task_id)
        emit_system_log(
            category="task_execution",
            event_name="task_exception",
            owner_id=owner_id,
            task_id=task_id,
            task_type=TaskType.PATENT_ANALYSIS.value,
            success=False,
            message=str(exc),
        )
        await _notify_task_terminal_email(
            task_id,
            "failed",
            task_type=TaskType.PATENT_ANALYSIS.value,
            error_message=str(exc),
        )
    finally:
        latest_task = task_manager.get_task(task_id)
        if latest_task:
            usage_collector.mark_status(latest_task.status.value)
        persist_task_usage(task_manager.storage, usage_collector)
        PATENT_CHECKPOINTERS.pop(task_id, None)


async def run_ai_review_task(
    task_id: str,
    pn: Optional[str],
    upload_file_path: Optional[str] = None,
    input_sha256: Optional[str] = None,
    cancel_event: Optional[Event] = None,
):
    task_logger = bind_task_logger(task_id, TaskType.AI_REVIEW.value, pn=pn, stage="run_ai_review_task")
    task_snapshot = task_manager.get_task(task_id)
    owner_id = getattr(task_snapshot, "owner_id", "") or ""
    usage_collector = create_task_usage_collector(
        task_id=task_id,
        owner_id=owner_id,
        task_type=TaskType.AI_REVIEW.value,
    )
    try:
        task_logger.info("开始处理任务")
        task_manager.start_task(task_id)
        emit_system_log(
            category="task_execution",
            event_name="task_started",
            owner_id=owner_id,
            task_id=task_id,
            task_type=TaskType.AI_REVIEW.value,
            success=True,
            message="AI 审查任务开始执行",
            payload={"pn": pn or None},
        )
        task_manager.update_progress(task_id, 5, "正在准备材料")

        cached_analysis_payload = _get_cached_analysis_payload(pn=pn, input_sha256=input_sha256)
        loop = asyncio.get_event_loop()

        def run_workflow() -> Dict[str, Any]:
            workflow_start = perf_counter()
            from agents.ai_review.main import build_runtime_config, create_workflow
            from agents.ai_review.src.state import WorkflowConfig, WorkflowState

            output_dir = settings.OUTPUT_DIR / task_id
            output_dir.mkdir(parents=True, exist_ok=True)

            config = WorkflowConfig(
                cache_dir=str(output_dir / ".cache"),
                pdf_parser=os.getenv("PDF_PARSER", "local"),
                cancel_event=cancel_event,
                enable_checkpoint=True,
                checkpoint_ns="ai_review",
                checkpointer=_get_ai_review_checkpointer(task_id),
            )
            initial_state = WorkflowState(
                pn=str(pn or "").strip(),
                upload_file_path=upload_file_path,
                output_dir=str(output_dir),
                task_id=task_id,
                cached_analysis=cached_analysis_payload,
                current_node="start",
                status="pending",
                progress=0.0,
            )

            workflow = create_workflow(config)
            runtime_config = build_runtime_config(task_id, checkpoint_ns=config.checkpoint_ns)
            last_state: Dict[str, Any] = _to_dict(initial_state)
            try:
                with task_log_context(task_id, TaskType.AI_REVIEW.value, pn=pn or "-"), task_usage_collection(usage_collector):
                    last_progress = -1
                    last_step = ""
                    last_persist_at = 0.0
                    for state_value in workflow.stream(initial_state, config=runtime_config, stream_mode="values"):
                        if cancel_event and cancel_event.is_set():
                            raise RuntimeError("任务已取消")
                        state_dict = _to_dict(state_value)
                        if not state_dict:
                            continue
                        last_state = state_dict
                        node_name = str(state_dict.get("current_node", "")).strip()
                        if not node_name or node_name == "start":
                            continue
                        step_label = AI_REVIEW_NODE_LABELS.get(node_name, "处理中")
                        progress_raw = state_dict.get("progress")
                        try:
                            progress = int(float(progress_raw))
                        except Exception:
                            continue
                        progress = max(0, min(95, progress))
                        if progress <= 0:
                            continue
                        if progress != last_progress or step_label != last_step:
                            now = perf_counter()
                            if _should_persist_progress_update(
                                previous_step=last_step,
                                next_step=step_label,
                                last_persist_at=last_persist_at,
                                now=now,
                            ):
                                task_manager.update_progress(task_id, progress, step_label)
                                emit_system_log(
                                    category="task_execution",
                                    event_name="task_progress",
                                    owner_id=owner_id,
                                    task_id=task_id,
                                    task_type=TaskType.AI_REVIEW.value,
                                    success=True,
                                    message=f"progress={progress} step={step_label}",
                                    payload={
                                        "progress": progress,
                                        "step": step_label,
                                        "node": node_name,
                                    },
                                )
                                last_persist_at = now
                            last_progress = progress
                            last_step = step_label
                return last_state
            finally:
                workflow_elapsed = perf_counter() - workflow_start
                status = str(last_state.get("status", "unknown")).strip().lower()
                task_logger.info(
                    f"ai_review workflow 总耗时: {workflow_elapsed:.3f}s status={status}"
                )

        result = await loop.run_in_executor(None, run_workflow)

        if cancel_event and cancel_event.is_set():
            task_manager.cancel_task(task_id, "任务已取消")
            emit_system_log(
                category="task_execution",
                event_name="task_cancelled",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.AI_REVIEW.value,
                success=False,
                message="任务已取消",
            )
            return

        status = str(result.get("status", "failed")).strip().lower()
        if status == "cancelled":
            task_manager.cancel_task(task_id, "任务已取消")
            emit_system_log(
                category="task_execution",
                event_name="task_cancelled",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.AI_REVIEW.value,
                success=False,
                message="流程返回 cancelled",
            )
            return

        if status == "failed":
            errors = result.get("errors") or []
            first_error = ""
            if isinstance(errors, list) and errors:
                first_error = str(_to_dict(errors[0]).get("error_message", "")).strip()
            error_msg = first_error or "AI 审查任务执行失败"
            task_manager.fail_task(task_id, error_msg)
            emit_system_log(
                category="task_execution",
                event_name="task_failed",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.AI_REVIEW.value,
                success=False,
                message=error_msg,
                payload={"status": status},
            )
            return

        if status == "completed":
            task_manager.update_progress(task_id, 95, "正在整理报告")
            output_pdf = str(result.get("final_output_pdf", "")).strip()
            if not output_pdf:
                output_pdf = str((settings.OUTPUT_DIR / task_id) / "final.pdf")
            output_md = str(result.get("final_output_md", "")).strip()
            if not output_md:
                output_md = str((settings.OUTPUT_DIR / task_id) / "final.md")

            pipeline_pn = str(result.get("resolved_pn", "")).strip()
            final_pn = pipeline_pn or (pn or "") or task_id
            if final_pn and final_pn != (pn or ""):
                task_manager.storage.update_task(
                    task_id,
                    pn=final_pn,
                    title=_build_task_title(TaskType.AI_REVIEW.value, pn=final_pn),
                )

            pdf_bytes = await asyncio.to_thread(_read_local_pdf_bytes, output_pdf)
            if not pdf_bytes:
                error_msg = f"报告文件不存在或为空：{output_pdf}"
                task_manager.fail_task(task_id, error_msg)
                task_logger.bind(stage="finalize_report").error(error_msg)
                return

            output_dir = settings.OUTPUT_DIR / task_id
            ai_review_json_path = output_dir / "ai_review.json"
            ai_review_payload = _build_ai_review_json_payload(
                resolved_pn=final_pn,
                task_id=task_id,
                input_sha256=input_sha256,
                check_result=_to_dict(result.get("check_result")),
                output_pdf=output_pdf,
                output_md=output_md,
            )
            ai_review_json_path.write_text(
                json.dumps(ai_review_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            output_files = {
                "pdf": output_pdf,
                "md": output_md,
                "json": str(ai_review_json_path),
                "pn": final_pn,
            }

            r2_storage = _build_r2_storage()
            if r2_storage.enabled:
                ai_review_pdf_key = r2_storage.build_ai_review_pdf_key(final_pn)
                ai_review_json_key = r2_storage.build_ai_review_json_key(final_pn)
                stored_pdf = await asyncio.to_thread(
                    r2_storage.put_bytes,
                    ai_review_pdf_key,
                    pdf_bytes,
                    "application/pdf",
                )
                stored_json = await asyncio.to_thread(
                    r2_storage.put_bytes,
                    ai_review_json_key,
                    json.dumps(ai_review_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                    "application/json",
                )
                if stored_pdf:
                    output_files["r2_key"] = ai_review_pdf_key
                if stored_json:
                    output_files["ai_review_r2_key"] = ai_review_json_key

            task_manager.complete_task(task_id, output_files=output_files)
            task_logger.bind(stage="finalize_report").success(f"任务已完成：{output_pdf}")
            emit_system_log(
                category="task_execution",
                event_name="task_completed",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.AI_REVIEW.value,
                success=True,
                message="任务执行完成",
                payload={"output_pdf": output_pdf, "pn": final_pn},
            )
            return

        error_msg = f"未知流程状态: {status}"
        task_manager.fail_task(task_id, error_msg)
        emit_system_log(
            category="task_execution",
            event_name="task_failed",
            owner_id=owner_id,
            task_id=task_id,
            task_type=TaskType.AI_REVIEW.value,
            success=False,
            message=error_msg,
            payload={"status": status},
        )

    except asyncio.CancelledError:
        task_logger.warning("任务已取消")
        task_manager.cancel_task(task_id, "任务已取消")
        emit_system_log(
            category="task_execution",
            event_name="task_cancelled",
            owner_id=owner_id,
            task_id=task_id,
            task_type=TaskType.AI_REVIEW.value,
            success=False,
            message="任务被 asyncio 取消",
        )
        raise
    except Exception as exc:
        if cancel_event and cancel_event.is_set():
            task_logger.warning("任务已取消")
            task_manager.cancel_task(task_id, "任务已取消")
            emit_system_log(
                category="task_execution",
                event_name="task_cancelled",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.AI_REVIEW.value,
                success=False,
                message="异常分支检测到任务已取消",
            )
            return
        task_logger.exception(f"任务异常失败：{str(exc)}")
        task_manager.fail_task(task_id, str(exc))
        emit_system_log(
            category="task_execution",
            event_name="task_exception",
            owner_id=owner_id,
            task_id=task_id,
            task_type=TaskType.AI_REVIEW.value,
            success=False,
            message=str(exc),
        )
    finally:
        latest_task = task_manager.get_task(task_id)
        if latest_task:
            usage_collector.mark_status(latest_task.status.value)
        persist_task_usage(task_manager.storage, usage_collector)
        AI_REVIEW_CHECKPOINTERS.pop(task_id, None)


async def run_ai_reply_task(
    task_id: str,
    input_files: List[Dict[str, str]],
    cancel_event: Optional[Event] = None,
):
    """后台执行 AI 答复流程。"""
    task_logger = bind_task_logger(task_id, TaskType.AI_REPLY.value, pn="-", stage="run_ai_reply_task")
    task_snapshot = task_manager.get_task(task_id)
    owner_id = getattr(task_snapshot, "owner_id", "") or ""
    usage_collector = create_task_usage_collector(
        task_id=task_id,
        owner_id=owner_id,
        task_type=TaskType.AI_REPLY.value,
    )
    try:
        task_logger.info("开始处理任务")
        task_manager.start_task(task_id)
        emit_system_log(
            category="task_execution",
            event_name="task_started",
            owner_id=owner_id,
            task_id=task_id,
            task_type=TaskType.AI_REPLY.value,
            success=True,
            message="AI 答复任务开始执行",
            payload={"input_file_count": len(input_files)},
        )
        task_manager.update_progress(task_id, 5, "正在准备材料")

        loop = asyncio.get_event_loop()

        def run_workflow() -> Dict[str, Any]:
            from agents.ai_reply.main import create_workflow
            from agents.ai_reply.main import build_runtime_config
            from agents.ai_reply.src.state import WorkflowConfig, WorkflowState, InputFile

            output_dir = settings.OUTPUT_DIR / task_id
            output_dir.mkdir(parents=True, exist_ok=True)

            config = WorkflowConfig(
                cache_dir=str(output_dir / ".cache"),
                pdf_parser=os.getenv("PDF_PARSER", "local"),
                cancel_event=cancel_event,
                enable_checkpoint=True,
                checkpoint_ns=TaskType.AI_REPLY.value,
                checkpointer=_get_oar_checkpointer(task_id),
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
            runtime_config = build_runtime_config(task_id, checkpoint_ns=config.checkpoint_ns)
            with task_log_context(task_id, TaskType.AI_REPLY.value, pn="-"), task_usage_collection(usage_collector):
                last_state: Dict[str, Any] = _to_dict(initial_state)
                last_progress = -1
                last_step = ""
                last_persist_at = 0.0
                for state_value in workflow.stream(initial_state, config=runtime_config, stream_mode="values"):
                    if cancel_event and cancel_event.is_set():
                        raise RuntimeError("任务已取消")
                    state_dict = _to_dict(state_value)
                    if not state_dict:
                        continue
                    last_state = state_dict
                    node_name = str(state_dict.get("current_node", "")).strip()
                    if not node_name or node_name == "start":
                        continue
                    step_label = NODE_LABELS.get(node_name, "处理中")
                    progress_raw = state_dict.get("progress")
                    try:
                        progress = int(float(progress_raw))
                    except Exception:
                        continue
                    progress = max(0, min(95, progress))
                    if progress <= 0:
                        continue
                    if progress != last_progress or step_label != last_step:
                        now = perf_counter()
                        if _should_persist_progress_update(
                            previous_step=last_step,
                            next_step=step_label,
                            last_persist_at=last_persist_at,
                            now=now,
                        ):
                            task_manager.update_progress(task_id, progress, step_label)
                            emit_system_log(
                                category="task_execution",
                                event_name="task_progress",
                                owner_id=owner_id,
                                task_id=task_id,
                                task_type=TaskType.AI_REPLY.value,
                                success=True,
                                message=f"progress={progress} step={step_label}",
                                payload={
                                    "progress": progress,
                                    "step": step_label,
                                    "node": node_name,
                                },
                            )
                            last_persist_at = now
                        last_progress = progress
                        last_step = step_label
            return last_state

        result = await asyncio.wait_for(
            loop.run_in_executor(None, run_workflow),
            timeout=settings.OAR_WORKFLOW_TIMEOUT_SECONDS,
        )

        if cancel_event and cancel_event.is_set():
            task_manager.cancel_task(task_id, "任务已取消")
            latest_task = task_manager.get_task(task_id)
            task_logger.warning("任务已取消")
            emit_system_log(
                category="task_execution",
                event_name="task_cancelled",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.AI_REPLY.value,
                success=False,
                message="任务已取消",
            )
            return

        status = str(result.get("status", "failed")).strip().lower()
        if status == "cancelled":
            task_manager.cancel_task(task_id, "任务已取消")
            task_logger.warning("任务已取消")
            emit_system_log(
                category="task_execution",
                event_name="task_cancelled",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.AI_REPLY.value,
                success=False,
                message="流程返回 cancelled",
            )
            return
        if status == "failed":
            errors = result.get("errors") or []
            first_error = ""
            if isinstance(errors, list) and errors:
                first_error = str(_to_dict(errors[0]).get("error_message", "")).strip()
            error_msg = first_error or "AI 答复任务执行失败"
            task_manager.fail_task(task_id, error_msg)
            latest_task = task_manager.get_task(task_id)
            task_logger.error(f"任务失败：{error_msg}")
            emit_system_log(
                category="task_execution",
                event_name="task_failed",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.AI_REPLY.value,
                success=False,
                message=error_msg,
                payload={"status": status},
            )
            await _notify_task_terminal_email(
                task_id,
                "failed",
                task_type=TaskType.AI_REPLY.value,
                error_message=error_msg,
            )
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
            latest_task = task_manager.get_task(task_id)
            task_logger.bind(stage="finalize_report").error(error_msg)
            await _notify_task_terminal_email(
                task_id,
                "failed",
                task_type=TaskType.AI_REPLY.value,
                error_message=error_msg,
            )
            return

        resolved_pn = _extract_ai_reply_publication_number(result)
        if not resolved_pn:
            application_number = _extract_ai_reply_application_number(result)
            resolved_pn = await asyncio.to_thread(
                _resolve_ai_reply_publication_number_by_application_number,
                application_number,
            )
        existing_task = task_manager.get_task(task_id)
        existing_pn = _normalize_pn(getattr(existing_task, "pn", "") if existing_task else "")
        title_updates: Dict[str, Any] = {}
        if resolved_pn and resolved_pn != existing_pn:
            title_updates["pn"] = resolved_pn
            title_updates["title"] = _build_task_title(TaskType.AI_REPLY.value, pn=resolved_pn)
        if title_updates:
            task_manager.storage.update_task(task_id, **title_updates)
        final_pn = resolved_pn or existing_pn

        output_files: Dict[str, str] = {"pdf": pdf_path}
        if Path(md_path).exists():
            output_files["md"] = md_path
        if Path(json_path).exists():
            output_files["json"] = json_path
        if final_pn:
            output_files["pn"] = final_pn

        r2_storage = _build_r2_storage()
        if r2_storage.enabled and final_pn:
            ai_reply_pdf_key = r2_storage.build_ai_reply_pdf_key(final_pn)
            stored_pdf = await asyncio.to_thread(
                r2_storage.put_bytes,
                ai_reply_pdf_key,
                pdf_bytes,
                "application/pdf",
            )
            if stored_pdf:
                output_files["r2_key"] = ai_reply_pdf_key
            else:
                emit_system_log(
                    category="task_execution",
                    event_name="task_artifact_upload_failed",
                    owner_id=owner_id,
                    task_id=task_id,
                    task_type=TaskType.AI_REPLY.value,
                    success=False,
                    message="AI 答复 PDF 上传到 R2 失败",
                    payload={"pn": final_pn, "r2_key": ai_reply_pdf_key, "artifact": "pdf"},
                )
                task_logger.bind(stage="r2_upload").warning(f"AI 答复 PDF 上传到 R2 失败：{ai_reply_pdf_key}")

            json_file = Path(json_path)
            if json_file.exists():
                json_bytes = await asyncio.to_thread(json_file.read_bytes)
                ai_reply_json_key = r2_storage.build_ai_reply_json_key(final_pn)
                stored_json = await asyncio.to_thread(
                    r2_storage.put_bytes,
                    ai_reply_json_key,
                    json_bytes,
                    "application/json",
                )
                if stored_json:
                    output_files["ai_reply_r2_key"] = ai_reply_json_key
                else:
                    emit_system_log(
                        category="task_execution",
                        event_name="task_artifact_upload_failed",
                        owner_id=owner_id,
                        task_id=task_id,
                        task_type=TaskType.AI_REPLY.value,
                        success=False,
                        message="AI 答复 JSON 上传到 R2 失败",
                        payload={"pn": final_pn, "r2_key": ai_reply_json_key, "artifact": "json"},
                    )
                    task_logger.bind(stage="r2_upload").warning(f"AI 答复 JSON 上传到 R2 失败：{ai_reply_json_key}")

        final_report = _to_dict(result.get("final_report"))
        search_followup_section = _to_dict(final_report.get("search_followup_section")) if final_report else {}
        search_followup_needed = bool(search_followup_section.get("needed"))
        existing_metadata = existing_task.metadata.copy() if existing_task and isinstance(existing_task.metadata, dict) else {}
        existing_metadata["search_followup_needed"] = search_followup_needed
        task_manager.storage.update_task(task_id, metadata=existing_metadata)

        task_manager.complete_task(task_id, output_files=output_files)
        task_logger.bind(stage="finalize_report").success(f"任务已完成：{pdf_path}")
        emit_system_log(
            category="task_execution",
            event_name="task_completed",
            owner_id=owner_id,
            task_id=task_id,
            task_type=TaskType.AI_REPLY.value,
            success=True,
            message="任务执行完成",
            payload={"output_pdf": pdf_path},
        )
        await _notify_task_terminal_email(
            task_id,
            "completed",
            task_type=TaskType.AI_REPLY.value,
        )

    except asyncio.CancelledError:
        task_logger.warning("任务已取消")
        task_manager.cancel_task(task_id, "任务已取消")
        latest_task = task_manager.get_task(task_id)
        emit_system_log(
            category="task_execution",
            event_name="task_cancelled",
            owner_id=owner_id,
            task_id=task_id,
            task_type=TaskType.AI_REPLY.value,
            success=False,
            message="任务被 asyncio 取消",
        )
        raise
    except asyncio.TimeoutError:
        error_msg = f"AI 答复任务超时（>{settings.OAR_WORKFLOW_TIMEOUT_SECONDS}秒）"
        task_logger.error(error_msg)
        task_manager.fail_task(task_id, error_msg)
        latest_task = task_manager.get_task(task_id)
        emit_system_log(
            category="task_execution",
            event_name="task_timeout",
            owner_id=owner_id,
            task_id=task_id,
            task_type=TaskType.AI_REPLY.value,
            success=False,
            message=error_msg,
        )
        await _notify_task_terminal_email(
            task_id,
            "failed",
            task_type=TaskType.AI_REPLY.value,
            error_message=error_msg,
        )
    except Exception as exc:
        if cancel_event and cancel_event.is_set():
            task_logger.warning("任务已取消")
            task_manager.cancel_task(task_id, "任务已取消")
            latest_task = task_manager.get_task(task_id)
            emit_system_log(
                category="task_execution",
                event_name="task_cancelled",
                owner_id=owner_id,
                task_id=task_id,
                task_type=TaskType.AI_REPLY.value,
                success=False,
                message="异常分支检测到任务已取消",
            )
            return
        task_logger.exception(f"任务异常失败：{str(exc)}")
        task_manager.fail_task(task_id, str(exc))
        latest_task = task_manager.get_task(task_id)
        emit_system_log(
            category="task_execution",
            event_name="task_exception",
            owner_id=owner_id,
            task_id=task_id,
            task_type=TaskType.AI_REPLY.value,
            success=False,
            message=str(exc),
        )
        await _notify_task_terminal_email(
            task_id,
            "failed",
            task_type=TaskType.AI_REPLY.value,
            error_message=str(exc),
        )
    finally:
        latest_task = task_manager.get_task(task_id)
        if latest_task:
            usage_collector.mark_status(latest_task.status.value)
        persist_task_usage(task_manager.storage, usage_collector)
        OAR_CHECKPOINTERS.pop(task_id, None)


@router.get("/api/tasks")
async def list_tasks(current_user: CurrentUser = Depends(_get_current_user)):
    """获取用户的任务列表"""
    tasks = [
        task
        for task in task_manager.list_tasks(owner_id=current_user.user_id)
        if _task_type(task) in VISIBLE_TASK_TYPES
    ]
    return {
        "tasks": [_task_to_response(task) for task in tasks],
        "total": len(tasks),
    }


@router.get("/api/tasks/patent-validation", response_model=PatentNumberValidationResponse)
async def validate_patent_number(
    patentNumber: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    _ = current_user
    result = _validate_patent_analysis_patent_number(patentNumber)
    return PatentNumberValidationResponse(
        patentNumber=result["patentNumber"],
        exists=True,
        patentTitle=result.get("patentTitle"),
        message="已在智慧芽检索到该专利，可创建 AI 分析任务。",
    )


@router.post("/api/tasks", response_model=TaskResponse)
async def create_task(
    request: Request,
    taskType: Optional[str] = Form(None),
    patentNumber: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    officeActionFile: Optional[UploadFile] = File(None),
    responseFile: Optional[UploadFile] = File(None),
    previousClaimsFile: Optional[UploadFile] = File(None),
    currentClaimsFile: Optional[UploadFile] = File(None),
    comparisonDocs: Optional[List[UploadFile]] = File(None),
    current_user: CurrentUser = Depends(_get_current_user),
):
    task_type = _normalize_task_type(taskType)
    _enforce_daily_quota(current_user.user_id, task_type=task_type)

    form_keys = set((await request.form()).keys())
    if "claimsFile" in form_keys:
        raise HTTPException(status_code=400, detail="claimsFile 已废弃，请改用 previousClaimsFile 或 currentClaimsFile。")

    if task_type in {TaskType.PATENT_ANALYSIS.value, TaskType.AI_REVIEW.value}:
        if not patentNumber and not file:
            raise HTTPException(status_code=400, detail="必须提供专利号或上传 PDF 文件。")

        if file:
            _validate_file_suffix(file, {".pdf"}, "专利文档")

        pn_value = (patentNumber or "").strip()
        pn = pn_value or None
        validated_patent_info: Optional[Dict[str, Any]] = None
        if task_type == TaskType.PATENT_ANALYSIS.value and pn:
            validated = _validate_patent_analysis_patent_number(pn)
            pn = validated["patentNumber"]
            validated_patent_info = validated
        task = task_manager.create_task(
            owner_id=current_user.user_id,
            task_type=task_type,
            pn=pn,
            title=_build_task_title(task_type, pn=pn, filename=file.filename if file else None),
        )
        emit_system_log(
            category="task_execution",
            event_name="task_created",
            owner_id=current_user.user_id,
            task_id=task.id,
            task_type=task_type,
            success=True,
            message="创建 AI 分析任务" if task_type == TaskType.PATENT_ANALYSIS.value else "创建 AI 审查任务",
            payload={
                "pn": pn,
                "has_upload_file": bool(file),
            },
        )

        task_metadata: Dict[str, Any] = {"task_type": task_type, "input_files": []}
        if validated_patent_info and validated_patent_info.get("patentTitle"):
            task_metadata["patent_title"] = validated_patent_info["patentTitle"]
        upload_file_path: Optional[str] = None
        upload_sha256: Optional[str] = None
        try:
            if file:
                upload_file_path = await _save_upload_file(task.id, file, "patent", "source")
                upload_sha256 = _compute_file_sha256(upload_file_path)
                task_metadata["input_files"].append(
                    {
                        "file_type": "patent_pdf",
                        "original_name": file.filename or "upload.pdf",
                        "stored_path": upload_file_path,
                        "sha256": upload_sha256,
                    }
                )
                task_manager.storage.update_task(
                    task.id,
                    metadata=task_metadata,
                )
            else:
                task_manager.storage.update_task(task.id, metadata=task_metadata)

            _enqueue_pipeline_task(task, upload_file_path=upload_file_path, input_sha256=upload_sha256)

            return TaskResponse(
                taskId=task.id,
                status="pending",
                message="任务已创建并开始处理。" if task_type == TaskType.PATENT_ANALYSIS.value else "AI 审查任务已创建并开始处理。",
            )
        except HTTPException as exc:
            _best_effort_fail_task(task.id, f"任务创建失败：{exc.detail}")
            _cleanup_path(upload_file_path)
            raise
        except Exception as exc:
            _best_effort_fail_task(task.id, f"任务创建失败：{str(exc)}")
            _cleanup_path(upload_file_path)
            raise HTTPException(status_code=500, detail="任务创建失败，请稍后重试。") from exc

    if not officeActionFile or not responseFile:
        raise HTTPException(status_code=400, detail="AI 答复任务必须上传审查意见通知书和意见陈述书。")

    _validate_file_suffix(officeActionFile, {".pdf", ".doc", ".docx"}, "审查意见通知书")
    _validate_file_suffix(responseFile, {".pdf", ".doc", ".docx"}, "意见陈述书")
    if previousClaimsFile:
        _validate_file_suffix(previousClaimsFile, {".pdf", ".doc", ".docx"}, "上一版权利要求书")
    if currentClaimsFile:
        _validate_file_suffix(currentClaimsFile, {".pdf", ".doc", ".docx"}, "当前最新权利要求书")
    for doc in comparisonDocs or []:
        _validate_file_suffix(doc, {".pdf", ".doc", ".docx"}, "对比文件")

    task = task_manager.create_task(
        owner_id=current_user.user_id,
        task_type=task_type,
        pn=None,
        title=_build_task_title(task_type, filename=officeActionFile.filename),
    )
    emit_system_log(
        category="task_execution",
        event_name="task_created",
        owner_id=current_user.user_id,
        task_id=task.id,
        task_type=task_type,
        success=True,
        message="创建 AI 答复任务",
        payload={
            "office_action_file": officeActionFile.filename,
            "response_file": responseFile.filename,
            "previous_claims_file": previousClaimsFile.filename if previousClaimsFile else None,
            "current_claims_file": currentClaimsFile.filename if currentClaimsFile else None,
            "comparison_doc_count": len(comparisonDocs or []),
        },
    )

    input_files: List[Dict[str, str]] = []
    saved_paths: List[str] = []

    try:
        office_action_path = await _save_upload_file(task.id, officeActionFile, "office_action", "office_action")
        saved_paths.append(office_action_path)
        input_files.append(
            {
                "file_type": "office_action",
                "original_name": officeActionFile.filename or "office_action.pdf",
                "stored_path": office_action_path,
            }
        )

        response_path = await _save_upload_file(task.id, responseFile, "office_action", "response")
        saved_paths.append(response_path)
        input_files.append(
            {
                "file_type": "response",
                "original_name": responseFile.filename or "response.pdf",
                "stored_path": response_path,
            }
        )

        if previousClaimsFile:
            previous_claims_path = await _save_upload_file(task.id, previousClaimsFile, "office_action", "claims_previous")
            saved_paths.append(previous_claims_path)
            input_files.append(
                {
                    "file_type": "claims_previous",
                    "original_name": previousClaimsFile.filename or "claims_previous.pdf",
                    "stored_path": previous_claims_path,
                }
            )

        if currentClaimsFile:
            current_claims_path = await _save_upload_file(task.id, currentClaimsFile, "office_action", "claims_current")
            saved_paths.append(current_claims_path)
            input_files.append(
                {
                    "file_type": "claims_current",
                    "original_name": currentClaimsFile.filename or "claims_current.pdf",
                    "stored_path": current_claims_path,
                }
            )

        for index, doc in enumerate(comparisonDocs or []):
            doc_path = await _save_upload_file(task.id, doc, "office_action", f"comparison_{index + 1}")
            saved_paths.append(doc_path)
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

        _enqueue_pipeline_task(task, input_files=input_files)

        return TaskResponse(
            taskId=task.id,
            status="pending",
            message="AI 答复任务已创建并开始处理。",
        )
    except HTTPException as exc:
        _best_effort_fail_task(task.id, f"任务创建失败：{exc.detail}")
        for path in saved_paths:
            _cleanup_path(path)
        raise
    except Exception as exc:
        _best_effort_fail_task(task.id, f"任务创建失败：{str(exc)}")
        for path in saved_paths:
            _cleanup_path(path)
        raise HTTPException(status_code=500, detail="任务创建失败，请稍后重试。") from exc


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str, current_user: CurrentUser = Depends(_get_current_user)):
    task = _get_owned_task(task_id, current_user.user_id)
    return _task_to_response(task)


@router.post("/api/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(task_id: str, current_user: CurrentUser = Depends(_get_current_user)):
    task = _get_owned_task(task_id, current_user.user_id)
    task_type = _task_type(task)
    status = _task_status_value(task)
    emit_system_log(
        category="task_execution",
        event_name="task_cancel_requested",
        owner_id=current_user.user_id,
        task_id=task_id,
        task_type=task_type,
        success=status in {"pending", "processing", "cancelled"},
        message="请求取消任务",
        payload={"status": status},
    )
    if status == "cancelled":
        return TaskResponse(taskId=task_id, status="cancelled", message="任务已取消。")
    if status not in {"pending", "processing"}:
        raise HTTPException(status_code=409, detail="仅进行中的任务支持取消。")

    runtime = RUNNING_TASKS.get(task_id)
    if runtime:
        runtime.set()
    task_manager.cancel_task(task_id, "任务已取消")
    return TaskResponse(taskId=task_id, status="cancelled", message="任务已取消。")


@router.post("/api/tasks/{task_id}/retry", response_model=TaskResponse)
async def retry_task(task_id: str, current_user: CurrentUser = Depends(_get_current_user)):
    source_task = _get_owned_task(task_id, current_user.user_id)
    task_type = _task_type(source_task)
    status = _task_status_value(source_task)
    emit_system_log(
        category="task_execution",
        event_name="task_retry_requested",
        owner_id=current_user.user_id,
        task_id=task_id,
        task_type=task_type,
        success=_is_retryable_task(source_task),
        message="请求重试任务",
        payload={"status": status},
    )
    if not _is_retryable_task(source_task):
        raise HTTPException(status_code=409, detail="仅失败或已取消的任务支持重试。")

    _enforce_daily_quota(current_user.user_id, task_type=task_type)

    retry_task_entity: Any = None
    try:
        retry_task_entity, upload_file_path, input_sha256, input_files = _prepare_retry_task(
            source_task,
            current_user=current_user,
        )
        _enqueue_pipeline_task(
            retry_task_entity,
            upload_file_path=upload_file_path,
            input_sha256=input_sha256,
            input_files=input_files,
        )
        emit_system_log(
            category="task_execution",
            event_name="task_retry_created",
            owner_id=current_user.user_id,
            task_id=retry_task_entity.id,
            task_type=task_type,
            success=True,
            message="重试任务已创建",
            payload={"retry_of": task_id},
        )
        return TaskResponse(
            taskId=retry_task_entity.id,
            status="pending",
            message="重试任务已创建并开始处理。",
        )
    except HTTPException:
        if retry_task_entity is not None:
            _cleanup_path(retry_task_entity.output_dir)
            _cleanup_path(settings.UPLOAD_DIR / retry_task_entity.id)
            task_manager.delete_task(retry_task_entity.id)
        raise
    except Exception as exc:
        if retry_task_entity is not None:
            _cleanup_path(retry_task_entity.output_dir)
            _cleanup_path(settings.UPLOAD_DIR / retry_task_entity.id)
            task_manager.delete_task(retry_task_entity.id)
        raise HTTPException(status_code=500, detail="重试任务创建失败，请稍后重试。") from exc


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, current_user: CurrentUser = Depends(_get_current_user)):
    task = _get_owned_task(task_id, current_user.user_id)
    if _is_running_task(task):
        emit_system_log(
            category="task_execution",
            event_name="task_delete_rejected_running",
            owner_id=current_user.user_id,
            task_id=task_id,
            task_type=_task_type(task),
            success=False,
            message="运行中的任务不可删除",
        )
        raise HTTPException(status_code=409, detail="请先取消任务。")

    if _task_status_value(task) == "completed":
        _cleanup_upload_only(task)
    else:
        _cleanup_task_resources(task)

    task_manager.delete_task(task_id)
    RUNNING_TASKS.pop(task_id, None)
    PATENT_CHECKPOINTERS.pop(task_id, None)
    AI_REVIEW_CHECKPOINTERS.pop(task_id, None)
    OAR_CHECKPOINTERS.pop(task_id, None)
    emit_system_log(
        category="task_execution",
        event_name="task_deleted",
        owner_id=current_user.user_id,
        task_id=task_id,
        task_type=_task_type(task),
        success=True,
        message="删除单个任务",
    )
    return {"deleted": True}


@router.delete("/api/tasks")
async def clear_tasks(current_user: CurrentUser = Depends(_get_current_user)):
    tasks = task_manager.list_tasks(owner_id=current_user.user_id, limit=1000)
    deleted = 0
    skipped_running = 0
    for task in tasks:
        if _is_running_task(task):
            skipped_running += 1
            continue

        if _task_status_value(task) == "completed":
            _cleanup_upload_only(task)
        else:
            _cleanup_task_resources(task)

        if task_manager.delete_task(task.id):
            deleted += 1
        RUNNING_TASKS.pop(task.id, None)
        PATENT_CHECKPOINTERS.pop(task.id, None)
        AI_REVIEW_CHECKPOINTERS.pop(task.id, None)
        OAR_CHECKPOINTERS.pop(task.id, None)

    emit_system_log(
        category="task_execution",
        event_name="task_bulk_deleted",
        owner_id=current_user.user_id,
        success=True,
        message="批量删除任务",
        payload={"deleted": deleted, "skipped_running": skipped_running},
    )
    return {"deleted": deleted, "skipped_running": skipped_running}


@router.get("/api/tasks/{task_id}/download")
async def download_result(task_id: str, current_user: CurrentUser = Depends(_get_current_user)):
    task = _get_owned_task(task_id, current_user.user_id)

    if task.status.value != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成。")

    task_type = _task_type(task)
    output_files = task.metadata.get("output_files", {}) if task.metadata else {}
    filename = _build_task_download_filename(task_type, task)

    if task_type == TaskType.AI_SEARCH.value:
        bundle_path_text = str(output_files.get("bundle_zip") or "").strip()
        bundle_path = Path(bundle_path_text) if bundle_path_text else Path(task.output_dir or settings.OUTPUT_DIR / task_id) / "ai_search_result_bundle.zip"
        if not bundle_path.exists():
            return JSONResponse(
                status_code=404,
                content={
                    "error": "检索结果文件不存在",
                    "message": f"未找到检索结果文件：{bundle_path}",
                    "task_id": task_id,
                    "suggestion": "请稍后重试或联系管理员。",
                },
            )
        emit_system_log(
            category="task_execution",
            event_name="task_download",
            owner_id=current_user.user_id,
            task_id=task_id,
            task_type=task_type,
            success=True,
            message="下载 AI 检索结果",
            payload={"filename": filename},
        )
        return FileResponse(
            path=str(bundle_path),
            filename=filename,
            media_type="application/zip",
        )

    r2_storage = _build_r2_storage()
    r2_key = _build_task_pdf_r2_key(task_type, task.pn, r2_storage)
    if r2_key and r2_storage.enabled:
        r2_pdf = await asyncio.to_thread(r2_storage.get_bytes, r2_key)
        if r2_pdf:
            from io import BytesIO
            from urllib.parse import quote

            emit_system_log(
                category="task_execution",
                event_name="task_download",
                owner_id=current_user.user_id,
                task_id=task_id,
                task_type=task_type,
                success=True,
                message="下载任务报告（R2）",
                payload={"filename": filename},
            )
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
    elif task_type == TaskType.AI_REPLY.value:
        pdf_path = Path(task.output_dir or settings.OUTPUT_DIR / task_id) / "final_report.pdf"
    else:
        artifact_name = task.pn or task_id
        pdf_path = Path(task.output_dir or settings.OUTPUT_DIR / task_id) / f"{artifact_name}.pdf"

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

    emit_system_log(
        category="task_execution",
        event_name="task_download",
        owner_id=current_user.user_id,
        task_id=task_id,
        task_type=task_type,
        success=True,
        message="下载任务报告",
        payload={"filename": filename},
    )

    return FileResponse(
        path=str(pdf_path),
        filename=filename,
        media_type="application/pdf",
    )
