"""
Task data models.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from backend.time_utils import parse_storage_ts, utc_now


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(str, Enum):
    PATENT_ANALYSIS = "patent_analysis"
    AI_REPLY = "ai_reply"
    AI_REVIEW = "ai_review"
    AI_SEARCH = "ai_search"


@dataclass
class User:
    owner_id: str
    authing_sub: str
    role: Optional[str] = None
    name: Optional[str] = None
    nickname: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    picture: Optional[str] = None
    notification_email_enabled: bool = False
    work_notification_email: Optional[str] = None
    personal_notification_email: Optional[str] = None
    raw_profile: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_login_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "owner_id": self.owner_id,
            "authing_sub": self.authing_sub,
            "role": self.role,
            "name": self.name,
            "nickname": self.nickname,
            "email": self.email,
            "phone": self.phone,
            "picture": self.picture,
            "notification_email_enabled": self.notification_email_enabled,
            "work_notification_email": self.work_notification_email,
            "personal_notification_email": self.personal_notification_email,
            "raw_profile": self.raw_profile,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_login_at": self.last_login_at.isoformat(),
        }


@dataclass
class RefreshSession:
    token_hash: str
    owner_id: str
    expires_at: datetime
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    revoked_at: Optional[datetime] = None
    replaced_by_token_hash: Optional[str] = None


@dataclass
class AccountMonthTarget:
    owner_id: str
    year: int
    month: int
    target_count: int
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


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
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
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
            created_at=parse_storage_ts(data["created_at"], naive_strategy="utc") if data.get("created_at") else utc_now(),
            updated_at=parse_storage_ts(data["updated_at"], naive_strategy="utc") if data.get("updated_at") else utc_now(),
            completed_at=parse_storage_ts(data["completed_at"], naive_strategy="utc") if data.get("completed_at") else None,
            metadata=data.get("metadata", {}),
        )
