import { defineStore } from 'pinia'
import type { Task, CreateTaskInput, TaskProgress } from '~/types/task'

const generateId = () => Math.random().toString(36).substr(2, 9)

const STORAGE_KEY = 'patent_tasks'

export const useTaskStore = defineStore('tasks', {
  state: () => ({
    tasks: [] as Task[],
    loading: false,
  }),

  getters: {
    // 按状态分组
    groupedTasks: (state) => {
      return {
        processing: state.tasks.filter(t => t.status === 'processing'),
        pending: state.tasks.filter(t => t.status === 'pending'),
        completed: state.tasks.filter(t => t.status === 'completed'),
        error: state.tasks.filter(t => t.status === 'error'),
      }
    },
    
    // 是否有进行中的任务
    hasProcessingTasks: (state) => 
      state.tasks.some(t => t.status === 'processing'),

    // 已完成任务数
    completedCount: (state) => 
      state.tasks.filter(t => t.status === 'completed').length,

    // 按创建时间倒序排列的任务
    sortedTasks: (state) => {
      const statusOrder = { processing: 0, pending: 1, completed: 2, error: 3 }
      return [...state.tasks].sort((a, b) => {
        return statusOrder[a.status] - statusOrder[b.status] || 
               b.createdAt - a.createdAt
      })
    },
  },

  actions: {
    // 初始化：从localStorage加载
    init() {
      if (process.client) {
        const saved = localStorage.getItem(STORAGE_KEY)
        if (saved) {
          try {
            this.tasks = JSON.parse(saved)
            // 恢复进行中的任务状态
            this.restoreProcessingTasks()
          } catch (e) {
            console.error('Failed to parse tasks from storage:', e)
          }
        }
      }
    },

    // 创建新任务
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
      
      // 提交到后端
      await this.submitTask(task, input)
      
      return task
    },

    // 提交任务到后端
    async submitTask(task: Task, input: CreateTaskInput) {
      const config = useRuntimeConfig()
      
      try {
        const formData = new FormData()
        if (input.patentNumber) {
          formData.append('patentNumber', input.patentNumber)
        }
        if (input.file) {
          formData.append('file', input.file)
        }

        const response = await fetch(`${config.public.apiBaseUrl}/api/tasks`, {
          method: 'POST',
          body: formData,
        })

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`)
        }

        const data = await response.json()
        
        // 更新任务ID并开始监听进度
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

    // 实时进度追踪（SSE）
    startProgressTracking(task: Task) {
      const config = useRuntimeConfig()
      
      if (!task.backendId || task.status === 'completed' || task.status === 'error') {
        return
      }

      const eventSource = new EventSource(`${config.public.apiBaseUrl}/api/tasks/${task.backendId}/progress`)
      
      eventSource.onmessage = (event) => {
        try {
          const data: TaskProgress = JSON.parse(event.data)
          
          task.progress = data.progress
          task.currentStep = data.step
          task.status = data.status
          task.updatedAt = Date.now()
          
          if (data.status === 'completed') {
            task.downloadUrl = data.downloadUrl
            eventSource.close()
          } else if (data.status === 'error') {
            task.error = data.error
            eventSource.close()
          }
          
          this.saveToStorage()
        } catch (e) {
          console.error('Failed to parse progress data:', e)
        }
      }
      
      eventSource.onerror = () => {
        // 连接错误，3秒后重试
        eventSource.close()
        setTimeout(() => {
          if (task.status === 'processing') {
            this.startProgressTracking(task)
          }
        }, 3000)
      }
    },

    // 删除任务
    deleteTask(taskId: string) {
      const index = this.tasks.findIndex(t => t.id === taskId)
      if (index > -1) {
        this.tasks.splice(index, 1)
        this.saveToStorage()
      }
    },

    // 清空所有任务
    clearAllTasks() {
      this.tasks = []
      if (process.client) {
        localStorage.removeItem(STORAGE_KEY)
      }
    },

    // 下载结果
    async downloadResult(task: Task) {
      if (!task.downloadUrl) return
      
      const config = useRuntimeConfig()
      // 构建完整的下载 URL
      const baseUrl = config.public.apiBaseUrl
      const downloadUrl = task.downloadUrl.startsWith('http') 
        ? task.downloadUrl 
        : `${baseUrl}${task.downloadUrl}`
      
      try {
        // 使用 fetch 获取文件，然后创建下载链接
        const response = await fetch(downloadUrl)
        
        if (!response.ok) {
          throw new Error(`下载失败: ${response.status} ${response.statusText}`)
        }
        
        // 获取文件 blob
        const blob = await response.blob()
        
        // 创建临时下载链接
        const url = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = url
        link.download = `专利分析报告_${task.title}.pdf`
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        
        // 清理临时 URL
        window.URL.revokeObjectURL(url)
        
      } catch (error) {
        console.error('下载失败:', error)
        // 如果 fetch 失败，回退到直接打开链接的方式
        window.open(downloadUrl, '_blank')
      }
    },

    // 重试任务
    async retryTask(task: Task) {
      // 重置任务状态
      task.status = 'pending'
      task.progress = 0
      task.currentStep = '等待处理'
      task.error = undefined
      task.updatedAt = Date.now()
      this.saveToStorage()

      // 重新提交
      const input: CreateTaskInput = task.type === 'patent' 
        ? { patentNumber: task.title }
        : { file: undefined } // 文件需要重新上传
      
      await this.submitTask(task, input)
    },

    // 持久化到localStorage
    saveToStorage() {
      if (process.client) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(this.tasks))
      }
    },

    // 恢复进行中的任务状态
    restoreProcessingTasks() {
      this.tasks.forEach(task => {
        if (task.status === 'processing' && task.backendId) {
          this.startProgressTracking(task)
        }
      })
    },
  },
})
