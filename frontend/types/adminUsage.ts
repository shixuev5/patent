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
