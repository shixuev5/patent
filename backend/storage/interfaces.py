"""Compatibility entrypoint for storage protocols."""

from .protocols import (
    AiSearchStorage,
    SystemLogsStorage,
    TaskDomainStorage,
    TaskStorage,
    UsageStorage,
    UserStorage,
    WeChatStorage,
)

__all__ = [
    "AiSearchStorage",
    "SystemLogsStorage",
    "TaskDomainStorage",
    "TaskStorage",
    "UsageStorage",
    "UserStorage",
    "WeChatStorage",
]
