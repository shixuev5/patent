"""
Task storage layer - factory for initializing storage instances based on TASK_STORAGE_BACKEND.
"""

import os
import threading
from pathlib import Path
from typing import Any, Optional, Union

from loguru import logger


_storage_instance: Optional[Any] = None
_storage_lock = threading.Lock()


def get_task_storage(db_path: Optional[Union[str, Path]] = None) -> Any:
    """
    Get a task storage instance based on TASK_STORAGE_BACKEND environment variable.

    Args:
        db_path: Optional database path (only used for SQLite backend)

    Returns:
        Task storage instance
    """
    global _storage_instance
    if _storage_instance is None:
        with _storage_lock:
            if _storage_instance is None:
                backend = os.getenv("TASK_STORAGE_BACKEND", "sqlite").strip().lower()
                logger.info(f"Initializing task storage backend: {backend}")

                if backend == "d1":
                    from .d1_storage import D1TaskStorage

                    account_id = os.getenv("D1_ACCOUNT_ID", "").strip()
                    database_id = os.getenv("D1_DATABASE_ID", "").strip()
                    api_token = os.getenv("D1_API_TOKEN", "").strip()
                    api_base_url = os.getenv(
                        "D1_API_BASE_URL",
                        "https://api.cloudflare.com/client/v4",
                    ).strip()
                    _storage_instance = D1TaskStorage(
                        account_id=account_id,
                        database_id=database_id,
                        api_token=api_token,
                        api_base_url=api_base_url,
                    )
                elif backend in {"", "sqlite"}:
                    from .sqlite_storage import SQLiteTaskStorage

                    _storage_instance = SQLiteTaskStorage(db_path)
                else:
                    raise ValueError(
                        f"Unsupported TASK_STORAGE_BACKEND={backend}. "
                        "Use `sqlite` or `d1`."
                    )
    return _storage_instance


def reset_storage_instance():
    """Reset the storage instance singleton."""
    global _storage_instance
    with _storage_lock:
        _storage_instance = None
