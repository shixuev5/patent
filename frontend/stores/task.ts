import { defineStore } from 'pinia'
import type { CreateTaskInput, Task, TaskProgress } from '~/types/task'

const generateId = () => Math.random().toString(36).slice(2, 11)
const STORAGE_KEY = 'patent_tasks'
const AUTH_TOKEN_KEY = 'patent_auth_token'
const AUTH_USER_ID_KEY = 'patent_auth_user_id'

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

export const useTaskStore = defineStore('tasks', {
  state: () => ({
    tasks: [] as Task[],
    loading: false,
    authToken: '' as string,
    userId: '' as string,
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
    init() {
      if (!process.client) return
      this.loadAuthFromStorage()

      const saved = localStorage.getItem(STORAGE_KEY)
      if (!saved) return

      try {
        this.tasks = JSON.parse(saved)
        this.restoreProcessingTasks()
      } catch (error) {
        console.error('解析本地任务缓存失败：', error)
      }
    },

    loadAuthFromStorage() {
      if (!process.client) return
      this.authToken = localStorage.getItem(AUTH_TOKEN_KEY) || ''
      this.userId = localStorage.getItem(AUTH_USER_ID_KEY) || ''
    },

    saveAuthToStorage(token: string, userId: string) {
      this.authToken = token
      this.userId = userId
      if (!process.client) return
      localStorage.setItem(AUTH_TOKEN_KEY, token)
      localStorage.setItem(AUTH_USER_ID_KEY, userId)
    },

    async ensureAuth(): Promise<boolean> {
      if (this.authToken) return true
      this.loadAuthFromStorage()
      if (this.authToken) return true

      const config = useRuntimeConfig()
      try {
        const response = await fetch(`${config.public.apiBaseUrl}/api/auth/guest`, {
          method: 'POST',
        })
        if (!response.ok) return false
        const data = await response.json()
        if (!data?.token || !data?.userId) return false
        this.saveAuthToStorage(data.token, data.userId)
        return true
      } catch (error) {
        console.error('创建匿名身份失败：', error)
        return false
      }
    },

    async parseApiError(response: Response): Promise<string> {
      try {
        const payload = await response.json()
        if (typeof payload?.detail === 'string') return payload.detail
        if (typeof payload?.detail?.message === 'string') return payload.detail.message
      } catch (_error) {
        // 忽略解析失败，使用兜底文案
      }
      return `请求失败（HTTP ${response.status}）`
    },

    async createTask(input: CreateTaskInput) {
      const task: Task = {
        id: generateId(),
        title: input.patentNumber || input.file?.name || '未命名任务',
        pn: input.patentNumber?.trim() || undefined,
        type: input.patentNumber ? 'patent' : 'file',
        status: 'pending',
        progress: 0,
        currentStep: '等待处理',
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }

      this.tasks.unshift(task)
      this.saveToStorage()
      await this.submitTask(task, input)
      return task
    },

    async submitTask(task: Task, input: CreateTaskInput) {
      const config = useRuntimeConfig()
      const authed = await this.ensureAuth()
      if (!authed || !this.authToken) {
        task.status = 'error'
        task.error = '认证失败，请刷新后重试。'
        task.updatedAt = Date.now()
        this.saveToStorage()
        return
      }

      try {
        const formData = new FormData()
        if (input.patentNumber) formData.append('patentNumber', input.patentNumber)
        if (input.file) formData.append('file', input.file)

        const response = await fetch(`${config.public.apiBaseUrl}/api/tasks`, {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${this.authToken}`,
          },
          body: formData,
        })
        if (!response.ok) throw new Error(await this.parseApiError(response))

        const data = await response.json()
        task.backendId = data.taskId
        task.pn = input.patentNumber?.trim() || task.pn
        task.status = normalizeStatus(data.status || 'processing')
        task.currentStep = task.status === 'completed' ? '已复用历史报告' : '处理中'
        task.updatedAt = Date.now()
        this.saveToStorage()

        if (task.status === 'completed') {
          task.progress = 100
          task.downloadUrl = `/api/tasks/${task.backendId}/download`
          this.saveToStorage()
          return
        }

        this.startProgressTracking(task)
      } catch (error) {
        console.error('提交任务失败：', error)
        task.status = 'error'
        task.error = error instanceof Error ? error.message : '提交失败，请重试。'
        task.updatedAt = Date.now()
        this.saveToStorage()
      }
    },

    async startProgressTracking(task: Task) {
      const config = useRuntimeConfig()
      if (!task.backendId || task.status === 'completed' || task.status === 'error') return

      const authed = await this.ensureAuth()
      if (!authed || !this.authToken) {
        task.status = 'error'
        task.error = '认证已过期，请重试。'
        task.updatedAt = Date.now()
        this.saveToStorage()
        return
      }

      const baseUrl = `${config.public.apiBaseUrl}/api/tasks/${task.backendId}/progress`
      const eventSource = new EventSource(withTokenQuery(baseUrl, this.authToken))

      eventSource.onmessage = (event) => {
        try {
          const data: TaskProgress = JSON.parse(event.data)
          const normalized = normalizeStatus(data.status)

          task.progress = data.progress ?? task.progress
          task.currentStep = data.step || task.currentStep
          if (data.pn) task.pn = data.pn
          task.status = normalized
          task.updatedAt = Date.now()

          if (normalized === 'completed') {
            task.downloadUrl = data.downloadUrl
            eventSource.close()
          } else if (normalized === 'error') {
            task.error = data.error || '任务执行失败。'
            eventSource.close()
          }

          this.saveToStorage()
        } catch (error) {
          console.error('解析任务进度失败：', error)
        }
      }

      eventSource.onerror = () => {
        eventSource.close()
        setTimeout(() => {
          if (task.status === 'processing') this.startProgressTracking(task)
        }, 3000)
      }
    },

    deleteTask(taskId: string) {
      const index = this.tasks.findIndex((t) => t.id === taskId)
      if (index > -1) {
        this.tasks.splice(index, 1)
        this.saveToStorage()
      }
    },

    clearAllTasks() {
      this.tasks = []
      if (process.client) localStorage.removeItem(STORAGE_KEY)
    },

    async downloadResult(task: Task) {
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
        const response = await fetch(downloadUrl, {
          headers: {
            Authorization: `Bearer ${this.authToken}`,
          },
        })
        if (!response.ok) throw new Error(`下载失败：${response.status} ${response.statusText}`)

        const blob = await response.blob()
        const url = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = `专利分析报告_${task.pn || task.title}.pdf`
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        window.URL.revokeObjectURL(url)
      } catch (error) {
        console.error('下载失败：', error)
        window.open(downloadUrl, '_blank')
      }
    },

    async retryTask(task: Task) {
      task.status = 'pending'
      task.progress = 0
      task.currentStep = '等待处理'
      task.error = undefined
      task.updatedAt = Date.now()
      this.saveToStorage()

      const input: CreateTaskInput = task.type === 'patent' ? { patentNumber: task.pn || task.title } : { file: undefined }
      await this.submitTask(task, input)
    },

    saveToStorage() {
      if (process.client) localStorage.setItem(STORAGE_KEY, JSON.stringify(this.tasks))
    },

    restoreProcessingTasks() {
      this.tasks.forEach((task) => {
        task.status = normalizeStatus(task.status)
        if (task.status === 'processing' && task.backendId) this.startProgressTracking(task)
      })
    },
  },
})
