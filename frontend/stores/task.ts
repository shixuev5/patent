import { defineStore } from 'pinia'
import type { Task, CreateTaskInput, TaskProgress } from '~/types/task'

const generateId = () => Math.random().toString(36).slice(2, 11)
const STORAGE_KEY = 'patent_tasks'

const normalizeStatus = (status: string): Task['status'] => {
  if (status === 'failed' || status === 'cancelled') return 'error'
  if (status === 'pending' || status === 'processing' || status === 'completed' || status === 'error') return status
  return 'processing'
}

export const useTaskStore = defineStore('tasks', {
  state: () => ({
    tasks: [] as Task[],
    loading: false,
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
      const saved = localStorage.getItem(STORAGE_KEY)
      if (!saved) return
      try {
        this.tasks = JSON.parse(saved)
        this.restoreProcessingTasks()
      } catch (e) {
        console.error('Failed to parse tasks from storage:', e)
      }
    },

    async createTask(input: CreateTaskInput) {
      const task: Task = {
        id: generateId(),
        title: input.patentNumber || input.file?.name || '未命名任务',
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
      try {
        const formData = new FormData()
        if (input.patentNumber) formData.append('patentNumber', input.patentNumber)
        if (input.file) formData.append('file', input.file)

        const response = await fetch(`${config.public.apiBaseUrl}/api/tasks`, {
          method: 'POST',
          body: formData,
        })
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)

        const data = await response.json()
        task.backendId = data.taskId
        task.status = 'processing'
        task.currentStep = '正在处理'
        task.updatedAt = Date.now()
        this.saveToStorage()
        this.startProgressTracking(task)
      } catch (error) {
        console.error('Failed to submit task:', error)
        task.status = 'error'
        task.error = '提交失败，请重试'
        task.updatedAt = Date.now()
        this.saveToStorage()
      }
    },

    startProgressTracking(task: Task) {
      const config = useRuntimeConfig()
      if (!task.backendId || task.status === 'completed' || task.status === 'error') return

      const eventSource = new EventSource(`${config.public.apiBaseUrl}/api/tasks/${task.backendId}/progress`)

      eventSource.onmessage = (event) => {
        try {
          const data: TaskProgress = JSON.parse(event.data)
          const normalized = normalizeStatus(data.status)

          task.progress = data.progress ?? task.progress
          task.currentStep = data.step || task.currentStep
          task.status = normalized
          task.updatedAt = Date.now()

          if (normalized === 'completed') {
            task.downloadUrl = data.downloadUrl
            eventSource.close()
          } else if (normalized === 'error') {
            task.error = data.error || '任务执行失败'
            eventSource.close()
          }

          this.saveToStorage()
        } catch (e) {
          console.error('Failed to parse progress data:', e)
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
      const downloadUrl = task.downloadUrl.startsWith('http') ? task.downloadUrl : `${baseUrl}${task.downloadUrl}`

      try {
        const response = await fetch(downloadUrl)
        if (!response.ok) throw new Error(`下载失败: ${response.status} ${response.statusText}`)

        const blob = await response.blob()
        const url = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = `专利分析报告_${task.title}.pdf`
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        window.URL.revokeObjectURL(url)
      } catch (error) {
        console.error('下载失败:', error)
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

      const input: CreateTaskInput = task.type === 'patent' ? { patentNumber: task.title } : { file: undefined }
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
