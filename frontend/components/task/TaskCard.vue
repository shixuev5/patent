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
          <span class="task-type">{{ taskTypeLabel }}</span>
          <span class="task-time">{{ formatTime(task.createdAt) }}</span>
        </div>

        <div v-if="showPatentNumber" class="task-pn">
          <span class="pn-label">专利号</span>
          <code class="pn-value">{{ task.pn }}</code>
        </div>

        <div v-if="displayStatus === 'processing' || displayStatus === 'pending'" class="task-progress">
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
          :disabled="taskStore.isDownloading(task.id)"
        >
          <svg v-if="taskStore.isDownloading(task.id)" class="animate-spin w-4 h-4 text-blue-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <ArrowDownTrayIcon v-else class="w-4 h-4" />
        </button>

        <button v-if="displayStatus === 'error' && canRetry" class="action-btn secondary" @click="retry" title="重试">
          <ArrowPathIcon class="w-4 h-4" />
        </button>

        <button v-if="canCancel" class="action-btn warning" @click="cancel" title="取消">
          <StopCircleIcon class="w-4 h-4" />
        </button>

        <button v-if="canDelete" class="action-btn danger" @click="deleteTask" title="删除">
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
  StopCircleIcon,
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

const showPatentNumber = computed(() => {
  if (!props.task.pn) return false
  if (props.task.taskType !== 'patent_analysis' && props.task.taskType !== 'ai_review') return false
  return props.task.title !== props.task.pn
})

const taskTypeLabel = computed(() => {
  if (props.task.taskType === 'ai_search') return 'AI 检索'
  if (props.task.taskType === 'ai_reply') return 'AI 答复'
  if (props.task.taskType === 'ai_review') return 'AI 审查'
  return 'AI 分析'
})

const canRetry = computed(() => {
  if (!props.task.backendId) return false
  if (props.task.status !== 'failed' && props.task.status !== 'cancelled') return false
  return props.task.taskType === 'patent_analysis' || props.task.taskType === 'ai_review' || props.task.taskType === 'ai_reply'
})

const canCancel = computed(() => {
  return !!props.task.backendId && (props.task.status === 'pending' || props.task.status === 'processing')
})

const canDelete = computed(() => {
  return props.task.status !== 'pending' && props.task.status !== 'processing'
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
const cancel = () => taskStore.cancelTask(props.task)
const deleteTask = () => {
  if (confirm('确定要删除这个任务吗？')) taskStore.deleteTask(props.task.id)
}
</script>

<style scoped>
.task-card {
  @apply overflow-hidden rounded-2xl border border-slate-200/90 bg-white transition-all duration-200 hover:border-slate-300 hover:shadow-sm;
}
.task-card.status-processing {
  @apply border-cyan-300 ring-1 ring-cyan-100;
}
.task-card.status-completed {
  @apply border-emerald-200;
}
.task-card.status-error {
  @apply border-rose-200;
}
.task-content {
  @apply flex items-start gap-3.5 p-4;
}
.task-icon {
  @apply flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl;
}
.task-icon.processing {
  @apply bg-cyan-100 text-cyan-700;
}
.task-icon.completed {
  @apply bg-emerald-100 text-emerald-700;
}
.task-icon.pending {
  @apply bg-slate-100 text-slate-500;
}
.task-icon.error {
  @apply bg-rose-100 text-rose-600;
}
.task-info {
  @apply flex-1 min-w-0;
}
.task-title {
  @apply mb-1 truncate text-sm font-semibold text-slate-900;
}
.task-meta {
  @apply flex items-center gap-2 text-xs text-slate-500;
}
.task-type {
  @apply rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600;
}
.task-pn {
  @apply mb-2 flex items-center gap-2;
}
.pn-label {
  @apply text-xs text-slate-500;
}
.pn-value {
  @apply break-all rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700;
}
.task-progress {
  @apply mt-2 space-y-1.5;
}
.progress-bar {
  @apply h-1.5 overflow-hidden rounded-full bg-slate-100;
}
.progress-fill {
  @apply h-full rounded-full bg-cyan-600 transition-all duration-300;
}
.progress-text {
  @apply text-xs text-slate-500;
}
.task-error {
  @apply mt-2 flex items-start gap-1.5 text-xs text-rose-700;
}
.task-actions {
  @apply flex flex-shrink-0 items-center gap-1.5;
}
.action-btn {
  @apply inline-flex h-9 w-9 items-center justify-center rounded-xl border border-transparent transition-colors;
}
.action-btn.primary {
  @apply text-cyan-700 hover:border-cyan-100 hover:bg-cyan-50;
}
.action-btn.secondary {
  @apply text-slate-600 hover:border-slate-200 hover:bg-slate-100;
}
.action-btn.warning {
  @apply text-amber-700 hover:border-amber-100 hover:bg-amber-50;
}
.action-btn.danger {
  @apply text-rose-600 hover:border-rose-100 hover:bg-rose-50;
}

@media (max-width: 640px) {
  .task-content {
    @apply gap-3 p-3.5;
  }

  .task-icon {
    @apply h-9 w-9;
  }

  .action-btn {
    @apply h-8 w-8 rounded-lg;
  }
}
</style>
