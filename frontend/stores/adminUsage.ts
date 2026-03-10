import { defineStore } from 'pinia'
import { useTaskStore } from '~/stores/task'
import type {
  AdminAccessResponse,
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

const withQuery = (base: string, query: Record<string, string | number | undefined>) => {
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null || `${value}`.trim() === '') return
    params.set(key, String(value))
  })
  const q = params.toString()
  return q ? `${base}?${q}` : base
}

export const useAdminUsageStore = defineStore('admin-usage', {
  state: () => ({
    isAdmin: false,
    checkedAccess: false,
    loadingAccess: false,
    loadingDashboard: false,
    loadingTable: false,
    dashboard: null as AdminUsageDashboardResponse | null,
    tableData: null as AdminUsageTableResponse | null,
  }),

  actions: {
    async _authorizedFetch(path: string): Promise<Response | null> {
      const taskStore = useTaskStore()
      const config = useRuntimeConfig()
      const authed = await taskStore.ensureAuth()
      if (!authed || !taskStore.authToken) return null
      try {
        return await fetch(`${config.public.apiBaseUrl}${path}`, {
          headers: {
            Authorization: `Bearer ${taskStore.authToken}`,
          },
        })
      } catch (_error) {
        return null
      }
    },

    async fetchAccess(force = false): Promise<boolean> {
      if (!force && this.checkedAccess) return this.isAdmin
      this.loadingAccess = true
      try {
        const response = await this._authorizedFetch('/api/admin/access')
        if (!response || !response.ok) {
          this.isAdmin = false
          this.checkedAccess = true
          return false
        }
        const data = await response.json() as AdminAccessResponse
        this.isAdmin = !!data?.isAdmin
        this.checkedAccess = true
        return this.isAdmin
      } finally {
        this.loadingAccess = false
      }
    },

    async fetchDashboard(rangeType: UsageRangeType, anchor?: string): Promise<AdminUsageDashboardResponse | null> {
      this.loadingDashboard = true
      try {
        const path = withQuery('/api/admin/usage/dashboard', {
          rangeType,
          anchor,
          topN: 10,
        })
        const response = await this._authorizedFetch(path)
        if (!response || !response.ok) return null
        const data = await response.json() as AdminUsageDashboardResponse
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
          pageSize: input.pageSize ?? 20,
          sortBy: input.sortBy ?? 'lastUsageAt',
          sortOrder: input.sortOrder ?? 'desc',
        })
        const response = await this._authorizedFetch(path)
        if (!response || !response.ok) return null
        const data = await response.json() as AdminUsageTableResponse
        this.tableData = data
        return data
      } finally {
        this.loadingTable = false
      }
    },
  },
})
