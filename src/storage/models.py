"""
任务数据模型定义
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
import json


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"           # 等待处理
    PROCESSING = "processing"     # 处理中
    PAUSED = "paused"            # 已暂停
    COMPLETED = "completed"      # 已完成
    FAILED = "failed"             # 失败
    CANCELLED = "cancelled"      # 已取消


@dataclass
class TaskStep:
    """任务步骤/阶段记录"""
    step_name: str                    # 步骤名称
    step_order: int                   # 步骤顺序
    status: str = "pending"           # 步骤状态: pending/running/completed/failed
    progress: int = 0                 # 该步骤的进度 (0-100)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "step_name": self.step_name,
            "step_order": self.step_order,
            "status": self.status,
            "progress": self.progress,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "error_message": self.error_message,
            "metadata": json.dumps(self.metadata, ensure_ascii=False) if self.metadata else None,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "TaskStep":
        """从字典创建"""
        return cls(
            step_name=data["step_name"],
            step_order=data["step_order"],
            status=data.get("status", "pending"),
            progress=data.get("progress", 0),
            start_time=datetime.fromisoformat(data["start_time"]) if data.get("start_time") else None,
            end_time=datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None,
            error_message=data.get("error_message"),
            metadata=json.loads(data["metadata"]) if data.get("metadata") else {},
        )


@dataclass
class Task:
    """任务数据模型"""
    # 基本信息
    id: str                           # 任务ID (唯一标识)
    pn: Optional[str] = None            # 专利号 (Patent Number)
    title: Optional[str] = None         # 任务标题/描述

    # 状态信息
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0                 # 整体进度 (0-100)
    current_step: Optional[str] = None  # 当前步骤名称

    # 文件路径
    output_dir: Optional[str] = None    # 输出目录路径
    raw_pdf_path: Optional[str] = None  # 原始PDF路径

    # 错误信息
    error_message: Optional[str] = None

    # 时间戳
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None

    # 关联数据 (不直接存储到task表)
    steps: List[TaskStep] = field(default_factory=list)  # 任务步骤列表

    # 扩展元数据
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据

    def to_dict(self) -> Dict:
        """转换为字典 (用于JSON序列化)"""
        return {
            "id": self.id,
            "pn": self.pn,
            "title": self.title,
            "status": self.status.value,
            "progress": self.progress,
            "current_step": self.current_step,
            "output_dir": self.output_dir,
            "raw_pdf_path": self.raw_pdf_path,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Task":
        """从字典创建实例"""
        return cls(
            id=data["id"],
            pn=data.get("pn"),
            title=data.get("title"),
            status=TaskStatus(data.get("status", "pending")),
            progress=data.get("progress", 0),
            current_step=data.get("current_step"),
            output_dir=data.get("output_dir"),
            raw_pdf_path=data.get("raw_pdf_path"),
            error_message=data.get("error_message"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(),
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            metadata=data.get("metadata", {}),
        )

    def update_progress(self, progress: int, step: str = None):
        """更新进度"""
        self.progress = max(0, min(100, progress))
        if step:
            self.current_step = step
        self.updated_at = datetime.now()

    def complete(self):
        """标记任务完成"""
        self.status = TaskStatus.COMPLETED
        self.progress = 100
        self.completed_at = datetime.now()
        self.updated_at = datetime.now()

    def fail(self, error_message: str):
        """标记任务失败"""
        self.status = TaskStatus.FAILED
        self.error_message = error_message
        self.updated_at = datetime.now()
