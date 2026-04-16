"""Storage-layer exception types."""

from __future__ import annotations

from typing import Optional


class StorageError(RuntimeError):
    """Base storage exception."""


class StorageUnavailableError(StorageError):
    """Raised when the storage backend is temporarily unavailable."""

    def __init__(self, message: str, *, retry_after_seconds: Optional[int] = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class StorageRateLimitedError(StorageUnavailableError):
    """Raised when the storage backend rate-limits requests."""

