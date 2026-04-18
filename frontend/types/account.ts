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

export interface AccountNotificationSettings {
  notificationEmailEnabled: boolean
  workNotificationEmail?: string
  personalNotificationEmail?: string
}

export interface AccountNotificationSettingsUpdateRequest {
  notificationEmailEnabled: boolean
  workNotificationEmail: string | null
  personalNotificationEmail: string | null
}

export interface AccountWeChatBinding {
  bindingId: string
  status: string
  accountId?: string | null
  wechatUserIdMasked?: string | null
  wechatDisplayName?: string | null
  pushTaskCompleted: boolean
  pushTaskFailed: boolean
  pushAiSearchPendingAction: boolean
  boundAt?: string | null
  disconnectedAt?: string | null
  lastInboundAt?: string | null
  lastOutboundAt?: string | null
}

export interface AccountWeChatLoginSession {
  loginSessionId: string
  status: string
  qrSvg: string
  qrUrl?: string | null
  expiresAt: string
  accountId?: string | null
  wechatDisplayName?: string | null
  wechatUserIdMasked?: string | null
  errorMessage?: string | null
  onlineAt?: string | null
  createdAt?: string | null
  updatedAt?: string | null
}

export interface AccountWeChatIntegration {
  bindingStatus: 'unbound' | 'logging_in' | 'bound'
  binding?: AccountWeChatBinding | null
  loginSession?: AccountWeChatLoginSession | null
}

export interface AccountWeChatIntegrationUpdateRequest {
  pushTaskCompleted: boolean
  pushTaskFailed: boolean
  pushAiSearchPendingAction: boolean
}

export interface WeeklyActivityPoint {
  week: string
  analysisCreated: number
  totalCreated: number
}

export interface TaskWindowCounts {
  analysisCount: number
  totalCount: number
}

export interface DailyActivityPoint {
  date: string
  analysisCreated: number
  totalCreated: number
}

export interface AccountDashboard {
  year: number
  month: number
  periodStart: string
  periodEnd: string
  periodLabel: string
  targetMetricType: 'patent_analysis'
  countBasis: 'completed_at'
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
