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
    name: str
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
    totalTokens: int
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


class AdminSystemLogSummaryResponse(BaseModel):
    totalLogs: int
    failedLogs: int
    failedRate: float
    llmCallCount: int
    byCategory: List[Dict[str, Any]]


class AdminSystemLogItem(BaseModel):
    logId: str
    timestamp: str
    category: str
    eventName: str
    level: str
    ownerId: Optional[str] = None
    userName: Optional[str] = None
    taskId: Optional[str] = None
    taskType: Optional[str] = None
    requestId: Optional[str] = None
    traceId: Optional[str] = None
    method: Optional[str] = None
    path: Optional[str] = None
    statusCode: Optional[int] = None
    durationMs: Optional[int] = None
    provider: Optional[str] = None
    targetHost: Optional[str] = None
    success: bool
    message: Optional[str] = None
    payloadBytes: int
    payloadOverflow: bool
    createdAt: str


class AdminSystemLogListResponse(BaseModel):
    page: int
    pageSize: int
    total: int
    items: List[AdminSystemLogItem]


class AdminSystemLogDetailResponse(BaseModel):
    item: AdminSystemLogItem
    payload: Any = None


class AdminEntityUserItem(BaseModel):
    ownerId: str
    userName: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    lastLoginAt: Optional[str] = None
    createdAt: Optional[str] = None
    taskCount: int = 0
    latestTaskAt: Optional[str] = None


class AdminEntityUserListResponse(BaseModel):
    page: int
    pageSize: int
    total: int
    items: List[AdminEntityUserItem]
    meta: Optional[Dict[str, Any]] = None


class AdminEntityTaskItem(BaseModel):
    taskId: str
    title: Optional[str] = None
    ownerId: Optional[str] = None
    userName: Optional[str] = None
    taskType: Optional[str] = None
    status: Optional[str] = None
    durationSeconds: Optional[int] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    completedAt: Optional[str] = None


class AdminEntityTaskListResponse(BaseModel):
    page: int
    pageSize: int
    total: int
    items: List[AdminEntityTaskItem]
    meta: Optional[Dict[str, Any]] = None


class AdminEntityTaskDetailResponse(BaseModel):
    item: Dict[str, Any]


@dataclass
class CurrentUser:
    user_id: str


@dataclass
class TaskRuntime:
    cancel_event: Event
    upload_file_path: Optional[str] = None
    output_dir: Optional[str] = None
