"""Compatibility entrypoint for storage protocols."""

from .protocols import (
    AiSearchStorage,
    SystemLogsStorage,
    TaskDomainStorage,
    TaskStorage,
    UsageStorage,
    UserStorage,
)

__all__ = [
    "AiSearchStorage",
    "SystemLogsStorage",
    "TaskDomainStorage",
    "TaskStorage",
    "UsageStorage",
    "UserStorage",
]
