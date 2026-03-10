"""
API 数据模型定义
"""
from dataclasses import dataclass
from threading import Event
from typing import Any, Dict, List, Literal, Optional

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


class GuestAuthRequest(BaseModel):
    deviceId: Optional[str] = None


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


class AccountProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    picture: Optional[str] = None


class AccountAvatarUploadResponse(BaseModel):
    url: str


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


class TaskPointCost(BaseModel):
    patentAnalysis: float
    officeActionReply: float


class UsageCreatedToday(BaseModel):
    analysisCount: int
    replyCount: int
    totalCount: int


class UsageResponse(BaseModel):
    userId: str
    authType: Literal["guest", "authing"]
    dailyPointLimit: float
    usedPoints: float
    remainingPoints: float
    costPerTask: TaskPointCost
    createdToday: UsageCreatedToday
    requestedTaskType: Optional[str] = None
    requestedTaskPoints: Optional[float] = None
    canCreateRequestedTask: Optional[bool] = None
    resetAt: str


class AdminAccessResponse(BaseModel):
    isAdmin: bool


class AdminUsageOverview(BaseModel):
    totalTasks: int
    totalUsers: int
    totalPromptTokens: int
    totalCompletionTokens: int
    totalTokens: int
    totalReasoningTokens: int
    totalEstimatedCostCny: float
    avgTokensPerTask: float
    avgCostPerTaskCny: float
    priceMissing: bool


class AdminUsageDashboardResponse(BaseModel):
    rangeType: Literal["day", "month", "year"]
    anchor: str
    startAt: str
    endAt: str
    currency: str = "CNY"
    overview: AdminUsageOverview
    trend: List[Dict[str, Any]]
    byTaskType: List[Dict[str, Any]]
    topUsers: List[Dict[str, Any]]
    priceMissing: bool


class AdminUsageTableResponse(BaseModel):
    scope: Literal["task", "user", "all"]
    rangeType: Literal["day", "month", "year"]
    anchor: str
    currency: str = "CNY"
    page: int
    pageSize: int
    total: int
    priceMissing: bool
    items: List[Dict[str, Any]]


@dataclass
class CurrentUser:
    user_id: str


@dataclass
class TaskRuntime:
    cancel_event: Event
    upload_file_path: Optional[str] = None
    output_dir: Optional[str] = None
