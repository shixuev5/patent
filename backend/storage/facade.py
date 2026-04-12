"""Unified storage facade assembled from repository mixins."""

from .backends.d1_backend import D1Backend
from .backends.sqlite_backend import SQLiteBackend
from .codecs import StorageCodecsMixin
from .repositories.ai_search import AiSearchRepositoryMixin
from .repositories.system_logs import SystemLogsRepositoryMixin
from .repositories.tasks import TaskRepositoryMixin
from .repositories.usage import UsageRepositoryMixin
from .repositories.users import UserRepositoryMixin
from .repositories.wechat import WeChatRepositoryMixin


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
