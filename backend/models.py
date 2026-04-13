"""
API 数据模型定义
"""
from dataclasses import dataclass
from threading import Event
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TaskResponse(BaseModel):
    taskId: str
    status: str
    message: str


class PatentNumberValidationResponse(BaseModel):
    patentNumber: str
    exists: bool = True
    patentTitle: Optional[str] = None
    message: str


class SessionAuthResponse(BaseModel):
    access_token: str
    access_expires_at: str
    refresh_token: str
    refresh_expires_at: str
    user_id: str
    auth_type: Literal["guest", "authing"]


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


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


class GuestAuthResponse(SessionAuthResponse):
    auth_type: Literal["guest"] = "guest"


class AuthingAuthResponse(SessionAuthResponse):
    auth_type: Literal["authing"] = "authing"
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


class AccountNotificationSettingsResponse(BaseModel):
    notificationEmailEnabled: bool
    workNotificationEmail: Optional[str] = None
    personalNotificationEmail: Optional[str] = None


class AccountNotificationSettingsUpdateRequest(BaseModel):
    notificationEmailEnabled: bool
    workNotificationEmail: Optional[str] = None
    personalNotificationEmail: Optional[str] = None


class AccountWeChatBindingResponse(BaseModel):
    bindingId: str
    status: str
    botAccountId: Optional[str] = None
    wechatPeerIdMasked: Optional[str] = None
    wechatPeerName: Optional[str] = None
    pushTaskCompleted: bool = True
    pushTaskFailed: bool = True
    pushAiSearchPendingAction: bool = True
    boundAt: Optional[str] = None
    disconnectedAt: Optional[str] = None
    lastInboundAt: Optional[str] = None
    lastOutboundAt: Optional[str] = None


class AccountWeChatBindSessionResponse(BaseModel):
    bindSessionId: str
    status: str
    bindCode: str
    qrPayload: str
    qrSvg: str
    qrUrl: Optional[str] = None
    qrScene: Literal["gateway_login", "bind_payload"] = "bind_payload"
    gatewayStatus: Optional[str] = None
    gatewayErrorMessage: Optional[str] = None
    gatewayUpdatedAt: Optional[str] = None
    expiresAt: str
    botAccountId: Optional[str] = None
    wechatPeerName: Optional[str] = None
    errorMessage: Optional[str] = None
    boundAt: Optional[str] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class AccountWeChatIntegrationResponse(BaseModel):
    bindingStatus: Literal["unbound", "binding", "bound"]
    binding: Optional[AccountWeChatBindingResponse] = None
    bindSession: Optional[AccountWeChatBindSessionResponse] = None


class AccountWeChatIntegrationUpdateRequest(BaseModel):
    pushTaskCompleted: bool
    pushTaskFailed: bool
    pushAiSearchPendingAction: bool


class InternalWeChatBindSessionCompleteRequest(BaseModel):
    botAccountId: str
    wechatPeerId: str
    wechatPeerName: Optional[str] = None


class InternalWeChatBindCodeCompleteRequest(BaseModel):
    bindCode: str
    botAccountId: str
    wechatPeerId: str
    wechatPeerName: Optional[str] = None


class InternalWeChatGatewayLoginStateUpdateRequest(BaseModel):
    status: str
    qrUrl: Optional[str] = None
    errorMessage: Optional[str] = None


class InternalWeChatDeliveryJobClaimRequest(BaseModel):
    limit: int = 1


class InternalWeChatDeliveryJobResolveRequest(BaseModel):
    errorMessage: Optional[str] = None


class InternalWeChatInboundAttachment(BaseModel):
    filename: str
    storedPath: str
    contentType: Optional[str] = None


class InternalWeChatInboundMessageRequest(BaseModel):
    botAccountId: str
    wechatPeerId: str
    wechatPeerName: Optional[str] = None
    text: Optional[str] = None
    attachments: List[InternalWeChatInboundAttachment] = Field(default_factory=list)


class InternalWeChatOutboundMessage(BaseModel):
    type: Literal["text", "file"] = "text"
    text: Optional[str] = None
    fileName: Optional[str] = None
    downloadPath: Optional[str] = None


class InternalWeChatInboundMessageResponse(BaseModel):
    ownerId: str
    bindingId: str
    handled: bool = True
    sessionType: Optional[str] = None
    flowSessionId: Optional[str] = None
    taskId: Optional[str] = None
    messages: List[InternalWeChatOutboundMessage] = Field(default_factory=list)


class WeeklyActivityPoint(BaseModel):
    week: str
    analysisCreated: int
    reviewCreated: int
    replyCreated: int
    searchCreated: int
    totalCreated: int


class TaskWindowCounts(BaseModel):
    analysisCount: int
    reviewCount: int
    replyCount: int
    searchCount: int
    totalCount: int


class DailyActivityPoint(BaseModel):
    date: str
    analysisCreated: int
    reviewCreated: int
    replyCreated: int
    searchCreated: int
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
    aiReview: float
    officeActionReply: float
    aiSearch: float


class UsageCreatedToday(BaseModel):
    analysisCount: int
    reviewCount: int
    replyCount: int
    searchCount: int
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


class AdminUsageSummary(BaseModel):
    totalTasks: int
    totalUsers: int
    totalTokens: int
    totalEstimatedCostCny: float
    totalLlmCallCount: int
    avgTokensPerEntity: float
    avgCostPerEntityCny: float
    entityType: Literal["task", "user", "all"]
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
    summary: AdminUsageSummary
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


class AdminEntityUserStatsResponse(BaseModel):
    userStats: Dict[str, Any]


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


class AdminEntityTaskStatsResponse(BaseModel):
    taskTypeWindows: List[Dict[str, Any]]


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
