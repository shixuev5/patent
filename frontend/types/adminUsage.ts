export type UsageRangeType = 'day' | 'month' | 'year'
export type UsageScopeType = 'task' | 'user' | 'all'
export type UsageEntityType = 'task' | 'user' | 'all'

export interface AdminAccessResponse {
  isAdmin: boolean
}

export interface AdminUsageOverview {
  totalTasks: number
  totalUsers: number
  totalTokens: number
  totalEstimatedCostCny: number
  avgTokensPerTask: number
  avgCostPerTaskCny: number
  priceMissing: boolean
}

export interface AdminUsageDashboardResponse {
  rangeType: UsageRangeType
  anchor: string
  startAt: string
  endAt: string
  currency: string
  overview: AdminUsageOverview
  priceMissing: boolean
}

export interface AdminUsagePricingStatusResponse {
  region: string
  billingMode: string
  currency: string
  entryCount: number
  lastSuccessAt?: string | null
  lastAttemptAt?: string | null
  expiresAt?: string | null
  sourceUrl?: string | null
  parseStatus: string
  errorMessage?: string | null
  hasUsableCache: boolean
  isExpired: boolean
}

export interface AdminUsagePricingRefreshResponse extends AdminUsagePricingStatusResponse {
  success: boolean
  refreshed: boolean
}

export interface AdminUsageTableResponse {
  scope: UsageScopeType
  rangeType: UsageRangeType
  anchor: string
  currency: string
  page: number
  pageSize: number
  total: number
  priceMissing: boolean
  summary: AdminUsageSummary
  items: AdminUsageTableItem[]
}

export interface AdminUsageSummary {
  totalTasks: number
  totalUsers: number
  totalTokens: number
  totalEstimatedCostCny: number
  totalLlmCallCount: number
  avgTokensPerEntity: number
  avgCostPerEntityCny: number
  entityType: UsageEntityType
  priceMissing: boolean
}

export interface AdminUsageTaskRow {
  taskId: string
  ownerId: string
  userName?: string | null
  taskType: string
  taskStatus: string
  totalTokens: number
  llmCallCount: number
  estimatedCostCny: number
  priceMissing: boolean
  models: string[]
  lastUsageAt?: string | null
}

export interface AdminUsageUserRow {
  ownerId: string
  userName?: string | null
  taskCount: number
  totalTokens: number
  llmCallCount: number
  estimatedCostCny: number
  priceMissing: boolean
  latestUsageAt?: string | null
}

export interface AdminUsageAllRow {
  taskCount: number
  userCount: number
  totalTokens: number
  llmCallCount: number
  estimatedCostCny: number
  priceMissing: boolean
}

export type AdminUsageTableItem = AdminUsageTaskRow | AdminUsageUserRow | AdminUsageAllRow

export interface AdminSystemLogSummaryResponse {
  totalLogs: number
  failedLogs: number
  failedRate: number
  llmCallCount: number
  byCategory: Array<{
    category: string
    count: number
  }>
}

export interface AdminSystemLogItem {
  logId: string
  timestamp: string
  category: string
  eventName: string
  level: string
  ownerId?: string | null
  userName?: string | null
  taskId?: string | null
  taskType?: string | null
  requestId?: string | null
  traceId?: string | null
  method?: string | null
  path?: string | null
  statusCode?: number | null
  durationMs?: number | null
  provider?: string | null
  targetHost?: string | null
  success: boolean
  message?: string | null
  payloadBytes: number
  payloadOverflow: boolean
  createdAt: string
}

export interface AdminSystemLogListResponse {
  page: number
  pageSize: number
  total: number
  items: AdminSystemLogItem[]
}

export interface AdminSystemLogDetailResponse {
  item: AdminSystemLogItem
  payload: unknown
}

export interface AdminEntityUserItem {
  ownerId: string
  userName?: string | null
  email?: string | null
  role?: string | null
  lastLoginAt?: string | null
  createdAt?: string | null
  taskCount: number
  latestTaskAt?: string | null
}

export interface AdminEntityUserListResponse {
  page: number
  pageSize: number
  total: number
  items: AdminEntityUserItem[]
  meta?: Record<string, any>
}

export interface AdminEntityUserStatsResponse {
  userStats: {
    totalUsers: number
    registeredUsers: number
    activeUsers1d: number
    activeUsers7d: number
    activeUsers30d: number
    newUsers1d: number
    newUsers7d: number
    newUsers30d: number
  }
}

export interface AdminEntityTaskItem {
  taskId: string
  title?: string | null
  ownerId?: string | null
  userName?: string | null
  taskType?: string | null
  status?: string | null
  durationSeconds?: number | null
  createdAt?: string | null
  updatedAt?: string | null
  completedAt?: string | null
}

export interface AdminEntityTaskListResponse {
  page: number
  pageSize: number
  total: number
  items: AdminEntityTaskItem[]
  meta?: Record<string, any>
}

export interface AdminEntityTaskStatsResponse {
  taskTypeWindows: Array<{
    taskType: string
    count1d: number
    count7d: number
    count30d: number
  }>
}

export interface AdminEntityTaskDetailResponse {
  item: Record<string, any>
}
