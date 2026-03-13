import type { TaskType } from '~/types/task'

export interface TaskPointCost {
  patentAnalysis: number
  aiReview: number
  officeActionReply: number
}

export interface UsageCreatedToday {
  analysisCount: number
  reviewCount: number
  replyCount: number
  totalCount: number
}

export interface UsageResponse {
  userId: string
  authType: 'guest' | 'authing'
  dailyPointLimit: number
  usedPoints: number
  remainingPoints: number
  costPerTask: TaskPointCost
  createdToday: UsageCreatedToday
  requestedTaskType?: TaskType
  requestedTaskPoints?: number
  canCreateRequestedTask?: boolean
  resetAt: string
}

export interface DailyPointsExceededDetail {
  code: 'DAILY_POINTS_EXCEEDED'
  message: string
  authType: 'guest' | 'authing'
  taskType: TaskType
  requiredPoints: number
  dailyPointLimit: number
  usedPoints: number
  remainingPoints: number
  resetAt: string
  shouldPromptLogin: boolean
}
