export type UsageRangeType = 'day' | 'month' | 'year'
export type UsageScopeType = 'task' | 'user' | 'all'

export interface AdminAccessResponse {
  isAdmin: boolean
}

export interface AdminUsageOverview {
  totalTasks: number
  totalUsers: number
  totalPromptTokens: number
  totalCompletionTokens: number
  totalTokens: number
  totalReasoningTokens: number
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
  trend: Array<{
    label: string
    promptTokens: number
    completionTokens: number
    totalTokens: number
    estimatedCostCny: number
  }>
  byTaskType: Array<{
    taskType: string
    taskCount: number
    totalTokens: number
    estimatedCostCny: number
  }>
  topUsers: Array<{
    ownerId: string
    taskCount: number
    totalTokens: number
    estimatedCostCny: number
    priceMissing?: boolean
    latestTaskStatus?: string
  }>
  priceMissing: boolean
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
  items: Record<string, any>[]
}

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
