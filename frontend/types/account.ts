export interface AccountProfile {
  ownerId: string
  authType: string
  name?: string
  nickname?: string
  email?: string
  phone?: string
  picture?: string
}

export interface AccountProfileUpdateRequest {
  name: string
  picture: string | null
}

export interface AccountAvatarUploadResponse {
  url: string
}

export interface WeeklyActivityPoint {
  week: string
  analysisCreated: number
  reviewCreated: number
  replyCreated: number
  totalCreated: number
}

export interface TaskWindowCounts {
  analysisCount: number
  reviewCount: number
  replyCount: number
  totalCount: number
}

export interface DailyActivityPoint {
  date: string
  analysisCreated: number
  reviewCreated: number
  replyCreated: number
  totalCreated: number
}

export interface AccountDashboard {
  year: number
  month: number
  monthTarget: number
  monthTargetSource: 'explicit' | 'carried' | 'empty'
  workWeek: TaskWindowCounts
  workMonth: TaskWindowCounts
  summaryText: string
  weeklySeries: WeeklyActivityPoint[]
  dailySeries: DailyActivityPoint[]
}

export interface AccountMonthTargetResponse {
  year: number
  month: number
  targetCount: number
  source: 'explicit' | 'carried' | 'empty'
}
