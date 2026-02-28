"""
Task data models.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskStep:
    step_name: str
    step_order: int
    status: str = "pending"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_name": self.step_name,
            "step_order": self.step_order,
            "status": self.status,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskStep":
        return cls(
            step_name=data["step_name"],
            step_order=data["step_order"],
            status=data.get("status", "pending"),
            start_time=datetime.fromisoformat(data["start_time"]) if data.get("start_time") else None,
            end_time=datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None,
            error_message=data.get("error_message"),
        )


@dataclass
class Task:
    id: str
    owner_id: Optional[str] = None
    pn: Optional[str] = None
    title: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    current_step: Optional[str] = None
    output_dir: Optional[str] = None
    raw_pdf_path: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    steps: List[TaskStep] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
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
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        return cls(
            id=data["id"],
            owner_id=data.get("owner_id"),
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
