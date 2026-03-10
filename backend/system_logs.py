"""
Unified system logging helpers.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import gzip
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from fastapi import Request
from loguru import logger

from config import settings


SYSTEM_LOG_RETENTION_DAYS = int(os.getenv("SYSTEM_LOG_RETENTION_DAYS", "14"))
SYSTEM_LOG_CLEANUP_INTERVAL_SECONDS = int(os.getenv("SYSTEM_LOG_CLEANUP_INTERVAL_SECONDS", "3600"))
SYSTEM_LOG_MAX_INLINE_PAYLOAD_BYTES = int(os.getenv("SYSTEM_LOG_MAX_INLINE_PAYLOAD_BYTES", str(512 * 1024)))
SYSTEM_LOG_DB_ENABLED = str(os.getenv("SYSTEM_LOG_DB_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
SYSTEM_LOG_DIR = Path(os.getenv("SYSTEM_LOG_DIR", str(settings.DATA_DIR / "logs")))
SYSTEM_LOG_FILE = SYSTEM_LOG_DIR / "system_events.log"
SYSTEM_LOG_PAYLOAD_DIR = Path(os.getenv("SYSTEM_LOG_PAYLOAD_DIR", str(SYSTEM_LOG_DIR / "payloads")))
REDACTED_VALUE = "***REDACTED***"


_SENSITIVE_KEY_RE = re.compile(
    r"(authorization|api[-_]?key|token|secret|password|cookie|set-cookie|access[-_]?key|private[-_]?key)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._\-~+/]+=*", re.IGNORECASE)
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9]{10,}\b")


_REQUEST_CONTEXT: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar(
    "SYSTEM_LOG_REQUEST_CONTEXT",
    default={},
)
_INTERNAL_LOG_WRITE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "SYSTEM_LOG_INTERNAL_WRITE",
    default=False,
)

_FILE_WRITE_LOCK = threading.Lock()
_PATCH_LOCK = threading.Lock()
_STORAGE_LOCK = threading.Lock()
_REQUESTS_PATCHED = False
_ORIGINAL_SESSION_REQUEST = None
_STORAGE_REF = None
_CLEANUP_TASK: Optional[asyncio.Task] = None


def _json_default(value: Any) -> str:
    return str(value)


def _safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _sanitize_text(text: str) -> str:
    if not text:
        return text
    redacted = _BEARER_RE.sub("Bearer ***", text)
    redacted = _OPENAI_KEY_RE.sub(REDACTED_VALUE, redacted)
    return redacted


def _is_sensitive_key(key: Any) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(key or "")))


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        output: Dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                output[str(key)] = REDACTED_VALUE
            else:
                output[str(key)] = redact_sensitive(item)
        return output
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _sanitize_query(url: str) -> str:
    try:
        parts = urlsplit(url)
    except Exception:
        return _sanitize_text(url)

    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if _is_sensitive_key(key):
            query_items.append((key, REDACTED_VALUE))
        else:
            query_items.append((key, _sanitize_text(value)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_items), parts.fragment))


def _provider_from_host(host: str) -> str:
    host_text = str(host or "").strip().lower()
    if not host_text:
        return "unknown"
    if "openai" in host_text or "deepseek" in host_text or "bigmodel" in host_text:
        return "llm"
    if "zhihuiya" in host_text:
        return "zhihuiya"
    if "mineru" in host_text:
        return "mineru"
    if "authing" in host_text:
        return "authing"
    if "cloudflare" in host_text:
        return "cloudflare"
    if "openalex" in host_text:
        return "openalex"
    if "tavily" in host_text:
        return "tavily"
    return host_text


def _get_storage():
    global _STORAGE_REF
    if _STORAGE_REF is None:
        with _STORAGE_LOCK:
            if _STORAGE_REF is None:
                from backend.storage import get_pipeline_manager

                _STORAGE_REF = get_pipeline_manager().storage
    return _STORAGE_REF


@contextlib.contextmanager
def internal_log_write_context():
    token = _INTERNAL_LOG_WRITE.set(True)
    try:
        yield
    finally:
        _INTERNAL_LOG_WRITE.reset(token)


def is_internal_log_write() -> bool:
    return bool(_INTERNAL_LOG_WRITE.get())


def get_request_context() -> Dict[str, Any]:
    data = _REQUEST_CONTEXT.get()
    return dict(data) if isinstance(data, dict) else {}


def bind_request_context(**kwargs):
    context = get_request_context()
    context.update({key: value for key, value in kwargs.items() if value is not None})
    return _REQUEST_CONTEXT.set(context)


def reset_request_context(token):
    _REQUEST_CONTEXT.reset(token)


def _payload_file_path(log_id: str, timestamp: str) -> Path:
    day = (timestamp or datetime.now().isoformat())[:10].replace("-", "")
    return SYSTEM_LOG_PAYLOAD_DIR / day / f"{log_id}.json.gz"


def _persist_payload(payload: Any, log_id: str, timestamp: str) -> Dict[str, Any]:
    payload_json = _safe_json_dumps(payload)
    payload_bytes = len(payload_json.encode("utf-8"))

    if payload_bytes <= SYSTEM_LOG_MAX_INLINE_PAYLOAD_BYTES:
        return {
            "payload_inline_json": payload_json,
            "payload_file_path": None,
            "payload_bytes": payload_bytes,
            "payload_overflow": False,
        }

    path = _payload_file_path(log_id, timestamp)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(payload_json)

    return {
        "payload_inline_json": None,
        "payload_file_path": str(path),
        "payload_bytes": payload_bytes,
        "payload_overflow": True,
    }


def resolve_payload_from_record(record: Dict[str, Any]) -> Any:
    inline_payload = record.get("payload_inline_json")
    if inline_payload:
        try:
            return json.loads(str(inline_payload))
        except Exception:
            return str(inline_payload)

    file_path = str(record.get("payload_file_path") or "").strip()
    if not file_path:
        return None

    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return None

    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None


def _append_system_log_file(record: Dict[str, Any]) -> None:
    SYSTEM_LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = _safe_json_dumps(record)
    with _FILE_WRITE_LOCK:
        with open(SYSTEM_LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")


def emit_system_log(
    *,
    category: str,
    event_name: str,
    level: str = "INFO",
    owner_id: Optional[str] = None,
    task_id: Optional[str] = None,
    task_type: Optional[str] = None,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
    status_code: Optional[int] = None,
    duration_ms: Optional[int] = None,
    provider: Optional[str] = None,
    target_host: Optional[str] = None,
    success: bool = True,
    message: Optional[str] = None,
    payload: Optional[Any] = None,
) -> str:
    now = datetime.now().isoformat()
    log_id = uuid.uuid4().hex
    context = get_request_context()

    safe_payload = redact_sensitive(payload or {})
    payload_record = _persist_payload(safe_payload, log_id, now)

    db_record: Dict[str, Any] = {
        "log_id": log_id,
        "timestamp": now,
        "category": str(category or "").strip() or "system",
        "event_name": str(event_name or "").strip() or "event",
        "level": str(level or "INFO").strip().upper(),
        "owner_id": str(owner_id or context.get("owner_id") or "").strip() or None,
        "task_id": str(task_id or context.get("task_id") or "").strip() or None,
        "task_type": str(task_type or context.get("task_type") or "").strip() or None,
        "request_id": str(request_id or context.get("request_id") or "").strip() or None,
        "trace_id": str(trace_id or context.get("trace_id") or "").strip() or None,
        "method": str(method or context.get("method") or "").strip() or None,
        "path": str(path or context.get("path") or "").strip() or None,
        "status_code": int(status_code) if status_code is not None else None,
        "duration_ms": int(duration_ms) if duration_ms is not None else None,
        "provider": str(provider or "").strip() or None,
        "target_host": str(target_host or "").strip() or None,
        "success": 1 if success else 0,
        "message": _sanitize_text(str(message or "")) or None,
        "payload_inline_json": payload_record["payload_inline_json"],
        "payload_file_path": payload_record["payload_file_path"],
        "payload_bytes": int(payload_record["payload_bytes"]),
        "payload_overflow": 1 if payload_record["payload_overflow"] else 0,
        "created_at": now,
    }

    file_record = db_record.copy()
    file_record["payload"] = safe_payload

    try:
        _append_system_log_file(file_record)
    except Exception as exc:
        logger.warning(f"[SystemLog] append file failed: {exc}")

    try:
        logger.bind(system_event=True).log(
            db_record["level"],
            f"[SystemLog] {db_record['category']}.{db_record['event_name']} success={success}",
        )
    except Exception:
        logger.info(f"[SystemLog] {db_record['category']}.{db_record['event_name']} success={success}")

    if SYSTEM_LOG_DB_ENABLED:
        try:
            storage = _get_storage()
            if hasattr(storage, "insert_system_log"):
                with internal_log_write_context():
                    storage.insert_system_log(db_record)
        except Exception as exc:
            logger.warning(f"[SystemLog] persist db failed: {exc}")

    return log_id


def _extract_safe_request_body(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if "json" in kwargs:
        payload["json"] = kwargs.get("json")
    if "data" in kwargs:
        data_value = kwargs.get("data")
        if isinstance(data_value, (str, dict, list, tuple)):
            payload["data"] = data_value
        elif data_value is not None:
            payload["data"] = f"<{type(data_value).__name__}>"
    if "timeout" in kwargs:
        payload["timeout"] = kwargs.get("timeout")
    headers = kwargs.get("headers")
    if isinstance(headers, dict):
        payload["headers"] = headers
    return payload


def instrument_requests() -> None:
    global _REQUESTS_PATCHED
    global _ORIGINAL_SESSION_REQUEST

    if _REQUESTS_PATCHED:
        return

    with _PATCH_LOCK:
        if _REQUESTS_PATCHED:
            return

        _ORIGINAL_SESSION_REQUEST = requests.sessions.Session.request

        def _wrapped_request(session, method, url, *args, **kwargs):
            if is_internal_log_write():
                return _ORIGINAL_SESSION_REQUEST(session, method, url, *args, **kwargs)

            started_at = time.perf_counter()
            parsed = urlsplit(str(url or ""))
            host = parsed.netloc
            provider = _provider_from_host(host)
            sanitized_url = _sanitize_query(str(url or ""))
            request_payload = _extract_safe_request_body(kwargs)

            try:
                response = _ORIGINAL_SESSION_REQUEST(session, method, url, *args, **kwargs)
                duration_ms = int((time.perf_counter() - started_at) * 1000)
                response_size = int(response.headers.get("content-length") or 0)
                emit_system_log(
                    category="external_api",
                    event_name="requests_call",
                    level="INFO" if response.ok else "WARNING",
                    method=str(method or "").upper(),
                    path=parsed.path or "/",
                    status_code=int(response.status_code),
                    duration_ms=duration_ms,
                    provider=provider,
                    target_host=host or None,
                    success=bool(response.ok),
                    message=f"{method} {sanitized_url}",
                    payload={
                        "request_url": sanitized_url,
                        "request": request_payload,
                        "response": {
                            "status_code": int(response.status_code),
                            "reason": response.reason,
                            "ok": bool(response.ok),
                            "response_size": response_size,
                        },
                    },
                )
                return response
            except Exception as exc:
                duration_ms = int((time.perf_counter() - started_at) * 1000)
                emit_system_log(
                    category="external_api",
                    event_name="requests_call_exception",
                    level="ERROR",
                    method=str(method or "").upper(),
                    path=parsed.path or "/",
                    duration_ms=duration_ms,
                    provider=provider,
                    target_host=host or None,
                    success=False,
                    message=f"{type(exc).__name__}: {exc}",
                    payload={
                        "request_url": sanitized_url,
                        "request": request_payload,
                        "error": {
                            "type": type(exc).__name__,
                            "message": str(exc),
                        },
                    },
                )
                raise

        requests.sessions.Session.request = _wrapped_request
        _REQUESTS_PATCHED = True


def _extract_owner_id_from_request(request: Request) -> Optional[str]:
    try:
        from backend.auth import _extract_token_from_request, _verify_token

        authorization = request.headers.get("authorization")
        token = request.query_params.get("token")
        raw_token = _extract_token_from_request(authorization, token)
        if not raw_token:
            return None
        payload = _verify_token(raw_token)
        if not payload:
            return None
        owner_id = str(payload.get("uid") or "").strip()
        return owner_id or None
    except Exception:
        return None


async def request_logging_middleware(request: Request, call_next):
    request_id = uuid.uuid4().hex[:16]
    owner_id = _extract_owner_id_from_request(request)
    path = request.url.path
    method = request.method.upper()
    query = {key: value for key, value in request.query_params.multi_items()}

    token = bind_request_context(
        request_id=request_id,
        owner_id=owner_id,
        method=method,
        path=path,
    )

    request.state.request_id = request_id

    started_at = time.perf_counter()
    response = None
    error_text: Optional[str] = None
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        status_code = int(getattr(response, "status_code", 500))
        level = "INFO"
        if error_text or status_code >= 500:
            level = "ERROR"
        elif status_code >= 400:
            level = "WARNING"

        should_log = path.startswith("/api/")
        if should_log:
            emit_system_log(
                category="user_action",
                event_name="http_request",
                level=level,
                owner_id=owner_id,
                request_id=request_id,
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
                success=error_text is None and status_code < 400,
                message=error_text or "ok",
                payload={
                    "query": query,
                    "client_ip": request.client.host if request.client else None,
                    "user_agent": request.headers.get("user-agent"),
                },
            )

        if response is not None:
            response.headers["X-Request-Id"] = request_id

        reset_request_context(token)


def cleanup_expired_system_logs(retention_days: Optional[int] = None) -> Dict[str, int]:
    days = int(retention_days or SYSTEM_LOG_RETENTION_DAYS)
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()

    deleted_db = 0
    storage = None
    try:
        storage = _get_storage()
    except Exception:
        storage = None

    if storage and hasattr(storage, "cleanup_system_logs_before"):
        try:
            with internal_log_write_context():
                deleted_db = int(storage.cleanup_system_logs_before(cutoff_iso) or 0)
        except Exception as exc:
            logger.warning(f"[SystemLog] cleanup db failed: {exc}")

    deleted_files = 0
    if SYSTEM_LOG_PAYLOAD_DIR.exists():
        for file in SYSTEM_LOG_PAYLOAD_DIR.rglob("*.json.gz"):
            try:
                if datetime.fromtimestamp(file.stat().st_mtime) < cutoff:
                    file.unlink(missing_ok=True)
                    deleted_files += 1
            except Exception:
                continue

    return {"deleted_db": deleted_db, "deleted_payload_files": deleted_files}


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(max(60, SYSTEM_LOG_CLEANUP_INTERVAL_SECONDS))
        cleanup_expired_system_logs()


def start_system_log_cleanup_loop() -> Optional[asyncio.Task]:
    global _CLEANUP_TASK
    if _CLEANUP_TASK is not None and not _CLEANUP_TASK.done():
        return _CLEANUP_TASK
    try:
        _CLEANUP_TASK = asyncio.create_task(_cleanup_loop())
        return _CLEANUP_TASK
    except RuntimeError:
        return None


async def stop_system_log_cleanup_loop() -> None:
    global _CLEANUP_TASK
    task = _CLEANUP_TASK
    _CLEANUP_TASK = None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def initialize_system_logging() -> None:
    instrument_requests()
    SYSTEM_LOG_DIR.mkdir(parents=True, exist_ok=True)
    SYSTEM_LOG_PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
