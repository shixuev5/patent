"""
API 数据模型定义
"""
from dataclasses import dataclass
from threading import Event
from typing import Optional

from pydantic import BaseModel


class TaskResponse(BaseModel):
    taskId: str
    status: str
    message: str


class GuestAuthResponse(BaseModel):
    token: str
    userId: str
    expiresAt: str


class UsageResponse(BaseModel):
    userId: str
    dailyLimit: int
    usedToday: int
    remaining: int
    resetAt: str


@dataclass
class CurrentUser:
    user_id: str


@dataclass
class TaskRuntime:
    cancel_event: Event
    upload_file_path: Optional[str] = None
    output_dir: Optional[str] = None
