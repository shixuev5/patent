"""
Loguru and timezone configuration helpers.
"""

import os
import sys
import time
from datetime import timedelta, timezone
from typing import Any, Dict, Optional

from loguru import logger


UTC_PLUS_8 = timezone(timedelta(hours=8))
CONSOLE_LOG_FORMAT = (
    "<green>{extra[time_utc8]}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)
FILE_LOG_FORMAT = "{extra[time_utc8]} | {level: <8} | {name}:{function}:{line} - {message}"


def configure_process_timezone_to_utc8() -> None:
    """
    Configure process local timezone to UTC+8.
    This affects stdlib logging/uvicorn timestamps that rely on localtime().
    """
    os.environ["TZ"] = os.getenv("APP_TIMEZONE", "Asia/Shanghai")
    if hasattr(time, "tzset"):
        time.tzset()


def _inject_utc8_time(record: Dict[str, Any]) -> None:
    record["extra"]["time_utc8"] = record["time"].astimezone(UTC_PLUS_8).strftime("%Y-%m-%d %H:%M:%S")


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
    fmt: str = FILE_LOG_FORMAT,
) -> None:
    """Attach an additional file sink using the same UTC+8 timestamp field."""
    logger.add(
        log_file,
        level=level,
        format=fmt,
        rotation=rotation,
    )


def setup_logging_utc8(
    level: str = "INFO",
    log_file: Optional[str] = None,
    file_level: str = "DEBUG",
    rotation: str = "10 MB",
) -> None:
    """Unified logging setup: process timezone + loguru console + optional file sink."""
    configure_process_timezone_to_utc8()
    configure_loguru_to_utc8(level=level)
    if log_file:
        add_loguru_file_sink(log_file, level=file_level, rotation=rotation)
