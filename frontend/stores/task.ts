import { defineStore } from 'pinia'
import { useAuthStore } from '~/stores/auth'
import { cachedGetJson, invalidateQueries, registerUnauthorizedTokenRefresher, requestRaw } from '~/utils/apiClient'
import type { CreateTaskInput, Task, TaskType } from '~/types/task'
import type { DailyPointsExceededDetail, UsageResponse } from '~/types/usage'

const generateId = () => Math.random().toString(36).slice(2, 11)
const STORAGE_KEY_PREFIX = 'patent_tasks::'
const AUTH_ACCESS_TOKEN_KEY = 'patent_auth_access_token'
const AUTH_TOKEN_KEY = 'patent_auth_token'
const AUTH_REFRESH_TOKEN_KEY = 'patent_auth_refresh_token'
const AUTH_ACCESS_EXPIRES_AT_KEY = 'patent_auth_access_expires_at'
const AUTH_REFRESH_EXPIRES_AT_KEY = 'patent_auth_refresh_expires_at'
const AUTH_USER_ID_KEY = 'patent_auth_user_id'
const AUTH_MODE_KEY = 'patent_auth_mode'
const GUEST_DEVICE_ID_KEY = 'patent_guest_device_id'
const TASK_CACHE_TTL_MS = 24 * 60 * 60 * 1000
const MAX_COMPLETED_CACHE_COUNT = 20
const PROGRESS_POLL_INTERVAL_MS = 5000
const ACCESS_TOKEN_REFRESH_BUFFER_MS = 5 * 60 * 1000
type AuthMode = 'guest' | 'authing'

interface TaskCachePayload {
  savedAt: number
  tasks: Task[]
}

interface ApiErrorPayload {
  message: string
  detail: any
}

interface PointLimitNoticeState {
  show: boolean
  text: string
  shouldPromptLogin: boolean
}

interface SessionAuthPayload {
  access_token: string
  access_expires_at: string
  refresh_token: string
  refresh_expires_at: string
  user_id: string
  auth_type: 'guest' | 'authing'
}

export interface TaskSubmitResult {
  ok: boolean
  message?: string
  error?: string
  errorCode?: string
  shouldPromptLogin?: boolean
}

const normalizeTaskType = (taskType?: string): TaskType => {
  if (taskType === 'ai_reply') return 'ai_reply'
  if (taskType === 'ai_review') return 'ai_review'
  return 'patent_analysis'
}

const isHiddenTaskType = (taskType?: string): boolean => String(taskType || '').trim().toLowerCase() === 'ai_search'

const normalizeStatus = (status: string): Task['status'] => {
  if (status === 'pending' || status === 'processing' || status === 'completed' || status === 'error' || status === 'failed' || status === 'cancelled') {
    return status
  }
  return 'processing'
}

const withTokenQuery = (url: string, token: string): string => {
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}token=${encodeURIComponent(token)}`
}

const buildTaskStorageKey = (mode: AuthMode, userId: string): string => {
  if (!userId) return ''
  return `${STORAGE_KEY_PREFIX}${mode}:${userId}`
}

const stripFilenameSuffix = (filename?: string | null): string => {
  const cleaned = String(filename || '').trim()
  if (!cleaned) return ''
  return cleaned.replace(/\.[^.]+$/, '').trim()
}

const buildTaskTitle = (input: CreateTaskInput): string => {
  if (input.taskType === 'patent_analysis' || input.taskType === 'ai_review') {
    return input.patentNumber?.trim() || stripFilenameSuffix(input.file?.name) || '未命名任务'
  }
  return stripFilenameSuffix(input.officeActionFile?.name) || 'AI 答复任务'
}

const toTaskFromServer = (serverTask: any): Task => ({
  id: generateId(),
  backendId: serverTask.id,
  title: serverTask.title || serverTask.pn || '未命名任务',
  taskType: normalizeTaskType(serverTask.taskType),
  pn: serverTask.pn,
  status: normalizeStatus(serverTask.status),
  progress: typeof serverTask.progress === 'number' ? serverTask.progress : 0,
  currentStep: serverTask.step || '等待处理',
  error: serverTask.error,
  createdAt: new Date(serverTask.created_at).getTime(),
  updatedAt: serverTask.updated_at ? new Date(serverTask.updated_at).getTime() : Date.now(),
  downloadUrl: serverTask.status === 'completed' ? `/api/tasks/${serverTask.id}/download` : undefined,
})

const createGuestDeviceId = (): string => {
  if (typeof globalThis.crypto !== 'undefined' && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  return `guest_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`
}

const toEpochMs = (value: string): number => {
  const ts = Date.parse(String(value || ''))
  return Number.isFinite(ts) ? ts : 0
}

export const useTaskStore = defineStore('tasks', {
  state: () => ({
    tasks: [] as Task[],
    loading: false,
    authToken: '' as string,
    refreshToken: '' as string,
    accessExpiresAt: '' as string,
    refreshExpiresAt: '' as string,
    userId: '' as string,
    authMode: 'guest' as AuthMode,
    refreshInFlight: null as Promise<boolean> | null,
    unauthorizedRefresherRegistered: false,
    taskStorageKey: '' as string,
    progressPollers: {} as Record<string, ReturnType<typeof setInterval>>,
    downloadingTaskIds: new Set<string>(), // 跟踪正在下载的任务ID
    globalNotice: { type: 'info' as 'success' | 'error' | 'info', text: '' as string, show: false }, // 全局通知
    dailyUsage: null as UsageResponse | null,
    pointLimitNotice: { show: false, text: '', shouldPromptLogin: false } as PointLimitNoticeState,
  }),

  getters: {
    groupedTasks: (state) => ({
      processing: state.tasks.filter((t) => t.status === 'processing'),
      pending: state.tasks.filter((t) => t.status === 'pending'),
      completed: state.tasks.filter((t) => t.status === 'completed'),
      error: state.tasks.filter((t) => t.status === 'error' || t.status === 'failed' || t.status === 'cancelled'),
    }),

    hasProcessingTasks: (state) => state.tasks.some((t) => t.status === 'processing'),

    completedCount: (state) => state.tasks.filter((t) => t.status === 'completed').length,

    isDownloading: (state) => (taskId: string) => state.downloadingTaskIds.has(taskId),

    sortedTasks: (state) => {
      const statusOrder: Record<string, number> = {
        processing: 0,
        pending: 1,
        completed: 2,
        error: 3,
        failed: 3,
        cancelled: 3,
      }
      return [...state.tasks].sort((a, b) => {
        return (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9) || b.createdAt - a.createdAt
      })
    },
  },

  actions: {
    getAuthScopeKey(): string {
      return `${this.authMode}:${this.userId || 'anonymous'}`
    },

    async invalidateTaskAndUsageQueries() {
      const authScope = this.getAuthScopeKey()
      await Promise.all([
        invalidateQueries(['api', authScope, 'tasks']),
        invalidateQueries(['api', authScope, 'task_snapshot']),
        invalidateQueries(['api', authScope, 'usage']),
      ])
    },

    resolveTaskRef(task: Task): Task {
      return this.tasks.find((t) => t.id === task.id) || task
    },

    getCurrentTaskStorageKey(): string {
      return buildTaskStorageKey(this.authMode, this.userId)
    },

    stopAllTracking() {
      Object.keys(this.progressPollers).forEach((key) => this.stopProgressPolling(key))
    },

    resetInMemoryTasks() {
      this.stopAllTracking()
      this.tasks = []
      this.downloadingTaskIds.clear()
      this.dailyUsage = null
      this.pointLimitNotice = { show: false, text: '', shouldPromptLogin: false }
    },

    pruneTasksForCache(tasks: Task[]): Task[] {
      const visibleTasks = tasks.filter((task) => !isHiddenTaskType(task.taskType))
      const active = visibleTasks.filter((task) => task.status === 'pending' || task.status === 'processing')
      const terminal = tasks
        .filter((task) => !isHiddenTaskType(task.taskType))
        .filter((task) => task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled' || task.status === 'error')
        .sort((a, b) => (b.updatedAt || b.createdAt) - (a.updatedAt || a.createdAt))
        .slice(0, MAX_COMPLETED_CACHE_COUNT)

      return [...active, ...terminal].sort((a, b) => b.createdAt - a.createdAt)
    },

    loadTasksFromStorage() {
      if (!process.client) return
      const storageKey = this.getCurrentTaskStorageKey()
      this.taskStorageKey = storageKey
      this.tasks = []
      if (!storageKey) return

      const saved = localStorage.getItem(storageKey)
      if (!saved) return

      try {
        const parsed = JSON.parse(saved) as Partial<TaskCachePayload>
        const savedAt = Number(parsed?.savedAt || 0)
        const now = Date.now()
        if (!savedAt || now - savedAt > TASK_CACHE_TTL_MS) {
          localStorage.removeItem(storageKey)
          return
        }

        const parsedTasks = Array.isArray(parsed?.tasks) ? parsed.tasks : []
        this.tasks = this.pruneTasksForCache(
          parsedTasks
            .filter((task: any) => !isHiddenTaskType(task?.taskType))
            .map((task: any) => ({
              ...task,
              taskType: normalizeTaskType(task.taskType),
              status: normalizeStatus(task.status),
            })),
        )
      } catch (error) {
        console.error('解析本地任务缓存失败：', error)
        localStorage.removeItem(storageKey)
      }
    },

    registerUnauthorizedRefresher() {
      if (this.unauthorizedRefresherRegistered) return
      registerUnauthorizedTokenRefresher(async () => {
        const refreshed = await this.refreshAuthSingleflight()
        return refreshed ? this.authToken : null
      })
      this.unauthorizedRefresherRegistered = true
    },

    isAccessTokenUsable(): boolean {
      if (!this.authToken || !this.accessExpiresAt) return false
      const expMs = toEpochMs(this.accessExpiresAt)
      if (!expMs) return false
      return expMs - Date.now() > ACCESS_TOKEN_REFRESH_BUFFER_MS
    },

    isRefreshTokenUsable(): boolean {
      if (!this.refreshToken || !this.refreshExpiresAt) return false
      const expMs = toEpochMs(this.refreshExpiresAt)
      if (!expMs) return false
      return expMs > Date.now()
    },

    applyAuthIdentity(
      accessToken: string,
      refreshToken: string,
      accessExpiresAt: string,
      refreshExpiresAt: string,
      userId: string,
      mode: AuthMode,
    ) {
      const changed = this.userId !== userId || this.authMode !== mode
      this.authToken = accessToken
      this.refreshToken = refreshToken
      this.accessExpiresAt = accessExpiresAt
      this.refreshExpiresAt = refreshExpiresAt
      this.userId = userId
      this.authMode = mode

      if (process.client) {
        localStorage.setItem(AUTH_TOKEN_KEY, accessToken)
        localStorage.setItem(AUTH_ACCESS_TOKEN_KEY, accessToken)
        localStorage.setItem(AUTH_REFRESH_TOKEN_KEY, refreshToken)
        localStorage.setItem(AUTH_ACCESS_EXPIRES_AT_KEY, accessExpiresAt)
        localStorage.setItem(AUTH_REFRESH_EXPIRES_AT_KEY, refreshExpiresAt)
        localStorage.setItem(AUTH_USER_ID_KEY, userId)
        localStorage.setItem(AUTH_MODE_KEY, mode)
      }

      if (changed) {
        this.resetInMemoryTasks()
        this.loadTasksFromStorage()
      }
    },

    async fetchServerTasks(): Promise<Task[] | null> {
      const config = useRuntimeConfig()
      const authScope = this.getAuthScopeKey()
      const data = await cachedGetJson<any>({
        baseUrl: config.public.apiBaseUrl,
        path: '/api/tasks',
        token: this.authToken,
        queryKey: ['api', authScope, 'tasks', 'list'],
        staleTime: 10 * 1000,
        gcTime: 30 * 60 * 1000,
      })
      if (!Array.isArray(data?.tasks)) return []
      return data.tasks
        .filter((item: any) => !isHiddenTaskType(item?.taskType))
        .map((item: any) => toTaskFromServer(item))
    },

    applyServerTaskSnapshot(task: Task, serverTask: any) {
      const taskRef = this.resolveTaskRef(task)
      const normalized = normalizeStatus(serverTask?.status || taskRef.status)
      taskRef.status = normalized
      taskRef.taskType = normalizeTaskType(serverTask?.taskType || taskRef.taskType)
      if (typeof serverTask?.title === 'string' && serverTask.title.trim()) taskRef.title = serverTask.title
      if (typeof serverTask?.progress === 'number') taskRef.progress = serverTask.progress
      if (typeof serverTask?.step === 'string' && serverTask.step) taskRef.currentStep = serverTask.step
      if (typeof serverTask?.pn === 'string' && serverTask.pn) taskRef.pn = serverTask.pn
      taskRef.error = typeof serverTask?.error === 'string' ? serverTask.error : taskRef.error
      taskRef.updatedAt = Date.now()

      if (normalized === 'completed') {
        taskRef.downloadUrl = serverTask?.downloadUrl || `/api/tasks/${serverTask?.id || taskRef.backendId}/download`
        taskRef.progress = 100
        taskRef.error = undefined
      }
      if (normalized === 'cancelled') {
        taskRef.currentStep = '已取消'
        taskRef.error = typeof serverTask?.error === 'string' && serverTask.error ? serverTask.error : '任务已取消。'
      }
      if (normalized === 'failed' && !taskRef.error) {
        taskRef.error = '任务执行失败。'
      }
      if (normalized === 'error' && !taskRef.error) {
        taskRef.error = '任务执行失败。'
      }
    },

    async fetchTaskSnapshot(task: Task): Promise<boolean> {
      const taskRef = this.resolveTaskRef(task)
      if (!taskRef.backendId) return false
      const authed = await this.ensureAuth()
      if (!authed || !this.authToken) return false

      const config = useRuntimeConfig()
      const authScope = this.getAuthScopeKey()
      try {
        const data = await cachedGetJson<any>({
          baseUrl: config.public.apiBaseUrl,
          path: `/api/tasks/${taskRef.backendId}`,
          token: this.authToken,
          queryKey: ['api', authScope, 'task_snapshot', taskRef.backendId],
          staleTime: 0,
          gcTime: 5 * 60 * 1000,
          persist: false,
        })
        this.applyServerTaskSnapshot(taskRef, data)
        this.saveToStorage()
        return true
      } catch (error) {
        console.error('查询任务快照失败：', error)
        return false
      }
    },

    async init() {
      if (!process.client) return
      this.loadAuthFromStorage()

      const authed = await this.ensureAuth()
      if (!authed || !this.authToken) return

      this.loadTasksFromStorage()

      try {
        const serverTasks = await this.fetchServerTasks()
        if (serverTasks) {
          // 以后端结果为准，避免跨账号残留本地任务。
          this.tasks = serverTasks
          this.saveToStorage()
          console.log('任务队列已同步：', serverTasks.length, '个任务')
        }
      } catch (error) {
        console.error('查询任务队列状态失败：', error)
      }

      await this.fetchUsage()
      this.restoreProcessingTasks()
    },

    loadAuthFromStorage() {
      if (!process.client) return
      this.authToken = localStorage.getItem(AUTH_ACCESS_TOKEN_KEY) || localStorage.getItem(AUTH_TOKEN_KEY) || ''
      this.refreshToken = localStorage.getItem(AUTH_REFRESH_TOKEN_KEY) || ''
      this.accessExpiresAt = localStorage.getItem(AUTH_ACCESS_EXPIRES_AT_KEY) || ''
      this.refreshExpiresAt = localStorage.getItem(AUTH_REFRESH_EXPIRES_AT_KEY) || ''
      this.userId = localStorage.getItem(AUTH_USER_ID_KEY) || ''
      const mode = localStorage.getItem(AUTH_MODE_KEY)
      this.authMode = mode === 'authing' ? 'authing' : 'guest'
      this.taskStorageKey = this.getCurrentTaskStorageKey()
    },

    clearAuthFromStorage() {
      this.resetInMemoryTasks()
      this.authToken = ''
      this.refreshToken = ''
      this.accessExpiresAt = ''
      this.refreshExpiresAt = ''
      this.userId = ''
      this.authMode = 'guest'
      this.refreshInFlight = null
      this.taskStorageKey = ''
      if (!process.client) return
      localStorage.removeItem(AUTH_TOKEN_KEY)
      localStorage.removeItem(AUTH_ACCESS_TOKEN_KEY)
      localStorage.removeItem(AUTH_REFRESH_TOKEN_KEY)
      localStorage.removeItem(AUTH_ACCESS_EXPIRES_AT_KEY)
      localStorage.removeItem(AUTH_REFRESH_EXPIRES_AT_KEY)
      localStorage.removeItem(AUTH_USER_ID_KEY)
      localStorage.removeItem(AUTH_MODE_KEY)
    },

    saveAuthToStorage(payload: SessionAuthPayload, mode: AuthMode) {
      this.applyAuthIdentity(
        payload.access_token,
        payload.refresh_token,
        payload.access_expires_at,
        payload.refresh_expires_at,
        payload.user_id,
        mode,
      )
    },

    ensureGuestDeviceId(): string {
      if (!process.client) return createGuestDeviceId()
      const existing = (localStorage.getItem(GUEST_DEVICE_ID_KEY) || '').trim()
      if (existing) return existing
      const created = createGuestDeviceId()
      localStorage.setItem(GUEST_DEVICE_ID_KEY, created)
      return created
    },

    async createGuestAuth(): Promise<boolean> {
      const config = useRuntimeConfig()
      const deviceId = this.ensureGuestDeviceId()
      try {
        const response = await requestRaw({
          baseUrl: config.public.apiBaseUrl,
          path: '/api/auth/guest',
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ deviceId }),
        })
        if (!response.ok) return false
        const data = await response.json() as SessionAuthPayload
        if (!data?.access_token || !data?.refresh_token || !data?.user_id) return false
        this.saveAuthToStorage(data, 'guest')
        return true
      } catch (error) {
        console.error('创建匿名身份失败：', error)
        return false
      }
    },

    async exchangeAuthingAuth(idToken: string): Promise<boolean> {
      const config = useRuntimeConfig()
      try {
        const response = await requestRaw({
          baseUrl: config.public.apiBaseUrl,
          path: '/api/auth/authing',
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ idToken }),
          retryOnUnauthorized: false,
        })
        if (!response.ok) {
          const errorText = await this.parseApiError(response)
          console.error('Authing token 交换失败：', errorText)
          return false
        }
        const data = await response.json() as SessionAuthPayload
        if (!data?.access_token || !data?.refresh_token || !data?.user_id) return false
        this.saveAuthToStorage(data, 'authing')
        return true
      } catch (error) {
        console.error('Authing token 交换异常：', error)
        return false
      }
    },

    async refreshAuthToken(): Promise<boolean> {
      const config = useRuntimeConfig()
      if (!this.isRefreshTokenUsable()) return false
      try {
        const response = await requestRaw({
          baseUrl: config.public.apiBaseUrl,
          path: '/api/auth/refresh',
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ refresh_token: this.refreshToken }),
          retryOnUnauthorized: false,
        })
        if (!response.ok) return false
        const data = await response.json() as SessionAuthPayload
        if (!data?.access_token || !data?.refresh_token || !data?.user_id) return false
        const mode: AuthMode = data.auth_type === 'authing' ? 'authing' : 'guest'
        this.saveAuthToStorage(data, mode)
        return true
      } catch (error) {
        console.error('刷新认证令牌失败：', error)
        return false
      }
    },

    async refreshAuthSingleflight(): Promise<boolean> {
      if (this.refreshInFlight) return this.refreshInFlight
      this.refreshInFlight = (async () => {
        this.loadAuthFromStorage()
        const ok = await this.refreshAuthToken()
        if (!ok) this.clearAuthFromStorage()
        return ok
      })()
      try {
        return await this.refreshInFlight
      } finally {
        this.refreshInFlight = null
      }
    },

    async ensureAuth(): Promise<boolean> {
      this.registerUnauthorizedRefresher()
      this.loadAuthFromStorage()
      const config = useRuntimeConfig()
      const hasAuthingEnabled = String(config.public.authingAppId || '').trim().length > 0

      if (hasAuthingEnabled) {
        const authStore = useAuthStore()
        await authStore.ensureInitialized()

        const idToken = authStore.idToken
        const isAuthingLoggedIn = authStore.isLoggedIn && !!idToken
        const authingSub = authStore.user?.sub ? String(authStore.user.sub) : ''
        const expectedAuthingUserId = authingSub ? `authing:${authingSub}` : ''

        if (isAuthingLoggedIn) {
          if (
            this.isAccessTokenUsable()
            && this.authMode === 'authing'
            && this.userId
            && this.userId === expectedAuthingUserId
          ) {
            return true
          }
          if (
            this.authMode === 'authing'
            && this.userId
            && this.userId === expectedAuthingUserId
            && this.isRefreshTokenUsable()
          ) {
            const refreshed = await this.refreshAuthSingleflight()
            if (refreshed && this.isAccessTokenUsable()) return true
          }
          this.clearAuthFromStorage()
          return await this.exchangeAuthingAuth(idToken)
        }
      }

      if (this.authMode === 'guest' && this.userId) {
        if (this.isAccessTokenUsable()) return true
        if (this.isRefreshTokenUsable()) {
          const refreshed = await this.refreshAuthSingleflight()
          if (refreshed && this.isAccessTokenUsable()) return true
        }
      }

      this.clearAuthFromStorage()
      return await this.createGuestAuth()
    },

    formatPointValue(value: number): string {
      if (!Number.isFinite(value)) return '0'
      return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/, '')
    },

    roundPoint(value: number): number {
      if (!Number.isFinite(value)) return 0
      return Number(value.toFixed(3))
    },

    getTaskPointCost(usage: UsageResponse, taskType: TaskType): number {
      const raw = taskType === 'ai_reply'
        ? usage.costPerTask.officeActionReply
        : taskType === 'ai_review'
          ? usage.costPerTask.aiReview
          : usage.costPerTask.patentAnalysis
      return Number.isFinite(raw) ? Math.max(0, raw) : 0
    },

    applyOptimisticUsageAfterCreate(taskType: TaskType, usageBeforeSubmit: UsageResponse | null) {
      const currentUsage = this.dailyUsage
      if (!currentUsage) return

      // Server usage may already be updated; avoid double counting.
      if (
        usageBeforeSubmit
        && this.roundPoint(currentUsage.usedPoints) > this.roundPoint(usageBeforeSubmit.usedPoints)
      ) {
        return
      }

      const cost = this.getTaskPointCost(currentUsage, taskType)
      if (cost <= 0) return

      const nextUsed = this.roundPoint(currentUsage.usedPoints + cost)
      const nextRemaining = this.roundPoint(Math.max(0, currentUsage.remainingPoints - cost))
      const nextCreatedToday = {
        analysisCount: (currentUsage.createdToday.analysisCount || 0) + (taskType === 'patent_analysis' ? 1 : 0),
        reviewCount: (currentUsage.createdToday.reviewCount || 0) + (taskType === 'ai_review' ? 1 : 0),
        replyCount: (currentUsage.createdToday.replyCount || 0) + (taskType === 'ai_reply' ? 1 : 0),
        searchCount: currentUsage.createdToday.searchCount || 0,
        totalCount: (currentUsage.createdToday.totalCount || 0) + 1,
      }

      this.dailyUsage = {
        ...currentUsage,
        usedPoints: nextUsed,
        remainingPoints: nextRemaining,
        createdToday: nextCreatedToday,
      }
    },

    buildPointLimitNoticeText(
      authType: 'guest' | 'authing',
      usedPoints: number,
      dailyPointLimit: number,
      remainingPoints: number,
      requiredPoints: number,
    ): string {
      const used = this.formatPointValue(usedPoints)
      const limit = this.formatPointValue(dailyPointLimit)
      const remaining = this.formatPointValue(remainingPoints)
      const required = this.formatPointValue(requiredPoints)
      if (authType === 'guest') {
        return `今日积分不足：已用 ${used}/${limit}，剩余 ${remaining}，当前任务需 ${required}。登录/注册可获得更多每日积分。`
      }
      return `今日积分不足：已用 ${used}/${limit}，剩余 ${remaining}，当前任务需 ${required}。请明日重置后再试。`
    },

    clearPointLimitNotice() {
      this.pointLimitNotice = {
        show: false,
        text: '',
        shouldPromptLogin: false,
      }
    },

    applyUsagePointLimitNotice(usage: UsageResponse, taskType: TaskType) {
      const requiredPoints = taskType === 'ai_reply'
        ? usage.costPerTask.officeActionReply
        : taskType === 'ai_review'
          ? usage.costPerTask.aiReview
          : usage.costPerTask.patentAnalysis
      const shouldPromptLogin = usage.authType === 'guest'
      this.pointLimitNotice = {
        show: true,
        text: this.buildPointLimitNoticeText(
          usage.authType,
          usage.usedPoints,
          usage.dailyPointLimit,
          usage.remainingPoints,
          requiredPoints,
        ),
        shouldPromptLogin,
      }
    },

    applyQuotaDetailPointLimitNotice(detail: DailyPointsExceededDetail) {
      const baseUsage = this.dailyUsage
      this.dailyUsage = {
        userId: this.userId || baseUsage?.userId || '',
        authType: detail.authType,
        dailyPointLimit: detail.dailyPointLimit,
        usedPoints: detail.usedPoints,
        remainingPoints: detail.remainingPoints,
        costPerTask: baseUsage?.costPerTask || { patentAnalysis: 1, aiReview: 1, officeActionReply: 2, aiSearch: 1 },
        createdToday: baseUsage?.createdToday || { analysisCount: 0, reviewCount: 0, replyCount: 0, searchCount: 0, totalCount: 0 },
        requestedTaskType: detail.taskType,
        requestedTaskPoints: detail.requiredPoints,
        canCreateRequestedTask: false,
        resetAt: detail.resetAt,
      }
      this.pointLimitNotice = {
        show: true,
        text: this.buildPointLimitNoticeText(
          detail.authType,
          detail.usedPoints,
          detail.dailyPointLimit,
          detail.remainingPoints,
          detail.requiredPoints,
        ),
        shouldPromptLogin: !!detail.shouldPromptLogin,
      }
    },

    async parseApiErrorPayload(response: Response): Promise<ApiErrorPayload> {
      let payload: any = null
      try {
        payload = await response.json()
      } catch (_error) {
        payload = null
      }

      if (typeof payload?.detail === 'string') {
        return {
          message: payload.detail,
          detail: payload.detail,
        }
      }
      if (typeof payload?.detail?.message === 'string') {
        return {
          message: payload.detail.message,
          detail: payload.detail,
        }
      }
      return {
        message: `请求失败（HTTP ${response.status}）`,
        detail: payload?.detail,
      }
    },

    async parseApiError(response: Response): Promise<string> {
      const payload = await this.parseApiErrorPayload(response)
      return payload.message
    },

    async fetchUsage(taskType?: TaskType): Promise<UsageResponse | null> {
      const config = useRuntimeConfig()
      const authed = await this.ensureAuth()
      if (!authed || !this.authToken) return null
      const query = taskType ? `?taskType=${encodeURIComponent(taskType)}` : ''
      const authScope = this.getAuthScopeKey()

      try {
        const usage = await cachedGetJson<UsageResponse>({
          baseUrl: config.public.apiBaseUrl,
          path: `/api/usage${query}`,
          token: this.authToken,
          queryKey: ['api', authScope, 'usage', taskType || 'all'],
          staleTime: 10 * 1000,
          gcTime: 30 * 60 * 1000,
        })
        this.dailyUsage = usage

        if (taskType && usage.canCreateRequestedTask === false) {
          this.applyUsagePointLimitNotice(usage, taskType)
        } else if (usage.remainingPoints > 0) {
          this.clearPointLimitNotice()
        }
        return usage
      } catch (error) {
        console.error('获取积分额度失败：', error)
        return null
      }
    },

    async createTask(input: CreateTaskInput): Promise<TaskSubmitResult> {
      const usage = await this.fetchUsage(input.taskType)
      if (usage && usage.canCreateRequestedTask === false) {
        this.applyUsagePointLimitNotice(usage, input.taskType)
        return {
          ok: false,
          errorCode: 'DAILY_POINTS_EXCEEDED',
          error: this.pointLimitNotice.text,
          shouldPromptLogin: usage.authType === 'guest',
        }
      }

      const isPatentLike = input.taskType === 'patent_analysis' || input.taskType === 'ai_review'
      const task: Task = {
        id: generateId(),
        taskType: input.taskType,
        title: buildTaskTitle(input),
        pn: isPatentLike ? input.patentNumber?.trim() || undefined : undefined,
        status: 'pending',
        progress: 0,
        currentStep: '等待处理',
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }

      this.tasks.unshift(task)
      this.saveToStorage()
      const taskRef = this.resolveTaskRef(task)
      const usageBeforeSubmit = this.dailyUsage
        ? {
            ...this.dailyUsage,
            costPerTask: { ...this.dailyUsage.costPerTask },
            createdToday: { ...this.dailyUsage.createdToday },
          }
        : null
      const result = await this.submitTask(taskRef, input)
      if (!result.ok && result.errorCode === 'DAILY_POINTS_EXCEEDED' && !taskRef.backendId) {
        this.tasks = this.tasks.filter((item) => item.id !== taskRef.id)
        this.saveToStorage()
      }
      if (result.ok) {
        this.applyOptimisticUsageAfterCreate(input.taskType, usageBeforeSubmit)
        // Asynchronously refresh real usage from server to reconcile any lag.
        setTimeout(() => {
          void this.fetchUsage(input.taskType)
        }, 1200)
      }
      return result
    },

    async submitTask(task: Task, input: CreateTaskInput): Promise<TaskSubmitResult> {
      const taskRef = this.resolveTaskRef(task)
      const config = useRuntimeConfig()
      const authed = await this.ensureAuth()
      if (!authed || !this.authToken) {
        taskRef.status = 'error'
        taskRef.error = '认证失败，请刷新后重试。'
        taskRef.updatedAt = Date.now()
        this.saveToStorage()
        return { ok: false, error: taskRef.error }
      }

      try {
        const formData = new FormData()
        formData.append('taskType', input.taskType)
        if (input.taskType === 'patent_analysis' || input.taskType === 'ai_review') {
          if (input.patentNumber) formData.append('patentNumber', input.patentNumber)
          if (input.file) formData.append('file', input.file)
        } else {
          formData.append('officeActionFile', input.officeActionFile)
          formData.append('responseFile', input.responseFile)
          if (input.previousClaimsFile) formData.append('previousClaimsFile', input.previousClaimsFile)
          if (input.currentClaimsFile) formData.append('currentClaimsFile', input.currentClaimsFile)
          input.comparisonDocs?.forEach((doc) => formData.append('comparisonDocs', doc))
        }

        const response = await requestRaw({
          baseUrl: config.public.apiBaseUrl,
          path: '/api/tasks',
          method: 'POST',
          token: this.authToken,
          body: formData,
        })
        if (!response.ok) {
          const apiError = await this.parseApiErrorPayload(response)
          const detail = apiError.detail as DailyPointsExceededDetail | undefined
          if (response.status === 429 && detail?.code === 'DAILY_POINTS_EXCEEDED') {
            this.applyQuotaDetailPointLimitNotice(detail)
            taskRef.status = 'error'
            taskRef.error = this.pointLimitNotice.text
            taskRef.updatedAt = Date.now()
            this.saveToStorage()
            return {
              ok: false,
              errorCode: detail.code,
              error: this.pointLimitNotice.text,
              shouldPromptLogin: !!detail.shouldPromptLogin,
            }
          }
          throw new Error(apiError.message)
        }

        const data = await response.json()
        taskRef.backendId = data.taskId
        taskRef.taskType = input.taskType
        if (input.taskType === 'patent_analysis' || input.taskType === 'ai_review') {
          taskRef.pn = input.patentNumber?.trim() || taskRef.pn
        }
        taskRef.status = normalizeStatus(data.status || 'processing')
        taskRef.currentStep = taskRef.status === 'completed' ? '已复用历史报告' : '处理中'
        taskRef.updatedAt = Date.now()
        this.saveToStorage()
        await this.invalidateTaskAndUsageQueries()
        await this.fetchUsage(input.taskType)

        if (taskRef.status === 'completed') {
          taskRef.progress = 100
          taskRef.downloadUrl = `/api/tasks/${taskRef.backendId}/download`
          this.saveToStorage()
          return {
            ok: true,
            message: data?.message || '已复用历史报告。',
          }
        }

        await this.fetchTaskSnapshot(taskRef)
        const latestTaskRef = this.resolveTaskRef(taskRef)
        const latestStatus: Task['status'] = latestTaskRef.status
        if (latestStatus === 'completed') {
          this.saveToStorage()
          return {
            ok: true,
            message: data?.message || '已复用历史报告。',
          }
        }

        this.startProgressPolling(taskRef)
        return {
          ok: true,
          message: data?.message || '任务已创建，正在分析。',
        }
      } catch (error) {
        console.error('提交任务失败：', error)
        taskRef.status = 'error'
        taskRef.error = error instanceof Error ? error.message : '提交失败，请重试。'
        taskRef.updatedAt = Date.now()
        this.saveToStorage()
        return { ok: false, error: taskRef.error }
      }
    },

    startProgressPolling(task: Task) {
      const taskRef = this.resolveTaskRef(task)
      const backendId = taskRef.backendId
      if (!backendId || taskRef.status === 'completed' || taskRef.status === 'error' || taskRef.status === 'failed' || taskRef.status === 'cancelled') return
      if (this.progressPollers[backendId]) return

      const poll = async () => {
        const currentTask = this.resolveTaskRef(taskRef)
        if (!currentTask.backendId) {
          this.stopProgressPolling(backendId)
          return
        }
        if (currentTask.status === 'completed' || currentTask.status === 'error' || currentTask.status === 'failed' || currentTask.status === 'cancelled') {
          this.stopProgressPolling(backendId)
          return
        }
        const ok = await this.fetchTaskSnapshot(currentTask)
        if (!ok) return
        const refreshedTask = this.resolveTaskRef(taskRef)
        const refreshedStatus: Task['status'] = refreshedTask.status
        if (refreshedStatus === 'completed' || refreshedStatus === 'error' || refreshedStatus === 'failed' || refreshedStatus === 'cancelled') {
          this.stopProgressPolling(backendId)
        }
      }

      void poll()
      this.progressPollers[backendId] = setInterval(() => {
        void poll()
      }, PROGRESS_POLL_INTERVAL_MS)
    },

    stopProgressPolling(backendId?: string) {
      if (!backendId) return
      const timer = this.progressPollers[backendId]
      if (timer) {
        clearInterval(timer)
        delete this.progressPollers[backendId]
      }
    },

    async deleteTask(taskId: string) {
      const index = this.tasks.findIndex((t) => t.id === taskId)
      if (index < 0) return
      const task = this.tasks[index]
      if (!task.backendId) {
        this.tasks.splice(index, 1)
        this.saveToStorage()
        return
      }

      const authed = await this.ensureAuth()
      if (!authed || !this.authToken) {
        task.error = '认证失败，请刷新后重试。'
        task.updatedAt = Date.now()
        this.saveToStorage()
        return
      }

      try {
        const config = useRuntimeConfig()
        const response = await requestRaw({
          baseUrl: config.public.apiBaseUrl,
          path: `/api/tasks/${task.backendId}`,
          method: 'DELETE',
          token: this.authToken,
        })
        if (!response.ok) throw new Error(await this.parseApiError(response))
        this.stopProgressPolling(task.backendId)
        this.tasks.splice(index, 1)
        this.saveToStorage()
        await this.invalidateTaskAndUsageQueries()
      } catch (error) {
        console.error('删除任务失败：', error)
        task.error = error instanceof Error ? error.message : '删除失败，请重试。'
        task.updatedAt = Date.now()
        this.saveToStorage()
      }
    },

    async clearAllTasks() {
      const authed = await this.ensureAuth()
      if (!authed || !this.authToken) {
        console.error('认证失败，无法清空任务')
        return
      }

      try {
        const config = useRuntimeConfig()
        const response = await requestRaw({
          baseUrl: config.public.apiBaseUrl,
          path: '/api/tasks',
          method: 'DELETE',
          token: this.authToken,
        })
        if (!response.ok) throw new Error(await this.parseApiError(response))
        const data = await response.json() as { deleted?: number; skipped_running?: number }
        const remainingBackendIds = new Set(
          this.tasks
            .filter((task) => task.status === 'pending' || task.status === 'processing')
            .map((task) => task.backendId)
            .filter((value): value is string => !!value),
        )
        Object.keys(this.progressPollers).forEach((backendId) => {
          if (!remainingBackendIds.has(backendId)) this.stopProgressPolling(backendId)
        })
        this.tasks = this.tasks.filter((task) => task.status === 'pending' || task.status === 'processing')
        await this.invalidateTaskAndUsageQueries()
        this.saveToStorage()
        if ((data?.skipped_running || 0) > 0) {
          this.showGlobalNotice('info', `已清空 ${data?.deleted || 0} 个终态任务，跳过 ${(data?.skipped_running || 0)} 个进行中任务。`)
        } else {
          this.showGlobalNotice('success', `已清空 ${data?.deleted || 0} 个任务。`)
        }
      } catch (error) {
        console.error('清空任务失败：', error)
        this.showGlobalNotice('error', error instanceof Error ? error.message : '清空任务失败，请稍后重试。')
      }
    },

    showGlobalNotice(type: 'success' | 'error' | 'info', text: string, duration: number = 4000) {
      this.globalNotice = { type, text, show: true }
      // 自动隐藏通知
      setTimeout(() => {
        this.globalNotice.show = false
      }, duration)
    },

    async downloadResult(task: Task) {
      // 检查是否正在下载
      if (this.downloadingTaskIds.has(task.id)) return

      if (!task.downloadUrl) return
      const config = useRuntimeConfig()
      const baseUrl = config.public.apiBaseUrl
      const rawDownloadUrl = task.downloadUrl.startsWith('http') ? task.downloadUrl : `${baseUrl}${task.downloadUrl}`

      const authed = await this.ensureAuth()
      if (!authed || !this.authToken) {
        window.open(rawDownloadUrl, '_blank')
        return
      }

      const downloadUrl = withTokenQuery(rawDownloadUrl, this.authToken)

      try {
        this.downloadingTaskIds.add(task.id)
        const link = document.createElement('a')
        link.href = downloadUrl
        link.target = '_blank'
        link.rel = 'noopener'
        link.download = task.taskType === 'ai_reply'
          ? `AI 答复报告_${task.pn || task.title}.pdf`
          : task.taskType === 'ai_review'
            ? `AI 审查报告_${task.pn || task.title}.pdf`
            : `AI 分析报告_${task.pn || task.title}.pdf`
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
      } catch (error) {
        console.error('下载失败：', error)
        this.showGlobalNotice('error', '下载失败，请稍后重试。')
        window.open(downloadUrl, '_blank')
      } finally {
        // 移除下载标记
        this.downloadingTaskIds.delete(task.id)
      }
    },

    async retryTask(task: Task) {
      const taskRef = this.resolveTaskRef(task)
      if (!taskRef.backendId) {
        this.showGlobalNotice('error', '原任务缺少服务端记录，无法重试。')
        return
      }

      const authed = await this.ensureAuth()
      if (!authed || !this.authToken) {
        this.showGlobalNotice('error', '认证失败，请刷新后重试。')
        return
      }

      try {
        const config = useRuntimeConfig()
        const response = await requestRaw({
          baseUrl: config.public.apiBaseUrl,
          path: `/api/tasks/${taskRef.backendId}/retry`,
          method: 'POST',
          token: this.authToken,
        })
        if (!response.ok) throw new Error(await this.parseApiError(response))
        const data = await response.json() as { taskId?: string; status?: string; message?: string }
        if (!data?.taskId) throw new Error('重试任务创建失败。')

        const retriedTask: Task = {
          id: generateId(),
          backendId: data.taskId,
          title: taskRef.title,
          taskType: taskRef.taskType,
          pn: taskRef.pn,
          status: normalizeStatus(data.status || 'pending'),
          progress: 0,
          currentStep: '等待处理',
          createdAt: Date.now(),
          updatedAt: Date.now(),
        }
        this.tasks.unshift(retriedTask)
        this.saveToStorage()
        await this.invalidateTaskAndUsageQueries()
        await this.fetchUsage(taskRef.taskType)
        await this.fetchTaskSnapshot(retriedTask)
        const latestTaskRef = this.resolveTaskRef(retriedTask)
        if (latestTaskRef.status === 'pending' || latestTaskRef.status === 'processing') {
          this.startProgressPolling(latestTaskRef)
        }
        this.showGlobalNotice('success', data.message || '重试任务已创建并开始处理。')
      } catch (error) {
        console.error('重试任务失败：', error)
        this.showGlobalNotice('error', error instanceof Error ? error.message : '重试任务失败，请稍后重试。')
      }
    },

    async cancelTask(task: Task) {
      const taskRef = this.resolveTaskRef(task)
      if (!taskRef.backendId) {
        this.showGlobalNotice('error', '任务尚未创建完成，暂时无法取消。')
        return
      }

      const authed = await this.ensureAuth()
      if (!authed || !this.authToken) {
        this.showGlobalNotice('error', '认证失败，请刷新后重试。')
        return
      }

      try {
        const config = useRuntimeConfig()
        const response = await requestRaw({
          baseUrl: config.public.apiBaseUrl,
          path: `/api/tasks/${taskRef.backendId}/cancel`,
          method: 'POST',
          token: this.authToken,
        })
        if (!response.ok) throw new Error(await this.parseApiError(response))
        const data = await response.json() as { message?: string }
        this.stopProgressPolling(taskRef.backendId)
        taskRef.status = 'cancelled'
        taskRef.currentStep = '已取消'
        taskRef.error = data?.message || '任务已取消。'
        taskRef.updatedAt = Date.now()
        this.saveToStorage()
        await this.invalidateTaskAndUsageQueries()
        this.showGlobalNotice('success', data?.message || '任务已取消。')
      } catch (error) {
        console.error('取消任务失败：', error)
        this.showGlobalNotice('error', error instanceof Error ? error.message : '取消任务失败，请稍后重试。')
      }
    },

    saveToStorage() {
      if (!process.client) return
      const storageKey = this.getCurrentTaskStorageKey()
      if (!storageKey) return
      this.taskStorageKey = storageKey
      const payload: TaskCachePayload = {
        savedAt: Date.now(),
        tasks: this.pruneTasksForCache(this.tasks),
      }
      localStorage.setItem(storageKey, JSON.stringify(payload))
    },

    restoreProcessingTasks() {
      this.tasks.forEach((task) => {
        task.status = normalizeStatus(task.status)
        if ((task.status === 'pending' || task.status === 'processing') && task.backendId) {
          this.startProgressPolling(task)
        }
      })
    },
  },
})
