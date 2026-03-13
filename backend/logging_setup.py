"""
Loguru and timezone configuration helpers.
"""

import os
import re
import sys
import time
from datetime import timedelta, timezone
from typing import Any, Dict, Optional

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
