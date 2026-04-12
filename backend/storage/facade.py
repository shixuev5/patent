"""Unified storage facade assembled from repository mixins."""

from .backends import D1Backend, SQLiteBackend
from .codecs import StorageCodecsMixin
from .repositories import (
    AiSearchRepositoryMixin,
    SystemLogsRepositoryMixin,
    TaskRepositoryMixin,
    UsageRepositoryMixin,
    UserRepositoryMixin,
    WeChatRepositoryMixin,
)


class TaskStorageFacade(
    StorageCodecsMixin,
    UsageRepositoryMixin,
    SystemLogsRepositoryMixin,
    UserRepositoryMixin,
    WeChatRepositoryMixin,
    TaskRepositoryMixin,
    AiSearchRepositoryMixin,
):
    """Concrete storage behavior mixed into backend executors."""

    pass


class SQLiteTaskStorage(SQLiteBackend, TaskStorageFacade):
    """SQLite storage entrypoint."""

    pass


class D1TaskStorage(D1Backend, TaskStorageFacade):
    """Cloudflare D1 storage entrypoint."""

    pass
