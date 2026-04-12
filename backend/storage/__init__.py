"""
任务存储模块 - 提供任务持久化存储能力
"""
from .task_storage import get_task_storage
from .sqlite_storage import SQLiteTaskStorage
from .d1_storage import D1TaskStorage
from .models import (
    RefreshSession,
    Task,
    TaskStatus,
    TaskType,
    User,
    WeChatBinding,
    WeChatBindSession,
    WeChatDeliveryJob,
    WeChatFlowSession,
)
from .pipeline_adapter import PipelineTaskManager, get_pipeline_manager, DEFAULT_PIPELINE_STEPS

__all__ = [
    # Storage
    "SQLiteTaskStorage",
    "D1TaskStorage",
    "get_task_storage",
    # Models
    "Task",
    "TaskStatus",
    "TaskType",
    "User",
    "RefreshSession",
    "WeChatBinding",
    "WeChatBindSession",
    "WeChatFlowSession",
    "WeChatDeliveryJob",
    # Pipeline Adapter
    "PipelineTaskManager",
    "get_pipeline_manager",
    "DEFAULT_PIPELINE_STEPS",
]
