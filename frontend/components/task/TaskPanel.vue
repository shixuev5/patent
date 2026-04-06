<template>
  <div
    class="task-panel"
    :class="{
      expanded: isExpanded,
      'has-tasks': taskStore.tasks.length > 0,
      'has-processing': taskStore.hasProcessingTasks,
    }"
  >
    <div class="panel-shell">
      <div class="panel-header" @click="toggleExpand">
        <div class="header-left">
          <ChevronUpIcon class="expand-icon" :class="{ rotated: !isExpanded }" />
          <span class="panel-title">
            任务队列
            <span v-if="taskStore.groupedTasks.processing.length > 0" class="badge processing">
              {{ taskStore.groupedTasks.processing.length }} 进行中
            </span>
            <span v-else-if="taskStore.groupedTasks.completed.length > 0" class="badge completed">
              {{ taskStore.groupedTasks.completed.length }} 已完成
            </span>
          </span>
        </div>

        <div v-if="taskStore.tasks.length > 0" class="header-right" @click.stop>
          <button v-if="taskStore.completedCount > 0" class="action-btn" @click="downloadAll">
            <ArrowDownTrayIcon class="w-4 h-4" />
            <span class="action-label">全部下载</span>
          </button>
          <button class="action-btn danger" @click="confirmClearAll">
            <TrashIcon class="w-4 h-4" />
            <span class="action-label">清空</span>
          </button>
        </div>
      </div>

      <div v-show="isExpanded" class="panel-content custom-scrollbar">
        <div v-if="taskStore.tasks.length === 0" class="empty-state">
          <InboxIcon class="w-14 h-14 text-slate-300 mb-4" />
          <p class="text-slate-600 font-medium">暂无任务</p>
          <p class="text-sm text-slate-500 mt-1">创建 AI 分析、AI 审查或 AI 答复任务后，会在这里显示处理进度</p>
        </div>
        <div v-else class="task-list">
          <TaskCard v-for="task in taskStore.sortedTasks" :key="task.id" :task="task" />
        </div>
      </div>
    </div>

    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showClearConfirm" class="modal-overlay" @click="showClearConfirm = false">
          <div class="modal-content" @click.stop>
            <div class="modal-header">
              <div class="modal-icon warning">
                <ExclamationTriangleIcon class="w-6 h-6" />
              </div>
              <h3 class="modal-title">确认清空所有任务？</h3>
            </div>
            <div class="modal-body">
              <p class="text-gray-600">此操作不可撤销，所有任务记录将被永久删除。</p>
              <div v-if="taskStore.groupedTasks.processing.length > 0" class="warning-box mt-4">
                <ExclamationCircleIcon class="w-5 h-5 flex-shrink-0" />
                <p>
                  当前有 <strong>{{ taskStore.groupedTasks.processing.length }}</strong> 个任务正在处理中，清空时会跳过这些任务。
                </p>
              </div>
            </div>
            <div class="modal-footer">
              <button class="btn-secondary" @click="showClearConfirm = false">取消</button>
              <button class="btn-danger" @click="clearAll">确认清空</button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  ChevronUpIcon,
  ArrowDownTrayIcon,
  TrashIcon,
  InboxIcon,
  ExclamationTriangleIcon,
  ExclamationCircleIcon,
} from '@heroicons/vue/24/outline'
import TaskCard from './TaskCard.vue'
import { useTaskStore } from '~/stores/task'

const taskStore = useTaskStore()
const isExpanded = ref(false)
const showClearConfirm = ref(false)

const toggleExpand = () => {
  isExpanded.value = !isExpanded.value
}

const confirmClearAll = () => {
  showClearConfirm.value = true
}

const clearAll = () => {
  taskStore.clearAllTasks()
  showClearConfirm.value = false
  isExpanded.value = false
}

const downloadAll = () => {
  const completedTasks = taskStore.tasks.filter((t) => t.status === 'completed' && t.downloadUrl)
  completedTasks.forEach((task, index) => {
    setTimeout(() => taskStore.downloadResult(task), index * 500)
  })
}

onMounted(() => {
  if (taskStore.hasProcessingTasks) isExpanded.value = true
})
</script>

<style scoped>
.task-panel {
  @apply fixed bottom-0 left-0 right-0 z-50 px-4 sm:px-6;
  max-height: 70vh;
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.panel-shell {
  @apply mx-auto w-full max-w-6xl overflow-hidden rounded-t-3xl border border-slate-200/90 bg-white/95 shadow-[0_-10px_30px_-18px_rgba(15,23,42,0.5)] backdrop-blur;
}

.task-panel:not(.expanded):not(.has-tasks) {
  transform: translateY(100%);
}
.task-panel:not(.expanded).has-tasks {
  transform: translateY(calc(100% - 64px));
}
.panel-header {
  @apply flex items-center justify-between px-5 py-3 cursor-pointer transition-colors hover:bg-slate-50/80;
  min-height: 64px;
}
.header-left {
  @apply flex items-center gap-3;
}
.expand-icon {
  @apply w-5 h-5 text-slate-400 transition-transform duration-200;
}
.expand-icon.rotated {
  @apply rotate-180;
}
.panel-title {
  @apply flex items-center gap-2 text-sm font-semibold text-slate-900 sm:text-base;
}
.badge {
  @apply rounded-full px-2.5 py-0.5 text-xs font-semibold;
}
.badge.processing {
  @apply bg-cyan-100 text-cyan-700;
}
.badge.completed {
  @apply bg-emerald-100 text-emerald-700;
}
.header-right {
  @apply flex items-center gap-2;
}
.action-btn {
  @apply inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-semibold text-slate-700 transition-colors hover:bg-slate-50;
  white-space: nowrap;
}
.action-btn.danger {
  @apply border-rose-200 text-rose-600 hover:bg-rose-50;
}
.panel-content {
  @apply overflow-y-auto;
  max-height: calc(70vh - 64px);
}
.empty-state {
  @apply flex flex-col items-center justify-center py-14 text-center;
}
.task-list {
  @apply space-y-3 px-4 pb-4 pt-2 sm:px-5 sm:pb-5 sm:pt-2.5;
}
.modal-overlay {
  @apply fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-4;
  backdrop-filter: blur(4px);
}
.modal-content {
  @apply w-full max-w-md overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl;
  animation: modal-in 0.2s ease-out;
}
@keyframes modal-in {
  from {
    opacity: 0;
    transform: scale(0.95);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}
.modal-header {
  @apply flex items-center gap-4 border-b border-slate-100 p-6;
}
.modal-icon {
  @apply w-12 h-12 rounded-full flex items-center justify-center flex-shrink-0;
}
.modal-icon.warning {
  @apply bg-amber-100 text-amber-600;
}
.modal-title {
  @apply text-lg font-semibold text-slate-900;
}
.modal-body {
  @apply p-6;
}
.warning-box {
  @apply flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800;
}
.modal-footer {
  @apply flex items-center justify-end gap-3 border-t border-slate-100 bg-slate-50 p-6;
}
.btn-secondary {
  @apply rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50;
}
.btn-danger {
  @apply rounded-xl bg-rose-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-rose-700;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 640px) {
  .panel-shell {
    @apply rounded-t-2xl;
  }

  .panel-header {
    @apply px-3 py-2.5;
    min-height: 64px;
  }

  .header-left {
    @apply gap-2;
    min-width: 0;
  }

  .panel-title {
    @apply text-sm gap-1.5;
    white-space: nowrap;
  }

  .badge {
    @apply px-2 py-0.5;
    font-size: 11px;
  }

  .header-right {
    @apply gap-1.5;
  }

  .action-btn {
    @apply px-2.5 py-1.5;
  }

  .action-label {
    display: none;
  }

  .task-panel:not(.expanded).has-tasks {
    transform: translateY(calc(100% - 64px));
  }

  .task-list {
    @apply space-y-3 px-4 pb-4 pt-2;
  }
}
</style>
