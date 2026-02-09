"""
任务存储模块 - 提供任务持久化存储能力
"""
from .task_storage import TaskStorage, get_task_storage
from .models import Task, TaskStep, TaskStatus
from .pipeline_adapter import PipelineTaskManager, get_pipeline_manager, DEFAULT_PIPELINE_STEPS

__all__ = [
    # Storage
    "TaskStorage",
    "get_task_storage",
    # Models
    "Task",
    "TaskStep",
    "TaskStatus",
    # Pipeline Adapter
    "PipelineTaskManager",
    "get_pipeline_manager",
    "DEFAULT_PIPELINE_STEPS",
]