export interface AccountProfile {
  ownerId: string
  authType: string
  name?: string
  nickname?: string
  email?: string
  phone?: string
  picture?: string
}

export interface WeeklyActivityPoint {
  week: string
  analysisCreated: number
  replyCreated: number
  totalCreated: number
}

export interface TaskWindowCounts {
  analysisCount: number
  replyCount: number
  totalCount: number
}

export interface DailyActivityPoint {
  date: string
  analysisCreated: number
  replyCreated: number
  totalCreated: number
}

export interface AccountDashboard {
  year: number
  month: number
  workWeek: TaskWindowCounts
  workMonth: TaskWindowCounts
  summaryText: string
  weeklySeries: WeeklyActivityPoint[]
  dailySeries: DailyActivityPoint[]
}
