"""
任务存储模块 - 提供任务持久化存储能力
"""
from .facade import D1TaskStorage, SQLiteTaskStorage
from .interfaces import TaskStorage
from .errors import StorageError, StorageRateLimitedError, StorageUnavailableError
from .models import (
    RefreshSession,
    Task,
    TaskStatus,
    TaskType,
    User,
    WeChatBinding,
    WeChatConversationSession,
    WeChatDeliveryJob,
    WeChatFlowSession,
    WeChatLoginSession,
)
from .pipeline_adapter import PipelineTaskManager, get_pipeline_manager, DEFAULT_PIPELINE_STEPS
from .task_storage import get_task_storage

__all__ = [
    # Storage
    "SQLiteTaskStorage",
    "D1TaskStorage",
    "TaskStorage",
    "get_task_storage",
    "StorageError",
    "StorageUnavailableError",
    "StorageRateLimitedError",
    # Models
    "Task",
    "TaskStatus",
    "TaskType",
    "User",
    "RefreshSession",
    "WeChatBinding",
    "WeChatLoginSession",
    "WeChatConversationSession",
    "WeChatFlowSession",
    "WeChatDeliveryJob",
    # Pipeline Adapter
    "PipelineTaskManager",
    "get_pipeline_manager",
    "DEFAULT_PIPELINE_STEPS",
]
