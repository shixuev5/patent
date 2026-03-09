import { defineStore } from 'pinia'
import { useAuthStore } from '~/stores/auth'
import type { CreateTaskInput, Task, TaskProgress, TaskType } from '~/types/task'
import type { DailyPointsExceededDetail, UsageResponse } from '~/types/usage'

const generateId = () => Math.random().toString(36).slice(2, 11)
const STORAGE_KEY_PREFIX = 'patent_tasks::'
const AUTH_TOKEN_KEY = 'patent_auth_token'
const AUTH_USER_ID_KEY = 'patent_auth_user_id'
const AUTH_MODE_KEY = 'patent_auth_mode'
const GUEST_DEVICE_ID_KEY = 'patent_guest_device_id'
const TASK_CACHE_TTL_MS = 24 * 60 * 60 * 1000
const MAX_COMPLETED_CACHE_COUNT = 20
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

export interface TaskSubmitResult {
  ok: boolean
  message?: string
  error?: string
  errorCode?: string
  shouldPromptLogin?: boolean
}

const normalizeTaskType = (taskType?: string): TaskType => {
  return taskType === 'office_action_reply' ? 'office_action_reply' : 'patent_analysis'
}

const normalizeStatus = (status: string): Task['status'] => {
  if (status === 'failed' || status === 'cancelled') return 'error'
  if (status === 'pending' || status === 'processing' || status === 'completed' || status === 'error') {
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
  updatedAt: Date.now(),
  downloadUrl: serverTask.status === 'completed' ? `/api/tasks/${serverTask.id}/download` : undefined,
})

const createGuestDeviceId = (): string => {
  if (typeof globalThis.crypto !== 'undefined' && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  return `guest_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`
}

export const useTaskStore = defineStore('tasks', {
  state: () => ({
    tasks: [] as Task[],
    loading: false,
    authToken: '' as string,
    userId: '' as string,
    authMode: 'guest' as AuthMode,
    taskStorageKey: '' as string,
    progressStreams: {} as Record<string, EventSource>,
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
    resolveTaskRef(task: Task): Task {
      return this.tasks.find((t) => t.id === task.id) || task
    },

    getCurrentTaskStorageKey(): string {
      return buildTaskStorageKey(this.authMode, this.userId)
    },

    stopAllTracking() {
      Object.keys(this.progressStreams).forEach((key) => this.stopProgressTracking(key))
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
      const active = tasks.filter((task) => task.status === 'pending' || task.status === 'processing')
      const completed = tasks
        .filter((task) => task.status === 'completed')
        .sort((a, b) => (b.updatedAt || b.createdAt) - (a.updatedAt || a.createdAt))
        .slice(0, MAX_COMPLETED_CACHE_COUNT)

      return [...active, ...completed].sort((a, b) => b.createdAt - a.createdAt)
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
          parsedTasks.map((task: any) => ({
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

    applyAuthIdentity(token: string, userId: string, mode: AuthMode) {
      const changed = this.userId !== userId || this.authMode !== mode
      this.authToken = token
      this.userId = userId
      this.authMode = mode

      if (process.client) {
        localStorage.setItem(AUTH_TOKEN_KEY, token)
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
      const response = await fetch(`${config.public.apiBaseUrl}/api/tasks`, {
        headers: {
          Authorization: `Bearer ${this.authToken}`,
        },
      })
      if (!response.ok) return null
      const data = await response.json()
      if (!Array.isArray(data?.tasks)) return []
      return data.tasks.map((item: any) => toTaskFromServer(item))
    },

    applyServerTaskSnapshot(task: Task, serverTask: any) {
      const taskRef = this.resolveTaskRef(task)
      const normalized = normalizeStatus(serverTask?.status || taskRef.status)
      taskRef.status = normalized
      taskRef.taskType = normalizeTaskType(serverTask?.taskType || taskRef.taskType)
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
      try {
        const response = await fetch(`${config.public.apiBaseUrl}/api/tasks/${taskRef.backendId}`, {
          headers: {
            Authorization: `Bearer ${this.authToken}`,
          },
        })
        if (!response.ok) return false
        const data = await response.json()
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
      this.authToken = localStorage.getItem(AUTH_TOKEN_KEY) || ''
      this.userId = localStorage.getItem(AUTH_USER_ID_KEY) || ''
      const mode = localStorage.getItem(AUTH_MODE_KEY)
      this.authMode = mode === 'authing' ? 'authing' : 'guest'
      this.taskStorageKey = this.getCurrentTaskStorageKey()
    },

    clearAuthFromStorage() {
      this.resetInMemoryTasks()
      this.authToken = ''
      this.userId = ''
      this.authMode = 'guest'
      this.taskStorageKey = ''
      if (!process.client) return
      localStorage.removeItem(AUTH_TOKEN_KEY)
      localStorage.removeItem(AUTH_USER_ID_KEY)
      localStorage.removeItem(AUTH_MODE_KEY)
    },

    saveAuthToStorage(token: string, userId: string, mode: AuthMode) {
      this.applyAuthIdentity(token, userId, mode)
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
        const response = await fetch(`${config.public.apiBaseUrl}/api/auth/guest`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ deviceId }),
        })
        if (!response.ok) return false
        const data = await response.json()
        if (!data?.token || !data?.userId) return false
        this.saveAuthToStorage(data.token, data.userId, 'guest')
        return true
      } catch (error) {
        console.error('创建匿名身份失败：', error)
        return false
      }
    },

    async exchangeAuthingAuth(idToken: string): Promise<boolean> {
      const config = useRuntimeConfig()
      try {
        const response = await fetch(`${config.public.apiBaseUrl}/api/auth/authing`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ idToken }),
        })
        if (!response.ok) {
          const errorText = await this.parseApiError(response)
          console.error('Authing token 交换失败：', errorText)
          return false
        }
        const data = await response.json()
        if (!data?.token || !data?.userId) return false
        this.saveAuthToStorage(data.token, data.userId, 'authing')
        return true
      } catch (error) {
        console.error('Authing token 交换异常：', error)
        return false
      }
    },

    async ensureAuth(): Promise<boolean> {
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
            this.authToken
            && this.authMode === 'authing'
            && this.userId
            && this.userId === expectedAuthingUserId
          ) {
            return true
          }
          this.clearAuthFromStorage()
          return await this.exchangeAuthingAuth(idToken)
        }
      }

      if (this.authToken && this.authMode === 'guest' && this.userId) return true

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
      const raw = taskType === 'office_action_reply'
        ? usage.costPerTask.officeActionReply
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
        analysisCount: currentUsage.createdToday.analysisCount + (taskType === 'patent_analysis' ? 1 : 0),
        replyCount: currentUsage.createdToday.replyCount + (taskType === 'office_action_reply' ? 1 : 0),
        totalCount: currentUsage.createdToday.totalCount + 1,
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
      const requiredPoints = taskType === 'office_action_reply'
        ? usage.costPerTask.officeActionReply
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
        costPerTask: baseUsage?.costPerTask || { patentAnalysis: 1, officeActionReply: 1.5 },
        createdToday: baseUsage?.createdToday || { analysisCount: 0, replyCount: 0, totalCount: 0 },
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

      try {
        const response = await fetch(`${config.public.apiBaseUrl}/api/usage${query}`, {
          headers: {
            Authorization: `Bearer ${this.authToken}`,
          },
        })
        if (!response.ok) return null
        const usage = await response.json() as UsageResponse
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

      const isPatent = input.taskType === 'patent_analysis'
      const task: Task = {
        id: generateId(),
        taskType: input.taskType,
        title: isPatent
          ? input.patentNumber || input.file?.name || '未命名任务'
          : input.officeActionFile.name || '审查意见答复任务',
        pn: isPatent ? input.patentNumber?.trim() || undefined : undefined,
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
        if (input.taskType === 'patent_analysis') {
          if (input.patentNumber) formData.append('patentNumber', input.patentNumber)
          if (input.file) formData.append('file', input.file)
        } else {
          formData.append('officeActionFile', input.officeActionFile)
          formData.append('responseFile', input.responseFile)
          if (input.claimsFile) formData.append('claimsFile', input.claimsFile)
          input.comparisonDocs?.forEach((doc) => formData.append('comparisonDocs', doc))
        }

        const response = await fetch(`${config.public.apiBaseUrl}/api/tasks`, {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${this.authToken}`,
          },
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
        if (input.taskType === 'patent_analysis') taskRef.pn = input.patentNumber?.trim() || taskRef.pn
        taskRef.status = normalizeStatus(data.status || 'processing')
        taskRef.currentStep = taskRef.status === 'completed' ? '已复用历史报告' : '处理中'
        taskRef.updatedAt = Date.now()
        this.saveToStorage()
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

        await this.startProgressTracking(taskRef)
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

    async startProgressTracking(task: Task) {
      const config = useRuntimeConfig()
      const taskRef = this.resolveTaskRef(task)
      if (!taskRef.backendId || taskRef.status === 'completed' || taskRef.status === 'error') return

      const authed = await this.ensureAuth()
      if (!authed || !this.authToken) {
        taskRef.status = 'error'
        taskRef.error = '认证已过期，请重试。'
        taskRef.updatedAt = Date.now()
        this.saveToStorage()
        return
      }

      const baseUrl = `${config.public.apiBaseUrl}/api/tasks/${taskRef.backendId}/progress`
      const existing = this.progressStreams[taskRef.backendId]
      if (existing) existing.close()
      const eventSource = new EventSource(withTokenQuery(baseUrl, this.authToken))
      this.progressStreams[taskRef.backendId] = eventSource

      eventSource.onmessage = (event) => {
        try {
          const data: TaskProgress = JSON.parse(event.data)
          this.applyServerTaskSnapshot(taskRef, data)
          if (taskRef.backendId) this.stopProgressPolling(taskRef.backendId)

          if (taskRef.status === 'completed') {
            taskRef.downloadUrl = data.downloadUrl
            eventSource.close()
            delete this.progressStreams[taskRef.backendId as string]
          } else if (taskRef.status === 'error') {
            taskRef.error = data.error || '任务执行失败。'
            eventSource.close()
            delete this.progressStreams[taskRef.backendId as string]
          }

          this.saveToStorage()
        } catch (error) {
          console.error('解析任务进度失败：', error)
        }
      }

      eventSource.onerror = () => {
        eventSource.close()
        delete this.progressStreams[taskRef.backendId as string]
        if (taskRef.backendId) this.startProgressPolling(taskRef)
        setTimeout(() => {
          if (taskRef.status === 'pending' || taskRef.status === 'processing') this.startProgressTracking(taskRef)
        }, 3000)
      }
    },

    startProgressPolling(task: Task) {
      const taskRef = this.resolveTaskRef(task)
      const backendId = taskRef.backendId
      if (!backendId || taskRef.status === 'completed' || taskRef.status === 'error') return
      if (this.progressPollers[backendId]) return

      const poll = async () => {
        const currentTask = this.resolveTaskRef(taskRef)
        if (!currentTask.backendId) {
          this.stopProgressPolling(backendId)
          return
        }
        if (currentTask.status === 'completed' || currentTask.status === 'error') {
          this.stopProgressPolling(backendId)
          return
        }
        const ok = await this.fetchTaskSnapshot(currentTask)
        if (!ok) return
        const refreshedTask = this.resolveTaskRef(taskRef)
        const refreshedStatus: Task['status'] = refreshedTask.status
        if (refreshedStatus === 'completed' || refreshedStatus === 'error') {
          this.stopProgressPolling(backendId)
          this.stopProgressTracking(backendId)
        }
      }

      void poll()
      this.progressPollers[backendId] = setInterval(() => {
        void poll()
      }, 3000)
    },

    stopProgressPolling(backendId?: string) {
      if (!backendId) return
      const timer = this.progressPollers[backendId]
      if (timer) {
        clearInterval(timer)
        delete this.progressPollers[backendId]
      }
    },

    stopProgressTracking(backendId?: string) {
      if (!backendId) return
      const existing = this.progressStreams[backendId]
      if (existing) {
        existing.close()
        delete this.progressStreams[backendId]
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
        const response = await fetch(`${config.public.apiBaseUrl}/api/tasks/${task.backendId}`, {
          method: 'DELETE',
          headers: {
            Authorization: `Bearer ${this.authToken}`,
          },
        })
        if (!response.ok) throw new Error(await this.parseApiError(response))
        this.stopProgressTracking(task.backendId)
        this.stopProgressPolling(task.backendId)
        this.tasks.splice(index, 1)
        this.saveToStorage()
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
        const response = await fetch(`${config.public.apiBaseUrl}/api/tasks`, {
          method: 'DELETE',
          headers: {
            Authorization: `Bearer ${this.authToken}`,
          },
        })
        if (!response.ok) throw new Error(await this.parseApiError(response))
        this.stopAllTracking()
        this.tasks = []
        if (process.client) {
          const storageKey = this.getCurrentTaskStorageKey()
          if (storageKey) localStorage.removeItem(storageKey)
        }
      } catch (error) {
        console.error('清空任务失败：', error)
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
        link.download = task.taskType === 'office_action_reply'
          ? `审查意见答复报告_${task.backendId || task.id}.pdf`
          : `专利分析报告_${task.pn || task.title}.pdf`
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
      if (task.taskType !== 'patent_analysis' || !task.pn) {
        this.showGlobalNotice('error', '该任务类型不支持直接重试，请重新上传文件创建新任务。')
        return
      }

      task.status = 'pending'
      task.progress = 0
      task.currentStep = '等待处理'
      task.error = undefined
      task.updatedAt = Date.now()
      this.saveToStorage()

      const input: CreateTaskInput = {
        taskType: 'patent_analysis',
        patentNumber: task.pn || task.title,
      }
      await this.submitTask(task, input)
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
          this.startProgressTracking(task)
        }
      })
    },
  },
})
