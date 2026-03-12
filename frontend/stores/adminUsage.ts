import { defineStore } from 'pinia'
import { useTaskStore } from '~/stores/task'
import { cachedGetJson } from '~/utils/apiClient'
import type {
  AdminAccessResponse,
  AdminEntityTaskDetailResponse,
  AdminEntityTaskListResponse,
  AdminEntityUserListResponse,
  AdminSystemLogDetailResponse,
  AdminSystemLogListResponse,
  AdminSystemLogSummaryResponse,
  AdminUsageDashboardResponse,
  AdminUsageTableResponse,
  UsageRangeType,
  UsageScopeType,
} from '~/types/adminUsage'

interface FetchTableInput {
  rangeType: UsageRangeType
  anchor?: string
  scope: UsageScopeType
  q?: string
  taskType?: string
  status?: string
  model?: string
  page?: number
  pageSize?: number
  sortBy?: string
  sortOrder?: 'asc' | 'desc'
}

interface FetchSystemLogInput {
  category?: string
  eventName?: string
  ownerId?: string
  userName?: string
  taskId?: string
  requestId?: string
  traceId?: string
  provider?: string
  success?: string
  dateFrom?: string
  dateTo?: string
  q?: string
  page?: number
  pageSize?: number
}

interface FetchEntityUsersInput {
  q?: string
  role?: string
  page?: number
  pageSize?: number
  sortBy?: string
  sortOrder?: 'asc' | 'desc'
}

interface FetchEntityTasksInput {
  q?: string
  userName?: string
  taskType?: string
  status?: string
  dateFrom?: string
  dateTo?: string
  page?: number
  pageSize?: number
  sortBy?: string
  sortOrder?: 'asc' | 'desc'
}

const withQuery = (base: string, query: Record<string, string | number | undefined>) => {
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null || `${value}`.trim() === '') return
    params.set(key, String(value))
  })
  const q = params.toString()
  return q ? `${base}?${q}` : base
}

let accessInflight: Promise<boolean> | null = null

export const useAdminUsageStore = defineStore('admin-usage', {
  state: () => ({
    isAdmin: false,
    checkedAccess: false,
    loadingAccess: false,
    loadingDashboard: false,
    loadingTable: false,
    loadingSystemLogs: false,
    loadingSystemLogDetail: false,
    loadingSystemSummary: false,
    loadingEntityUsers: false,
    loadingEntityTasks: false,
    loadingEntityTaskDetail: false,
    dashboard: null as AdminUsageDashboardResponse | null,
    tableData: null as AdminUsageTableResponse | null,
    systemLogSummary: null as AdminSystemLogSummaryResponse | null,
    systemLogs: null as AdminSystemLogListResponse | null,
    systemLogDetail: null as AdminSystemLogDetailResponse | null,
    entityUsers: null as AdminEntityUserListResponse | null,
    entityTasks: null as AdminEntityTaskListResponse | null,
    entityTaskDetail: null as AdminEntityTaskDetailResponse | null,
  }),

  actions: {
    async _authorizedGetJson<T>(
      path: string,
      queryKeyTail: readonly unknown[],
      staleTime: number = 30 * 1000,
    ): Promise<T | null> {
      const taskStore = useTaskStore()
      const config = useRuntimeConfig()
      const authed = await taskStore.ensureAuth()
      if (!authed || !taskStore.authToken) return null
      const authScope = `${taskStore.authMode}:${taskStore.userId || 'anonymous'}`
      try {
        return await cachedGetJson<T>({
          baseUrl: config.public.apiBaseUrl,
          path,
          token: taskStore.authToken,
          queryKey: ['api', authScope, ...queryKeyTail],
          staleTime,
        })
      } catch (_error) {
        return null
      }
    },

    async fetchAccess(force = false): Promise<boolean> {
      if (!force && this.checkedAccess) return this.isAdmin
      if (accessInflight) return accessInflight

      this.loadingAccess = true
      accessInflight = (async () => {
        try {
          const data = await this._authorizedGetJson<AdminAccessResponse>(
            '/api/admin/access',
            ['admin', 'access'],
            15 * 1000,
          )
          if (!data) {
            this.isAdmin = false
            this.checkedAccess = true
            return false
          }
          this.isAdmin = !!data?.isAdmin
          this.checkedAccess = true
          return this.isAdmin
        } finally {
          this.loadingAccess = false
          accessInflight = null
        }
      })()

      return accessInflight
    },

    async fetchDashboard(rangeType: UsageRangeType, anchor?: string): Promise<AdminUsageDashboardResponse | null> {
      this.loadingDashboard = true
      try {
        const path = withQuery('/api/admin/usage/dashboard', {
          rangeType,
          anchor,
        })
        const data = await this._authorizedGetJson<AdminUsageDashboardResponse>(
          path,
          ['admin', 'usage', 'dashboard', rangeType, anchor || ''],
          30 * 1000,
        )
        if (!data) return null
        this.dashboard = data
        return data
      } finally {
        this.loadingDashboard = false
      }
    },

    async fetchTable(input: FetchTableInput): Promise<AdminUsageTableResponse | null> {
      this.loadingTable = true
      try {
        const path = withQuery('/api/admin/usage/table', {
          rangeType: input.rangeType,
          anchor: input.anchor,
          scope: input.scope,
          q: input.q,
          taskType: input.taskType,
          status: input.status,
          model: input.model,
          page: input.page ?? 1,
          pageSize: input.pageSize ?? 10,
          sortBy: input.sortBy ?? 'lastUsageAt',
          sortOrder: input.sortOrder ?? 'desc',
        })
        const data = await this._authorizedGetJson<AdminUsageTableResponse>(
          path,
          ['admin', 'usage', 'table', path],
          20 * 1000,
        )
        if (!data) return null
        this.tableData = data
        return data
      } finally {
        this.loadingTable = false
      }
    },

    async fetchSystemLogSummary(dateFrom?: string, dateTo?: string): Promise<AdminSystemLogSummaryResponse | null> {
      this.loadingSystemSummary = true
      try {
        const path = withQuery('/api/admin/logs/summary', {
          dateFrom,
          dateTo,
        })
        const data = await this._authorizedGetJson<AdminSystemLogSummaryResponse>(
          path,
          ['admin', 'logs', 'summary', dateFrom || '', dateTo || ''],
          20 * 1000,
        )
        if (!data) return null
        this.systemLogSummary = data
        return data
      } finally {
        this.loadingSystemSummary = false
      }
    },

    async fetchSystemLogs(input: FetchSystemLogInput): Promise<AdminSystemLogListResponse | null> {
      this.loadingSystemLogs = true
      try {
        const path = withQuery('/api/admin/logs', {
          category: input.category,
          eventName: input.eventName,
          ownerId: input.ownerId,
          userName: input.userName,
          taskId: input.taskId,
          requestId: input.requestId,
          traceId: input.traceId,
          provider: input.provider,
          success: input.success,
          dateFrom: input.dateFrom,
          dateTo: input.dateTo,
          q: input.q,
          page: input.page ?? 1,
          pageSize: input.pageSize ?? 10,
        })
        const data = await this._authorizedGetJson<AdminSystemLogListResponse>(
          path,
          ['admin', 'logs', 'list', path],
          10 * 1000,
        )
        if (!data) return null
        this.systemLogs = data
        return data
      } finally {
        this.loadingSystemLogs = false
      }
    },

    async fetchSystemLogDetail(logId: string): Promise<AdminSystemLogDetailResponse | null> {
      this.loadingSystemLogDetail = true
      try {
        const path = `/api/admin/logs/${encodeURIComponent(logId)}`
        const data = await this._authorizedGetJson<AdminSystemLogDetailResponse>(
          path,
          ['admin', 'logs', 'detail', logId],
          10 * 1000,
        )
        if (!data) return null
        this.systemLogDetail = data
        return data
      } finally {
        this.loadingSystemLogDetail = false
      }
    },

    clearSystemLogDetail() {
      this.systemLogDetail = null
    },

    async fetchEntityUsers(input: FetchEntityUsersInput): Promise<AdminEntityUserListResponse | null> {
      this.loadingEntityUsers = true
      try {
        const path = withQuery('/api/admin/entities/users', {
          q: input.q,
          role: input.role,
          page: input.page ?? 1,
          pageSize: input.pageSize ?? 10,
          sortBy: input.sortBy ?? 'taskCount',
          sortOrder: input.sortOrder ?? 'desc',
        })
        const data = await this._authorizedGetJson<AdminEntityUserListResponse>(
          path,
          ['admin', 'entities', 'users', path],
          20 * 1000,
        )
        if (!data) return null
        this.entityUsers = data
        return data
      } finally {
        this.loadingEntityUsers = false
      }
    },

    async fetchEntityTasks(input: FetchEntityTasksInput): Promise<AdminEntityTaskListResponse | null> {
      this.loadingEntityTasks = true
      try {
        const path = withQuery('/api/admin/entities/tasks', {
          q: input.q,
          userName: input.userName,
          taskType: input.taskType,
          status: input.status,
          dateFrom: input.dateFrom,
          dateTo: input.dateTo,
          page: input.page ?? 1,
          pageSize: input.pageSize ?? 10,
          sortBy: input.sortBy ?? 'updatedAt',
          sortOrder: input.sortOrder ?? 'desc',
        })
        const data = await this._authorizedGetJson<AdminEntityTaskListResponse>(
          path,
          ['admin', 'entities', 'tasks', path],
          10 * 1000,
        )
        if (!data) return null
        this.entityTasks = data
        return data
      } finally {
        this.loadingEntityTasks = false
      }
    },

    async fetchEntityTaskDetail(taskId: string): Promise<AdminEntityTaskDetailResponse | null> {
      this.loadingEntityTaskDetail = true
      try {
        const path = `/api/admin/entities/tasks/${encodeURIComponent(taskId)}`
        const data = await this._authorizedGetJson<AdminEntityTaskDetailResponse>(
          path,
          ['admin', 'entities', 'task_detail', taskId],
          10 * 1000,
        )
        if (!data) return null
        this.entityTaskDetail = data
        return data
      } finally {
        this.loadingEntityTaskDetail = false
      }
    },

    clearEntityTaskDetail() {
      this.entityTaskDetail = null
    },
  },
})
