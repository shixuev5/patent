"""
Loguru and timezone configuration helpers.
"""

import logging
import os
import re
import sys
import time
from datetime import timedelta, timezone
from typing import Any, Dict, Optional, Sequence

from loguru import logger


UTC_PLUS_8 = timezone(timedelta(hours=8))
_LEADING_BRACKET_PREFIX_RE = re.compile(r"^\[[^\]]+\]\s*")
CONSOLE_LOG_FORMAT = (
    "<green>{extra[time_utc8]}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{extra[task_id]}</cyan> | "
    "<cyan>{extra[task_type_label]}</cyan> | "
    "<cyan>{extra[pn]}</cyan> | "
    "<cyan>{extra[stage]}</cyan> | "
    "<level>{message}</level>"
)
FILE_LOG_FORMAT = (
    "{extra[time_utc8]} | {level: <8} | {extra[task_id]} | "
    "{extra[task_type_label]} | {extra[pn]} | {extra[stage]} | {message}"
)
QUIET_UVICORN_ACCESS_PATHS = frozenset((
    "/api/health",
    "/api/account/wechat-integration",
    "/api/internal/wechat/delivery-jobs/claim",
    "/api/internal/wechat/gateway/login-state",
))
QUIET_UVICORN_ACCESS_PATH_PREFIXES = (
    "/api/account/wechat-integration/bind-session/",
)


def configure_process_timezone_to_utc8() -> None:
    """
    Configure process local timezone to UTC+8.
    This affects stdlib logging/uvicorn timestamps that rely on localtime().
    """
    os.environ["TZ"] = os.getenv("APP_TIMEZONE", "Asia/Shanghai")
    if hasattr(time, "tzset"):
        time.tzset()


def _inject_utc8_time(record: Dict[str, Any]) -> None:
    extra = record["extra"]
    extra["time_utc8"] = record["time"].astimezone(UTC_PLUS_8).strftime("%Y-%m-%d %H:%M:%S")
    extra.setdefault("task_id", "-")
    extra.setdefault("task_type_label", "-")
    extra.setdefault("pn", "-")
    extra.setdefault("stage", record.get("module", "-") or "-")
    _normalize_agent_log_message(record)


def _agent_component_from_name(name: str) -> str:
    """
    Build a short and stable component tag from module path.
    e.g. agents.ai_reply.src.nodes.document_processing
      -> ai_reply.document_processing
    """
    parts = (name or "").split(".")
    if len(parts) < 2 or parts[0] != "agents":
        return name or "agent"

    domain = parts[1]
    if "nodes" in parts and parts[-1]:
        return f"{domain}.{parts[-1]}"
    if len(parts) >= 3 and parts[2] == "src" and parts[-1]:
        return f"{domain}.{parts[-1]}"
    if parts[-1]:
        return f"{domain}.{parts[-1]}"
    return domain


def _normalize_agent_log_message(record: Dict[str, Any]) -> None:
    """
    Keep all logs emitted from agents.* modules in a single style:
    [component] message
    """
    name = str(record.get("name", "")).strip()
    if not name.startswith("agents."):
        return

    message = str(record.get("message", "")).strip()
    message = _LEADING_BRACKET_PREFIX_RE.sub("", message)
    component = _agent_component_from_name(name)
    record["message"] = f"[{component}] {message}" if message else f"[{component}]"


def configure_loguru_to_utc8(
    level: str = "INFO",
    sink: Any = sys.stderr,
    colorize: bool = True,
    fmt: str = CONSOLE_LOG_FORMAT,
) -> None:
    """Configure process-wide loguru output with UTC+8 timestamps."""
    logger.remove()
    logger.configure(patcher=_inject_utc8_time)
    logger.add(
        sink,
        level=level,
        colorize=colorize,
        format=fmt,
    )


def add_loguru_file_sink(
    log_file: str,
    level: str = "DEBUG",
    rotation: str = "10 MB",
    retention: str = "14 days",
    compression: str = "zip",
    fmt: str = FILE_LOG_FORMAT,
) -> None:
    """Attach an additional file sink using the same UTC+8 timestamp field."""
    logger.add(
        log_file,
        level=level,
        format=fmt,
        rotation=rotation,
        retention=retention,
        compression=compression,
    )


def setup_logging_utc8(
    level: str = "INFO",
    log_file: Optional[str] = None,
    file_level: str = "DEBUG",
    rotation: str = "10 MB",
    retention: str = "14 days",
    compression: str = "zip",
) -> None:
    """Unified logging setup: process timezone + loguru console + optional file sink."""
    configure_process_timezone_to_utc8()
    configure_loguru_to_utc8(level=level)
    if log_file:
        add_loguru_file_sink(
            log_file,
            level=file_level,
            rotation=rotation,
            retention=retention,
            compression=compression,
        )


def should_suppress_uvicorn_access_log(record: logging.LogRecord, quiet_paths: Optional[Sequence[str]] = None) -> bool:
    if str(getattr(record, "name", "") or "").strip() != "uvicorn.access":
        return False

    args = getattr(record, "args", ())
    if not isinstance(args, tuple) or len(args) < 5:
        return False

    full_path = str(args[2] or "").strip()
    path = full_path.split("?", 1)[0]
    try:
        status_code = int(args[4])
    except Exception:
        return False

    if status_code < 200 or status_code >= 300:
        return False
    normalized_quiet_paths = set(quiet_paths or QUIET_UVICORN_ACCESS_PATHS)
    if path in normalized_quiet_paths:
        return True
    return any(path.startswith(prefix) for prefix in QUIET_UVICORN_ACCESS_PATH_PREFIXES)


class QuietUvicornAccessFilter(logging.Filter):
    def __init__(self, quiet_paths: Optional[Sequence[str]] = None):
        super().__init__()
        self.quiet_paths = tuple(quiet_paths or QUIET_UVICORN_ACCESS_PATHS)

    def filter(self, record: logging.LogRecord) -> bool:
        return not should_suppress_uvicorn_access_log(record, self.quiet_paths)


def configure_uvicorn_access_log_filter(quiet_paths: Optional[Sequence[str]] = None) -> None:
    access_logger = logging.getLogger("uvicorn.access")
    normalized_paths = tuple(quiet_paths or QUIET_UVICORN_ACCESS_PATHS)
    for existing in access_logger.filters:
        if isinstance(existing, QuietUvicornAccessFilter) and tuple(existing.quiet_paths) == normalized_paths:
            return
    access_logger.addFilter(QuietUvicornAccessFilter(normalized_paths))
