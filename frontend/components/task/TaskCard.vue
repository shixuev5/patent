<template>
  <div 
    class="task-card"
    :class="`status-${task.status}`"
  >
    <div class="task-content">
      <!-- Icon -->
      <div class="task-icon" :class="task.status">
        <svg v-if="task.status === 'processing'" class="animate-spin w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        <CheckCircleIcon v-else-if="task.status === 'completed'" class="w-5 h-5" />
        <ClockIcon v-else-if="task.status === 'pending'" class="w-5 h-5" />
        <XCircleIcon v-else-if="task.status === 'error'" class="w-5 h-5" />
      </div>
      
      <!-- Info -->
      <div class="task-info">
        <h4 class="task-title">{{ task.title }}</h4>
        <div class="task-meta">
          <span class="task-type">{{ task.type === 'patent' ? '专利号' : 'PDF文件' }}</span>
          <span class="task-time">{{ formatTime(task.createdAt) }}</span>
        </div>
        
        <!-- Progress Bar -->
        <div v-if="task.status === 'processing'" class="task-progress">
          <div class="progress-bar">
            <div 
              class="progress-fill"
              :style="{ width: `${task.progress}%` }"
            ></div>
          </div>
          <span class="progress-text">{{ task.progress }}% - {{ task.currentStep }}</span>
        </div>
        
        <!-- Error Message -->
        <div v-else-if="task.status === 'error' && task.error" class="task-error">
          <ExclamationCircleIcon class="w-4 h-4 flex-shrink-0" />
          <span>{{ task.error }}</span>
        </div>
      </div>
      
      <!-- Actions -->
      <div class="task-actions" @click.stop>
        <!-- Download Button -->
        <button
          v-if="task.status === 'completed' && task.downloadUrl"
          class="action-btn primary"
          @click="download"
          title="下载报告"
        >
          <ArrowDownTrayIcon class="w-4 h-4" />
        </button>
        
        <!-- Retry Button -->
        <button
          v-if="task.status === 'error'"
          class="action-btn secondary"
          @click="retry"
          title="重试"
        >
          <ArrowPathIcon class="w-4 h-4" />
        </button>
        
        <!-- Delete Button -->
        <button
          class="action-btn danger"
          @click="deleteTask"
          title="删除"
        >
          <TrashIcon class="w-4 h-4" />
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  CheckCircleIcon,
  ClockIcon,
  XCircleIcon,
  ArrowDownTrayIcon,
  ArrowPathIcon,
  TrashIcon,
  ExclamationCircleIcon
} from '@heroicons/vue/24/outline'
import type { Task } from '~/types/task'
import { useTaskStore } from '~/stores/task'

const props = defineProps<{
  task: Task
}>()

const taskStore = useTaskStore()

const formatTime = (timestamp: number): string => {
  const now = Date.now()
  const diff = now - timestamp
  
  // 小于1分钟
  if (diff < 60000) {
    return '刚刚'
  }
  
  // 小于1小时
  if (diff < 3600000) {
    return `${Math.floor(diff / 60000)} 分钟前`
  }
  
  // 小于24小时
  if (diff < 86400000) {
    return `${Math.floor(diff / 3600000)} 小时前`
  }
  
  // 大于24小时，显示日期
  const date = new Date(timestamp)
  return `${date.getMonth() + 1}/${date.getDate()} ${date.getHours()}:${date.getMinutes().toString().padStart(2, '0')}`
}

const download = () => {
  taskStore.downloadResult(props.task)
}

const retry = () => {
  taskStore.retryTask(props.task)
}

const deleteTask = () => {
  if (confirm('确定要删除这个任务吗？')) {
    taskStore.deleteTask(props.task.id)
  }
}
</script>

<style scoped>
.task-card {
  @apply bg-white rounded-xl border border-gray-200 overflow-hidden transition-all duration-200 hover:shadow-md;
}

.task-card.status-processing {
  @apply border-blue-300 ring-1 ring-blue-100;
}

.task-card.status-completed {
  @apply border-green-200;
}

.task-card.status-error {
  @apply border-red-200;
}

.task-content {
  @apply flex items-start gap-4 p-4;
}

.task-icon {
  @apply w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0;
}

.task-icon.processing {
  @apply bg-blue-100 text-blue-600;
}

.task-icon.completed {
  @apply bg-green-100 text-green-600;
}

.task-icon.pending {
  @apply bg-gray-100 text-gray-500;
}

.task-icon.error {
  @apply bg-red-100 text-red-600;
}

.task-info {
  @apply flex-1 min-w-0;
}

.task-title {
  @apply text-sm font-semibold text-gray-900 truncate mb-1;
}

.task-meta {
  @apply flex items-center gap-2 text-xs text-gray-500 mb-2;
}

.task-type {
  @apply px-1.5 py-0.5 bg-gray-100 rounded text-gray-600;
}

.task-progress {
  @apply space-y-1.5;
}

.progress-bar {
  @apply h-1.5 bg-gray-100 rounded-full overflow-hidden;
}

.progress-fill {
  @apply h-full bg-blue-500 rounded-full transition-all duration-300;
}

.progress-text {
  @apply text-xs text-gray-500;
}

.task-error {
  @apply flex items-start gap-1.5 text-xs text-red-600 bg-red-50 p-2 rounded-lg;
}

.task-actions {
  @apply flex items-center gap-1 flex-shrink-0;
}

.action-btn {
  @apply p-2 rounded-lg transition-colors;
}

.action-btn.primary {
  @apply text-blue-600 hover:bg-blue-50;
}

.action-btn.secondary {
  @apply text-gray-600 hover:bg-gray-100;
}

.action-btn.danger {
  @apply text-red-600 hover:bg-red-50;
}
</style>
