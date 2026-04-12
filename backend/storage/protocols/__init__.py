"""Storage protocols grouped by subdomain."""

from .ai_search import AiSearchStorage
from .system_logs import SystemLogsStorage
from .tasks import TaskDomainStorage
from .usage import UsageStorage
from .users import UserStorage
from .wechat import WeChatStorage


class TaskStorage(
    TaskDomainStorage,
    UserStorage,
    WeChatStorage,
    UsageStorage,
    SystemLogsStorage,
    AiSearchStorage,
):
    """Complete storage protocol composed from subdomain protocols."""

    pass


__all__ = [
    "AiSearchStorage",
    "SystemLogsStorage",
    "TaskDomainStorage",
    "TaskStorage",
    "UsageStorage",
    "UserStorage",
    "WeChatStorage",
]
