<template>
  <div class="min-h-screen flex flex-col bg-white relative overflow-hidden">
    <!-- Background -->
    <div class="absolute inset-0 pointer-events-none">
      <div class="absolute top-0 left-1/2 -translate-x-1/2 w-[800px] h-[800px] bg-gradient-to-br from-blue-50 via-indigo-50 to-white rounded-full blur-3xl opacity-60"></div>
      <div class="absolute top-20 right-20 w-64 h-64 bg-gradient-to-br from-blue-100/50 to-indigo-100/50 rounded-full blur-2xl"></div>
      <div class="absolute bottom-40 left-20 w-96 h-96 bg-gradient-to-tr from-blue-50 to-indigo-50 rounded-full blur-3xl opacity-40"></div>
    </div>

    <!-- Main Content -->
    <main class="relative z-10 flex-1 flex flex-col items-center justify-center px-4 py-20">
      <!-- Badge -->
      <div class="mb-8">
        <div class="inline-flex items-center gap-2 px-4 py-2 bg-blue-50 border border-blue-100 rounded-full">
          <SparklesIcon class="w-4 h-4 text-blue-600" />
          <span class="text-sm font-medium text-blue-700">AI 驱动的智能分析</span>
        </div>
      </div>

      <!-- Title -->
      <h1 class="text-5xl md:text-6xl font-bold text-center text-gray-900 mb-6 tracking-tight">
        专利智能分析平台
      </h1>

      <!-- Description -->
      <p class="text-xl text-center text-gray-600 mb-12 max-w-2xl leading-relaxed">
        上传专利文件或输入专利号，AI 自动完成深度分析
        <br class="hidden md:block" />
        生成专业报告，助力研发决策
      </p>

      <!-- Input Section -->
      <div class="w-full max-w-xl space-y-6">
        <!-- Patent Number Input -->
        <div class="bg-white rounded-2xl shadow-lg shadow-gray-200/50 border border-gray-100 p-2">
          <div class="flex items-center gap-2">
            <div class="pl-4 text-gray-400">
              <MagnifyingGlassIcon class="w-5 h-5" />
            </div>
            <input
              v-model="patentNumber"
              type="text"
              placeholder="请输入专利号（如：CN116745575A）"
              class="flex-1 py-4 px-2 text-gray-900 placeholder-gray-400 bg-transparent border-none outline-none text-base"
              @keyup.enter="submitPatent"
            />
            <button
              :disabled="!patentNumber.trim() || loading"
              class="px-6 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white font-medium rounded-xl transition-all duration-200 flex items-center gap-2"
              @click="submitPatent"
            >
              <span v-if="loading" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
              <span>分析</span>
            </button>
          </div>
        </div>

        <!-- Divider -->
        <div class="flex items-center gap-4">
          <div class="flex-1 h-px bg-gray-200"></div>
          <span class="text-sm text-gray-400 font-medium">或</span>
          <div class="flex-1 h-px bg-gray-200"></div>
        </div>

        <!-- File Upload -->
        <div
          class="relative group"
          @dragover.prevent="isDragging = true"
          @dragleave.prevent="isDragging = false"
          @drop.prevent="handleDrop"
        >
          <input
            ref="fileInput"
            type="file"
            accept=".pdf"
            class="hidden"
            @change="handleFileSelect"
          />
          
          <div
            :class="[
              'relative border-2 border-dashed rounded-2xl p-8 text-center transition-all duration-200 cursor-pointer overflow-hidden',
              isDragging 
                ? 'border-blue-500 bg-blue-50' 
                : 'border-gray-300 hover:border-gray-400 hover:bg-gray-50'
            ]"
            @click="!selectedFile && $refs.fileInput?.click()"
          >
            <!-- Background Gradient on Drag -->
            <div 
              v-if="isDragging"
              class="absolute inset-0 bg-gradient-to-br from-blue-100/50 to-indigo-100/50 animate-pulse"
            ></div>
            
            <div class="relative z-10">
              <div v-if="!selectedFile" class="space-y-3">
                <div class="mx-auto w-16 h-16 rounded-2xl bg-gray-100 flex items-center justify-center group-hover:bg-gray-200 transition-colors">
                  <DocumentArrowUpIcon class="w-8 h-8 text-gray-400" />
                </div>
                <div>
                  <p class="text-base font-medium text-gray-900">
                    拖拽PDF文件到此处
                  </p>
                  <p class="text-sm text-gray-500 mt-1">
                    或点击选择文件
                  </p>
                </div>
                <p class="text-xs text-gray-400">
                  支持 .pdf 格式，最大 50MB
                </p>
              </div>
              
              <div v-else class="flex items-center gap-4 p-4 bg-white rounded-xl shadow-sm border border-gray-100">
                <div class="w-12 h-12 rounded-xl bg-blue-100 flex items-center justify-center flex-shrink-0">
                  <DocumentTextIcon class="w-6 h-6 text-blue-600" />
                </div>
                <div class="flex-1 min-w-0 text-left">
                  <p class="text-sm font-medium text-gray-900 truncate">
                    {{ selectedFile.name }}
                  </p>
                  <p class="text-xs text-gray-500">
                    {{ formatFileSize(selectedFile.size) }}
                  </p>
                </div>
                <button
                  type="button"
                  class="p-2 hover:bg-gray-100 rounded-lg transition-colors"
                  @click.stop="clearFile"
                >
                  <XMarkIcon class="w-5 h-5 text-gray-400" />
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- Submit Button for File -->
        <button
          v-if="selectedFile"
          :disabled="loading"
          class="w-full py-4 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white font-medium rounded-xl transition-all duration-200 flex items-center justify-center gap-2 shadow-lg shadow-blue-600/25"
          @click="submitFile"
        >
          <span v-if="loading" class="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
          <span>开始分析</span>
        </button>
      </div>
    </main>

    <!-- Task Panel -->
    <TaskPanel />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { 
  SparklesIcon, 
  MagnifyingGlassIcon,
  DocumentArrowUpIcon,
  DocumentTextIcon,
  XMarkIcon
} from '@heroicons/vue/24/outline'
import TaskPanel from '~/components/task/TaskPanel.vue'
import { useTaskStore } from '~/stores/task'

const taskStore = useTaskStore()

const patentNumber = ref('')
const selectedFile = ref<File | null>(null)
const isDragging = ref(false)
const loading = ref(false)
const fileInput = ref<HTMLInputElement>()

const submitPatent = async () => {
  if (!patentNumber.value.trim()) return
  
  loading.value = true
  try {
    await taskStore.createTask({ patentNumber: patentNumber.value.trim() })
    patentNumber.value = ''
  } finally {
    loading.value = false
  }
}

const handleFileSelect = (event: Event) => {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (file) {
    validateAndSetFile(file)
  }
}

const handleDrop = (event: DragEvent) => {
  isDragging.value = false
  const file = event.dataTransfer?.files[0]
  if (file) {
    validateAndSetFile(file)
  }
}

const validateAndSetFile = (file: File) => {
  if (file.type !== 'application/pdf') {
    alert('请上传 PDF 格式的文件')
    return
  }
  if (file.size > 50 * 1024 * 1024) {
    alert('文件大小不能超过 50MB')
    return
  }
  selectedFile.value = file
}

const clearFile = () => {
  selectedFile.value = null
  if (fileInput.value) {
    fileInput.value.value = ''
  }
}

const submitFile = async () => {
  if (!selectedFile.value) return
  
  loading.value = true
  try {
    await taskStore.createTask({ file: selectedFile.value })
    clearFile()
  } finally {
    loading.value = false
  }
}

const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes'
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i]
}

// 初始化任务存储
onMounted(() => {
  taskStore.init()
})
</script>

<style scoped>
.animate-pulse {
  animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 0.5;
  }
  50% {
    opacity: 0.8;
  }
}
</style>
