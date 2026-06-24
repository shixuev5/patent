"""
Task storage layer - factory for initializing storage instances based on TASK_STORAGE_BACKEND.
"""

import os
import threading
import time
from pathlib import Path
from typing import Optional, Union

from loguru import logger

from .errors import StorageUnavailableError
from .interfaces import TaskStorage


_storage_instance: Optional[TaskStorage] = None
_storage_lock = threading.Lock()
_storage_init_local = threading.local()
_storage_init_failure_until = 0.0


class StorageInitializationInProgressError(RuntimeError):
    """Raised when task storage is re-entered during initialization."""


def _storage_backend_name() -> str:
    return os.getenv("TASK_STORAGE_BACKEND", "sqlite").strip().lower()


def _d1_init_failure_cooldown_seconds() -> int:
    return max(0, int(os.getenv("D1_INIT_FAILURE_COOLDOWN_SECONDS", "60") or "60"))


def _raise_if_d1_init_cooling_down(backend: str) -> None:
    if backend != "d1":
        return
    remaining_seconds = _storage_init_failure_until - time.monotonic()
    if remaining_seconds <= 0:
        return
    retry_after_seconds = max(1, int(remaining_seconds) + 1)
    raise StorageUnavailableError(
        "D1 storage initialization is cooling down after a recent failure",
        retry_after_seconds=retry_after_seconds,
    )


def get_task_storage(db_path: Optional[Union[str, Path]] = None) -> TaskStorage:
    """
    Get a task storage instance based on TASK_STORAGE_BACKEND environment variable.

    Args:
        db_path: Optional database path (only used for SQLite backend)

    Returns:
        Task storage instance
    """
    global _storage_instance, _storage_init_failure_until
    if _storage_instance is not None:
        return _storage_instance

    backend = _storage_backend_name()
    _raise_if_d1_init_cooling_down(backend)

    # Prevent same-thread re-entrancy (e.g. outbound request logging during D1 bootstrap).
    if bool(getattr(_storage_init_local, "in_progress", False)):
        raise StorageInitializationInProgressError(
            "Task storage initialization in progress"
        )

    if _storage_instance is None:
        with _storage_lock:
            if _storage_instance is None:
                backend = _storage_backend_name()
                _raise_if_d1_init_cooling_down(backend)
                _storage_init_local.in_progress = True
                try:
                    logger.info(f"初始化任务存储后端：{backend}")

                    if backend == "d1":
                        from .facade import D1TaskStorage

                        account_id = os.getenv("D1_ACCOUNT_ID", "").strip()
                        database_id = os.getenv("D1_DATABASE_ID", "").strip()
                        api_token = os.getenv("D1_API_TOKEN", "").strip()
                        api_base_url = os.getenv(
                            "D1_API_BASE_URL",
                            "https://api.cloudflare.com/client/v4",
                        ).strip()
                        timeout_seconds = int(os.getenv("D1_TIMEOUT_SECONDS", "8") or "8")
                        _storage_instance = D1TaskStorage(
                            account_id=account_id,
                            database_id=database_id,
                            api_token=api_token,
                            api_base_url=api_base_url,
                            timeout_seconds=timeout_seconds,
                        )
                    elif backend in {"", "sqlite"}:
                        from .facade import SQLiteTaskStorage

                        _storage_instance = SQLiteTaskStorage(db_path)
                    else:
                        raise ValueError(
                            f"Unsupported TASK_STORAGE_BACKEND={backend}. "
                            "Use `sqlite` or `d1`."
                        )
                except StorageUnavailableError as exc:
                    if backend == "d1":
                        cooldown_seconds = _d1_init_failure_cooldown_seconds()
                        if cooldown_seconds > 0:
                            _storage_init_failure_until = time.monotonic() + cooldown_seconds
                            logger.warning(
                                "D1 任务存储初始化失败，进入冷却：retry_after_seconds={} error={}",
                                cooldown_seconds,
                                exc,
                            )
                    raise
                finally:
                    _storage_init_local.in_progress = False
    return _storage_instance


def reset_storage_instance():
    """Reset the storage instance singleton."""
    global _storage_instance, _storage_init_failure_until
    with _storage_lock:
        _storage_instance = None
        _storage_init_failure_until = 0.0
