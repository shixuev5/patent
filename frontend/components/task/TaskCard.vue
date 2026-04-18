<template>
  <div class="task-card" :class="[`status-${displayStatus}`, { 'task-card-menu-open': showMoreActions }]">
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
        <div class="task-top-row">
          <div class="task-summary">
            <h4 class="task-title">{{ task.title }}</h4>
            <div class="task-meta">
              <span class="task-type">{{ taskTypeLabel }}</span>
              <span class="task-time">{{ formatTime(task.createdAt) }}</span>
            </div>

            <div v-if="showPatentNumber" class="task-pn">
              <span class="pn-label">专利号</span>
              <code class="pn-value">{{ task.pn }}</code>
            </div>
          </div>

          <div class="task-actions" @click.stop>
            <template v-if="!useOverflowMenu">
              <button
                v-for="item in actionItems"
                :key="item.key"
                class="action-btn"
                :class="item.toneClass"
                :disabled="item.disabled"
                :title="item.label"
                @click="runInlineAction(item)"
              >
                <svg
                  v-if="item.loading"
                  class="animate-spin w-4 h-4"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                <span v-else-if="item.key === 'search' && !hasLinkedSearchSession" class="search-icon-create">
                  <MagnifyingGlassIcon class="h-4 w-4" />
                  <span class="search-icon-plus-badge">
                    <PlusIcon class="h-2.5 w-2.5" />
                  </span>
                </span>
                <component :is="item.icon" v-else class="w-4 h-4" />
              </button>
            </template>

            <template v-else>
              <button
                v-if="primaryAction"
                class="action-btn"
                :class="primaryAction.toneClass"
                :disabled="primaryAction.disabled"
                :title="primaryAction.label"
                @click="runPrimaryAction"
              >
                <svg
                  v-if="primaryAction.loading"
                  class="animate-spin w-4 h-4"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                <span v-else-if="primaryAction.key === 'search' && !hasLinkedSearchSession" class="search-icon-create">
                  <MagnifyingGlassIcon class="h-4 w-4" />
                  <span class="search-icon-plus-badge">
                    <PlusIcon class="h-2.5 w-2.5" />
                  </span>
                </span>
                <component :is="primaryAction.icon" v-else class="w-4 h-4" />
              </button>

              <div v-if="overflowActions.length > 0" ref="menuRef" class="action-menu">
                <button
                  class="action-btn secondary"
                  type="button"
                  title="更多操作"
                  aria-label="更多操作"
                  :aria-expanded="showMoreActions ? 'true' : 'false'"
                  @click="toggleMoreActions"
                >
                  <EllipsisVerticalIcon class="w-4 h-4" />
                </button>

                <div
                  v-if="showMoreActions"
                  ref="popoverRef"
                  class="action-menu-popover"
                  :class="{ 'action-menu-popover-up': menuDirection === 'up' }"
                >
                  <button
                    v-for="item in overflowActions"
                    :key="item.key"
                    class="action-menu-item"
                    :class="item.toneClass"
                    type="button"
                    :disabled="item.disabled"
                    @click="runOverflowAction(item)"
                  >
                    <component :is="item.icon" class="w-4 h-4" />
                    <span>{{ item.label }}</span>
                  </button>
                </div>
              </div>
            </template>
          </div>
        </div>

        <div v-if="displayStatus === 'processing' || displayStatus === 'pending'" class="task-progress">
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: `${task.progress}%` }" />
          </div>
          <span class="progress-text">{{ task.progress }}% - {{ task.currentStep }}</span>
        </div>

        <div v-else-if="displayStatus === 'error' && task.error" class="task-error">
          <ExclamationCircleIcon class="w-4 h-4 flex-shrink-0" />
          <span class="task-error-text">{{ task.error }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  ArrowDownTrayIcon,
  ArrowPathIcon,
  CheckCircleIcon,
  ClockIcon,
  EllipsisVerticalIcon,
  ExclamationCircleIcon,
  MagnifyingGlassIcon,
  PlusIcon,
  StopCircleIcon,
  TrashIcon,
  XCircleIcon,
} from '@heroicons/vue/24/outline'
import { useRouter } from 'vue-router'
import { useAdminUsageStore } from '~/stores/adminUsage'
import { useAiSearchStore } from '~/stores/aiSearch'
import { useAuthStore } from '~/stores/auth'
import { useTaskStore } from '~/stores/task'
import type { Task } from '~/types/task'

const props = defineProps<{ task: Task }>()
const authStore = useAuthStore()
const adminUsageStore = useAdminUsageStore()
const taskStore = useTaskStore()
const aiSearchStore = useAiSearchStore()
const router = useRouter()
const creatingSearchDraft = ref(false)
const showMoreActions = ref(false)
const menuRef = ref<HTMLElement | null>(null)
const popoverRef = ref<HTMLElement | null>(null)
const viewportWidth = ref(typeof window === 'undefined' ? 1280 : window.innerWidth)
const menuDirection = ref<'up' | 'down'>('down')

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

const linkedSearchSession = computed(() => {
  const sourceTaskId = String(props.task.backendId || '').trim()
  if (!sourceTaskId) return null
  return aiSearchStore.findSessionBySourceTaskId(sourceTaskId)
})

const hasLinkedSearchSession = computed(() => !!linkedSearchSession.value)

const canCreateSearchDraft = computed(() => {
  if (!authStore.isLoggedIn || !adminUsageStore.isAdmin) return false
  if (displayStatus.value !== 'completed' || !props.task.backendId) return false
  if (props.task.taskType === 'patent_analysis') return true
  if (props.task.taskType === 'ai_reply') {
    return !!props.task.aiSearchSeedAvailable || hasLinkedSearchSession.value
  }
  return false
})

const searchActionTitle = computed(() => {
  const sourceLabel = props.task.taskType === 'ai_reply' ? 'AI 答复报告' : 'AI 分析结果'
  return linkedSearchSession.value
    ? '打开已创建的 AI 检索任务'
    : `基于当前${sourceLabel}生成 AI 检索计划`
})

type ActionItem = {
  key: 'download' | 'search' | 'retry' | 'cancel' | 'delete'
  label: string
  icon: any
  toneClass: string
  disabled?: boolean
  loading?: boolean
}

const actionItems = computed<ActionItem[]>(() => {
  const items: ActionItem[] = []

  if (displayStatus.value === 'completed' && props.task.downloadUrl) {
    items.push({
      key: 'download',
      label: '下载报告',
      icon: ArrowDownTrayIcon,
      toneClass: 'primary',
      disabled: taskStore.isDownloading(props.task.id),
      loading: taskStore.isDownloading(props.task.id),
    })
  }

  if (displayStatus.value === 'error' && canRetry.value) {
    items.push({
      key: 'retry',
      label: '重试',
      icon: ArrowPathIcon,
      toneClass: 'secondary',
    })
  }

  if (canCancel.value) {
    items.push({
      key: 'cancel',
      label: '取消',
      icon: StopCircleIcon,
      toneClass: 'warning',
    })
  }

  if (canCreateSearchDraft.value) {
    items.push({
      key: 'search',
      label: hasLinkedSearchSession.value ? '打开 AI 检索' : '生成 AI 检索',
      icon: MagnifyingGlassIcon,
      toneClass: 'search',
      disabled: creatingSearchDraft.value,
      loading: creatingSearchDraft.value,
    })
  }

  if (canDelete.value) {
    items.push({
      key: 'delete',
      label: '删除',
      icon: TrashIcon,
      toneClass: 'danger',
    })
  }

  return items
})

const primaryAction = computed<ActionItem | null>(() => actionItems.value[0] ?? null)
const overflowActions = computed<ActionItem[]>(() => actionItems.value.slice(1))
const useOverflowMenu = computed(() => viewportWidth.value < 768 && actionItems.value.length > 1)

watch(
  canCreateSearchDraft,
  (value) => {
    if (!value) return
    void aiSearchStore.ensureSessionsLoaded()
  },
  { immediate: true },
)

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
const syncMenuDirection = async () => {
  await nextTick()
  if (!menuRef.value || !popoverRef.value || typeof window === 'undefined') return
  const menuRect = menuRef.value.getBoundingClientRect()
  const scrollContainerRect = menuRef.value.closest('.panel-content')?.getBoundingClientRect()
  const bottomLimit = Math.min(scrollContainerRect?.bottom ?? window.innerHeight, window.innerHeight)
  const popoverHeight = popoverRef.value.offsetHeight
  menuDirection.value = menuRect.bottom + 8 + popoverHeight > bottomLimit ? 'up' : 'down'
}

const toggleMoreActions = async () => {
  showMoreActions.value = !showMoreActions.value
  if (showMoreActions.value) await syncMenuDirection()
}

const runAction = async (action: ActionItem) => {
  if (action.disabled) return
  if (action.key === 'download') download()
  else if (action.key === 'retry') retry()
  else if (action.key === 'cancel') cancel()
  else if (action.key === 'search') await openSearchDraft()
  else if (action.key === 'delete') deleteTask()
  showMoreActions.value = false
}

const runPrimaryAction = () => {
  if (!primaryAction.value) return
  void runAction(primaryAction.value)
}

const runInlineAction = (action: ActionItem) => {
  void runAction(action)
}

const runOverflowAction = (action: ActionItem) => {
  void runAction(action)
}

const openSearchDraft = async () => {
  const sourceTaskId = String(props.task.backendId || '').trim()
  if (!sourceTaskId || creatingSearchDraft.value) return
  creatingSearchDraft.value = true
  try {
    await aiSearchStore.ensureSessionsLoaded()
    const linkedSessionId = String(linkedSearchSession.value?.sessionId || '').trim()
    const sessionId = linkedSessionId || (
      props.task.taskType === 'ai_reply'
        ? await aiSearchStore.createSessionFromReply(sourceTaskId)
        : await aiSearchStore.createSessionFromAnalysis(sourceTaskId)
    )
    if (!sessionId) {
      throw new Error('AI 检索计划创建成功，但未返回会话ID。')
    }
    await router.push({
      path: '/search',
      query: { session: sessionId },
    })
  } catch (error) {
    taskStore.showGlobalNotice('error', error instanceof Error ? error.message : '生成 AI 检索计划失败，请稍后重试。')
  } finally {
    creatingSearchDraft.value = false
  }
}
const deleteTask = () => {
  if (confirm('确定要删除这个任务吗？')) taskStore.deleteTask(props.task.id)
}

const handleDocumentClick = (event: MouseEvent) => {
  if (!menuRef.value) return
  if (menuRef.value.contains(event.target as Node)) return
  showMoreActions.value = false
}

const handleWindowResize = () => {
  if (typeof window !== 'undefined') viewportWidth.value = window.innerWidth
  if (showMoreActions.value) void syncMenuDirection()
}

watch(actionItems, () => {
  if (!useOverflowMenu.value || overflowActions.value.length === 0) showMoreActions.value = false
})

watch(useOverflowMenu, (value) => {
  if (!value) showMoreActions.value = false
})

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
  window.addEventListener('resize', handleWindowResize)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
  window.removeEventListener('resize', handleWindowResize)
})
</script>

<style scoped>
.task-card {
  @apply overflow-visible rounded-2xl border border-slate-200/90 bg-white transition-all duration-200 hover:border-slate-300 hover:shadow-sm;
  position: relative;
}
.task-card-menu-open {
  z-index: 40;
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
.task-top-row {
  @apply flex items-start justify-between gap-3;
}
.task-summary {
  @apply min-w-0 flex-1;
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
  @apply mt-3 flex items-start gap-1.5 text-xs text-rose-700;
}
.task-error-text {
  @apply min-w-0 whitespace-pre-wrap break-words;
}
.task-actions {
  @apply flex flex-shrink-0 items-center gap-1.5;
}
.action-menu {
  @apply relative;
}
.action-menu-popover {
  @apply absolute left-auto right-0 top-[calc(100%+0.5rem)] z-30 min-w-36 rounded-xl border border-slate-200 bg-white p-1.5 shadow-lg;
}
.action-menu-popover-up {
  bottom: calc(100% + 0.5rem);
  top: auto;
}
.action-menu-item {
  @apply flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60;
}
.action-btn {
  @apply inline-flex h-9 w-9 items-center justify-center rounded-xl border border-transparent transition-colors disabled:cursor-not-allowed disabled:opacity-60;
}
.action-chip {
  @apply inline-flex h-9 items-center gap-1.5 rounded-xl border px-3 text-[12px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60;
}
.action-btn.search {
  @apply text-cyan-700 hover:border-cyan-100 hover:bg-cyan-50;
}
.search-icon-create {
  @apply relative inline-flex h-4 w-4 items-center justify-center;
}
.search-icon-plus-badge {
  @apply absolute -right-1 -top-1 inline-flex h-3.5 w-3.5 items-center justify-center rounded-full bg-cyan-700 text-white;
}
.action-chip.info {
  @apply border-cyan-200 bg-cyan-50 text-cyan-700 hover:border-cyan-300 hover:bg-cyan-100/70;
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
.action-menu-item.search {
  @apply text-cyan-700 hover:bg-cyan-50;
}
.action-menu-item.primary {
  @apply text-cyan-700 hover:bg-cyan-50;
}
.action-menu-item.secondary {
  @apply text-slate-700 hover:bg-slate-100;
}
.action-menu-item.warning {
  @apply text-amber-700 hover:bg-amber-50;
}
.action-menu-item.danger {
  @apply text-rose-600 hover:bg-rose-50;
}

@media (max-width: 640px) {
  .task-content {
    @apply gap-3 p-3.5;
  }

  .task-top-row {
    @apply gap-2;
  }

  .task-icon {
    @apply h-9 w-9;
  }

  .task-meta {
    @apply flex-wrap gap-y-1;
  }

  .action-btn {
    @apply h-8 w-8 rounded-lg;
  }

  .action-chip {
    @apply h-8 rounded-lg px-2.5 text-[11px];
  }
}
</style>
