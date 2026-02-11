<template>
  <div
    class="task-panel"
    :class="{
      expanded: isExpanded,
      'has-tasks': taskStore.tasks.length > 0,
      'has-processing': taskStore.hasProcessingTasks,
    }"
  >
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
          全部下载
        </button>
        <button class="action-btn danger" @click="confirmClearAll">
          <TrashIcon class="w-4 h-4" />
          清空
        </button>
      </div>
    </div>

    <div v-show="isExpanded" class="panel-content custom-scrollbar">
      <div v-if="taskStore.tasks.length === 0" class="empty-state">
        <InboxIcon class="w-16 h-16 text-gray-200 mb-4" />
        <p class="text-gray-500 font-medium">暂无任务</p>
        <p class="text-sm text-gray-400 mt-1">输入专利号或上传 PDF 文件开始分析</p>
      </div>
      <div v-else class="task-list">
        <TaskCard v-for="task in taskStore.sortedTasks" :key="task.id" :task="task" />
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
                  注意：有 <strong>{{ taskStore.groupedTasks.processing.length }}</strong> 个任务正在处理中，清空会中断这些任务。
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
  @apply fixed bottom-0 left-0 right-0 bg-white border-t border-gray-200 shadow-lg z-50;
  max-height: 70vh;
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.task-panel:not(.expanded):not(.has-tasks) {
  transform: translateY(100%);
}
.task-panel:not(.expanded).has-tasks {
  transform: translateY(calc(100% - 64px));
}
.panel-header {
  @apply flex items-center justify-between px-6 py-4 cursor-pointer hover:bg-gray-50 transition-colors;
}
.header-left {
  @apply flex items-center gap-3;
}
.expand-icon {
  @apply w-5 h-5 text-gray-400 transition-transform duration-200;
}
.expand-icon.rotated {
  @apply rotate-180;
}
.panel-title {
  @apply font-semibold text-gray-900 flex items-center gap-2;
}
.badge {
  @apply px-2.5 py-0.5 text-xs font-medium rounded-full;
}
.badge.processing {
  @apply bg-blue-100 text-blue-700;
}
.badge.completed {
  @apply bg-green-100 text-green-700;
}
.header-right {
  @apply flex items-center gap-2;
}
.action-btn {
  @apply flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors;
}
.action-btn.danger {
  @apply text-red-600 border-red-200 hover:bg-red-50;
}
.panel-content {
  @apply overflow-y-auto;
  max-height: calc(70vh - 64px);
}
.empty-state {
  @apply flex flex-col items-center justify-center py-16 text-center;
}
.task-list {
  @apply p-6 space-y-4;
}
.modal-overlay {
  @apply fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-4;
  backdrop-filter: blur(4px);
}
.modal-content {
  @apply bg-white rounded-2xl shadow-2xl max-w-md w-full overflow-hidden;
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
  @apply flex items-center gap-4 p-6 border-b border-gray-100;
}
.modal-icon {
  @apply w-12 h-12 rounded-full flex items-center justify-center flex-shrink-0;
}
.modal-icon.warning {
  @apply bg-yellow-100 text-yellow-600;
}
.modal-title {
  @apply text-lg font-semibold text-gray-900;
}
.modal-body {
  @apply p-6;
}
.warning-box {
  @apply flex items-start gap-3 p-4 bg-yellow-50 border border-yellow-200 rounded-lg text-sm text-yellow-800;
}
.modal-footer {
  @apply flex items-center justify-end gap-3 p-6 border-t border-gray-100 bg-gray-50;
}
.btn-secondary {
  @apply px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors;
}
.btn-danger {
  @apply px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
