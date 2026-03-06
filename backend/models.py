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
    authType: str = "guest"


class UserProfileResponse(BaseModel):
    ownerId: str
    authingSub: str
    name: Optional[str] = None
    nickname: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    picture: Optional[str] = None


class AuthingTokenExchangeRequest(BaseModel):
    idToken: str


class AuthingAuthResponse(BaseModel):
    token: str
    userId: str
    expiresAt: str
    authType: str = "authing"
    user: UserProfileResponse


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
