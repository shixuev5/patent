"""
Task data models.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    PATENT_ANALYSIS = "patent_analysis"
    OFFICE_ACTION_REPLY = "office_action_reply"


@dataclass
class User:
    owner_id: str
    authing_sub: str
    name: Optional[str] = None
    nickname: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    picture: Optional[str] = None
    raw_profile: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    last_login_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "owner_id": self.owner_id,
            "authing_sub": self.authing_sub,
            "name": self.name,
            "nickname": self.nickname,
            "email": self.email,
            "phone": self.phone,
            "picture": self.picture,
            "raw_profile": self.raw_profile,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_login_at": self.last_login_at.isoformat(),
        }


@dataclass
class AccountMonthTarget:
    owner_id: str
    year: int
    month: int
    target_count: int
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Task:
    id: str
    owner_id: Optional[str] = None
    task_type: str = TaskType.PATENT_ANALYSIS.value
    pn: Optional[str] = None
    title: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    progress: int = 0
    current_step: Optional[str] = None
    output_dir: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "task_type": self.task_type,
            "pn": self.pn,
            "title": self.title,
            "status": self.status.value,
            "progress": self.progress,
            "current_step": self.current_step,
            "output_dir": self.output_dir,
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
            task_type=data.get("task_type", TaskType.PATENT_ANALYSIS.value),
            pn=data.get("pn"),
            title=data.get("title"),
            status=TaskStatus(data.get("status", "pending")),
            progress=data.get("progress", 0),
            current_step=data.get("current_step"),
            output_dir=data.get("output_dir"),
            error_message=data.get("error_message"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(),
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            metadata=data.get("metadata", {}),
        )
