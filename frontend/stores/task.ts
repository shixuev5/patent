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
    progressStreams: {} as Record<string, EventSource>,
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
    resolveTaskRef(task: Task): Task {
      return this.tasks.find((t) => t.id === task.id) || task
    },

    async init() {
      if (!process.client) return
      this.loadAuthFromStorage()

      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) {
        try {
          this.tasks = JSON.parse(saved)
        } catch (error) {
          console.error('解析本地任务缓存失败：', error)
        }
      }

      // 查询服务器任务队列状态
      const authed = await this.ensureAuth()
      if (authed && this.authToken) {
        try {
          const config = useRuntimeConfig()
          const response = await fetch(`${config.public.apiBaseUrl}/api/tasks`, {
            headers: {
              Authorization: `Bearer ${this.authToken}`,
            },
          })

          if (response.ok) {
            const data = await response.json()
            // 根据服务器返回的任务队列状态更新本地任务
            if (data.tasks && Array.isArray(data.tasks)) {
              // 遍历服务器返回的任务
              data.tasks.forEach((serverTask: any) => {
                // 查找本地是否已存在该任务
                const localTaskIndex = this.tasks.findIndex(t => t.backendId === serverTask.id)
                if (localTaskIndex !== -1) {
                  // 更新本地任务状态
                  const localTask = this.tasks[localTaskIndex]
                  localTask.status = normalizeStatus(serverTask.status)
                  localTask.progress = serverTask.progress || localTask.progress
                  localTask.currentStep = serverTask.step || localTask.currentStep
                  localTask.error = serverTask.error || localTask.error
                  localTask.updatedAt = Date.now()

                  if (localTask.status === 'completed' && !localTask.downloadUrl) {
                    localTask.downloadUrl = `/api/tasks/${serverTask.id}/download`
                  }
                } else {
                  // 添加新任务到本地
                  const newTask: Task = {
                    id: generateId(),
                    backendId: serverTask.id,
                    title: serverTask.title || serverTask.pn || '未命名任务',
                    pn: serverTask.pn,
                    type: serverTask.pn ? 'patent' : 'file',
                    status: normalizeStatus(serverTask.status),
                    progress: serverTask.progress || 0,
                    currentStep: serverTask.step || '等待处理',
                    error: serverTask.error,
                    createdAt: new Date(serverTask.created_at).getTime(),
                    updatedAt: Date.now(),
                    downloadUrl: serverTask.status === 'completed' ? `/api/tasks/${serverTask.id}/download` : undefined,
                  }
                  this.tasks.unshift(newTask)
                }
              })

              this.saveToStorage()
              console.log('任务队列已同步：', data.tasks.length, '个任务')
            }
          }
        } catch (error) {
          console.error('查询任务队列状态失败：', error)
        }
      }

      this.restoreProcessingTasks()
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

    async createTask(input: CreateTaskInput): Promise<{ ok: boolean; message?: string; error?: string }> {
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
      const taskRef = this.resolveTaskRef(task)
      return await this.submitTask(taskRef, input)
    },

    async submitTask(task: Task, input: CreateTaskInput): Promise<{ ok: boolean; message?: string; error?: string }> {
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
        taskRef.backendId = data.taskId
        taskRef.pn = input.patentNumber?.trim() || taskRef.pn
        taskRef.status = normalizeStatus(data.status || 'processing')
        taskRef.currentStep = taskRef.status === 'completed' ? '已复用历史报告' : '处理中'
        taskRef.updatedAt = Date.now()
        this.saveToStorage()

        if (taskRef.status === 'completed') {
          taskRef.progress = 100
          taskRef.downloadUrl = `/api/tasks/${taskRef.backendId}/download`
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
          const normalized = normalizeStatus(data.status)

          taskRef.progress = data.progress ?? taskRef.progress
          taskRef.currentStep = data.step || taskRef.currentStep
          if (data.pn) taskRef.pn = data.pn
          taskRef.status = normalized
          taskRef.updatedAt = Date.now()

          if (normalized === 'completed') {
            taskRef.downloadUrl = data.downloadUrl
            eventSource.close()
            delete this.progressStreams[taskRef.backendId as string]
          } else if (normalized === 'error') {
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
        setTimeout(() => {
          if (taskRef.status === 'processing') this.startProgressTracking(taskRef)
        }, 3000)
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
        Object.keys(this.progressStreams).forEach((key) => this.stopProgressTracking(key))
        this.tasks = []
        if (process.client) localStorage.removeItem(STORAGE_KEY)
      } catch (error) {
        console.error('清空任务失败：', error)
      }
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
