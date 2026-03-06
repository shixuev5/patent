"""
API 数据模型定义
"""
from dataclasses import dataclass
from threading import Event
from typing import List, Literal, Optional

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


class AccountProfileResponse(BaseModel):
    ownerId: str
    authType: str = "authing"
    name: Optional[str] = None
    nickname: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    picture: Optional[str] = None


class WeeklyActivityPoint(BaseModel):
    week: str
    analysisCreated: int
    replyCreated: int
    totalCreated: int


class TaskWindowCounts(BaseModel):
    analysisCount: int
    replyCount: int
    totalCount: int


class DailyActivityPoint(BaseModel):
    date: str
    analysisCreated: int
    replyCreated: int
    totalCreated: int


class AccountDashboardResponse(BaseModel):
    year: int
    month: int
    monthTarget: int
    monthTargetSource: Literal["explicit", "carried", "empty"]
    workWeek: TaskWindowCounts
    workMonth: TaskWindowCounts
    summaryText: str
    weeklySeries: List[WeeklyActivityPoint]
    dailySeries: List[DailyActivityPoint]


class AccountMonthTargetResponse(BaseModel):
    year: int
    month: int
    targetCount: int
    source: Literal["explicit", "carried", "empty"]


class AccountMonthTargetUpsertRequest(BaseModel):
    year: int
    month: int
    targetCount: int


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
