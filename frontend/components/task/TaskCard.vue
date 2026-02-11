<template>
  <div class="task-card" :class="`status-${displayStatus}`">
    <div class="task-content">
      <div class="task-icon" :class="displayStatus">
        <svg
          v-if="displayStatus === 'processing'"
          class="animate-spin w-5 h-5"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
        >
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        <CheckCircleIcon v-else-if="displayStatus === 'completed'" class="w-5 h-5" />
        <ClockIcon v-else-if="displayStatus === 'pending'" class="w-5 h-5" />
        <XCircleIcon v-else class="w-5 h-5" />
      </div>

      <div class="task-info">
        <h4 class="task-title">{{ task.title }}</h4>
        <div class="task-meta">
          <span class="task-type">{{ task.type === 'patent' ? '专利号' : 'PDF 文件' }}</span>
          <span class="task-time">{{ formatTime(task.createdAt) }}</span>
        </div>

        <div v-if="task.pn" class="task-pn">
          <span class="pn-label">专利号</span>
          <code class="pn-value">{{ task.pn }}</code>
        </div>

        <div v-if="displayStatus === 'processing'" class="task-progress">
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: `${task.progress}%` }" />
          </div>
          <span class="progress-text">{{ task.progress }}% - {{ task.currentStep }}</span>
        </div>

        <div v-else-if="displayStatus === 'error' && task.error" class="task-error">
          <ExclamationCircleIcon class="w-4 h-4 flex-shrink-0" />
          <span>{{ task.error }}</span>
        </div>
      </div>

      <div class="task-actions" @click.stop>
        <button
          v-if="displayStatus === 'completed' && task.downloadUrl"
          class="action-btn primary"
          @click="download"
          title="下载报告"
        >
          <ArrowDownTrayIcon class="w-4 h-4" />
        </button>

        <button v-if="displayStatus === 'error'" class="action-btn secondary" @click="retry" title="重试">
          <ArrowPathIcon class="w-4 h-4" />
        </button>

        <button class="action-btn danger" @click="deleteTask" title="删除">
          <TrashIcon class="w-4 h-4" />
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  ArrowDownTrayIcon,
  ArrowPathIcon,
  CheckCircleIcon,
  ClockIcon,
  ExclamationCircleIcon,
  TrashIcon,
  XCircleIcon,
} from '@heroicons/vue/24/outline'
import { useTaskStore } from '~/stores/task'
import type { Task } from '~/types/task'

const props = defineProps<{ task: Task }>()
const taskStore = useTaskStore()

const displayStatus = computed(() => {
  if (props.task.status === 'failed' || props.task.status === 'cancelled') return 'error'
  return props.task.status
})

const formatTime = (timestamp: number): string => {
  const now = Date.now()
  const diff = now - timestamp
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  const date = new Date(timestamp)
  return `${date.getMonth() + 1}/${date.getDate()} ${date.getHours()}:${date.getMinutes().toString().padStart(2, '0')}`
}

const download = () => taskStore.downloadResult(props.task)
const retry = () => taskStore.retryTask(props.task)
const deleteTask = () => {
  if (confirm('确定要删除这个任务吗？')) taskStore.deleteTask(props.task.id)
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
.task-pn {
  @apply flex items-center gap-2 mb-2;
}
.pn-label {
  @apply text-xs text-gray-500;
}
.pn-value {
  @apply text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-700 break-all;
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
