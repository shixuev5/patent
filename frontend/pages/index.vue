<template>
  <div class="min-h-screen flex flex-col bg-slate-50 relative overflow-hidden">
    <div class="absolute inset-0 pointer-events-none">
      <div class="absolute -top-36 left-1/2 -translate-x-1/2 w-[980px] h-[980px] bg-gradient-to-br from-cyan-100/50 via-blue-100/40 to-slate-50 rounded-full blur-3xl"></div>
      <div class="absolute top-24 right-[-80px] w-80 h-80 bg-gradient-to-br from-sky-100/40 to-indigo-100/40 rounded-full blur-3xl"></div>
      <div class="absolute bottom-[-120px] left-[-120px] w-96 h-96 bg-gradient-to-tr from-blue-100/30 to-cyan-100/30 rounded-full blur-3xl"></div>
    </div>

    <header class="relative z-20 w-full px-4 pt-5 md:px-8 md:pt-7">
      <div class="mx-auto max-w-6xl rounded-2xl border border-slate-200/70 bg-white/90 backdrop-blur shadow-[0_10px_40px_-20px_rgba(15,23,42,0.45)]">
        <div class="flex flex-col gap-4 p-4 md:flex-row md:items-center md:justify-between md:p-5">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-blue-600 text-white flex items-center justify-center shadow-lg shadow-blue-600/25">
              <SparklesIcon class="w-5 h-5" />
            </div>
            <div>
              <p class="text-sm text-slate-500 tracking-wide">PATENT INTELLIGENCE WORKBENCH</p>
              <p class="text-lg md:text-xl font-semibold text-slate-900">专利智能分析平台</p>
            </div>
          </div>

          <div class="flex flex-wrap items-center gap-3">
            <div class="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-sm text-emerald-700">
              <span class="inline-block w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
              服务状态正常
            </div>

            <div class="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
              <CheckCircleIcon class="w-4 h-4 text-blue-600" />
              <span class="text-xs text-slate-500">已分析专利数</span>
              <span class="text-base font-semibold text-slate-900 tabular-nums">{{ analyzedPatentCountText }}</span>
            </div>
          </div>
        </div>
      </div>
    </header>

    <main class="relative z-10 flex-1 flex flex-col items-center justify-center px-4 py-14 md:py-20">
      <div class="mb-7">
        <div class="inline-flex items-center gap-2 px-4 py-2 bg-blue-50 border border-blue-100 rounded-full">
          <BoltIcon class="w-4 h-4 text-blue-600" />
          <span class="text-sm font-medium text-blue-700">AI 驱动的智能分析</span>
        </div>
      </div>

      <h1 class="text-4xl md:text-6xl font-bold text-center text-slate-900 mb-5 tracking-tight">
        面向研发与审查的
        <br class="hidden md:block" />
        专利分析中枢
      </h1>

      <p class="text-lg md:text-xl text-center text-slate-600 mb-10 max-w-2xl leading-relaxed">
        输入专利号或上传 PDF，系统自动完成结构化解析、检索与报告生成，
        <br class="hidden md:block" />
        让分析流程更快、更稳、更可复用。
      </p>

      <div class="w-full max-w-xl space-y-6">
        <div class="bg-white rounded-2xl shadow-lg shadow-slate-200/50 border border-slate-100 p-2">
          <div class="flex items-center gap-2">
            <div class="pl-4 text-slate-400">
              <MagnifyingGlassIcon class="w-5 h-5" />
            </div>
            <input
              v-model="patentNumber"
              type="text"
              placeholder="请输入专利号（如：CN116745575A）"
              class="flex-1 py-4 px-2 text-slate-900 placeholder-slate-400 bg-transparent border-none outline-none text-base"
              @keyup.enter="submitPatent"
            />
            <button
              :disabled="!patentNumber.trim() || loading"
              class="px-6 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white font-medium rounded-xl transition-all duration-200 flex items-center gap-2"
              @click="submitPatent"
            >
              <span v-if="loading" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
              <span>开始分析</span>
            </button>
          </div>
        </div>

        <div class="flex items-center gap-4">
          <div class="flex-1 h-px bg-slate-200"></div>
          <span class="text-sm text-slate-400 font-medium">或</span>
          <div class="flex-1 h-px bg-slate-200"></div>
        </div>

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
              isDragging ? 'border-blue-500 bg-blue-50' : 'border-slate-300 hover:border-slate-400 hover:bg-slate-50',
            ]"
            @click="!selectedFile && $refs.fileInput?.click()"
          >
            <div
              v-if="isDragging"
              class="absolute inset-0 bg-gradient-to-br from-blue-100/50 to-indigo-100/50 animate-pulse"
            ></div>

            <div class="relative z-10">
              <div v-if="!selectedFile" class="space-y-3">
                <div class="mx-auto w-16 h-16 rounded-2xl bg-slate-100 flex items-center justify-center group-hover:bg-slate-200 transition-colors">
                  <DocumentArrowUpIcon class="w-8 h-8 text-slate-400" />
                </div>
                <div>
                  <p class="text-base font-medium text-slate-900">拖拽 PDF 文件到此处</p>
                  <p class="text-sm text-slate-500 mt-1">或点击选择文件</p>
                </div>
                <p class="text-xs text-slate-400">支持 .pdf 格式，最大 50MB</p>
              </div>

              <div v-else class="flex items-center gap-4 p-4 bg-white rounded-xl shadow-sm border border-slate-100">
                <div class="w-12 h-12 rounded-xl bg-blue-100 flex items-center justify-center flex-shrink-0">
                  <DocumentTextIcon class="w-6 h-6 text-blue-600" />
                </div>
                <div class="flex-1 min-w-0 text-left">
                  <p class="text-sm font-medium text-slate-900 truncate">{{ selectedFile.name }}</p>
                  <p class="text-xs text-slate-500">{{ formatFileSize(selectedFile.size) }}</p>
                </div>
                <button
                  type="button"
                  class="p-2 hover:bg-slate-100 rounded-lg transition-colors"
                  @click.stop="clearFile"
                >
                  <XMarkIcon class="w-5 h-5 text-slate-400" />
                </button>
              </div>
            </div>
          </div>
        </div>

        <button
          v-if="selectedFile"
          :disabled="loading"
          class="w-full py-4 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white font-medium rounded-xl transition-all duration-200 flex items-center justify-center gap-2 shadow-lg shadow-blue-600/25"
          @click="submitFile"
        >
          <span v-if="loading" class="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
          <span>上传并分析</span>
        </button>
      </div>
    </main>

    <TaskPanel />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  SparklesIcon,
  MagnifyingGlassIcon,
  DocumentArrowUpIcon,
  DocumentTextIcon,
  XMarkIcon,
  CheckCircleIcon,
  BoltIcon,
} from '@heroicons/vue/24/outline'
import TaskPanel from '~/components/task/TaskPanel.vue'
import { useTaskStore } from '~/stores/task'

const taskStore = useTaskStore()
const config = useRuntimeConfig()

const patentNumber = ref('')
const selectedFile = ref<File | null>(null)
const isDragging = ref(false)
const loading = ref(false)
const fileInput = ref<HTMLInputElement>()

const analyzedPatentCount = ref<number | null>(null)
let statsTimer: ReturnType<typeof setInterval> | null = null

const analyzedPatentCountText = computed(() => {
  if (analyzedPatentCount.value === null) return '--'
  return new Intl.NumberFormat('zh-CN').format(analyzedPatentCount.value)
})

const fetchHealthStats = async () => {
  try {
    const response = await fetch(`${config.public.apiBaseUrl}/api/health`)
    if (!response.ok) throw new Error(`status: ${response.status}`)
    const data = await response.json()
    analyzedPatentCount.value = Number(data?.statistics?.by_status?.completed ?? 0)
  } catch (error) {
    if (analyzedPatentCount.value === null) analyzedPatentCount.value = 0
    console.error('Failed to fetch health stats:', error)
  }
}

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
  if (file) validateAndSetFile(file)
}

const handleDrop = (event: DragEvent) => {
  isDragging.value = false
  const file = event.dataTransfer?.files[0]
  if (file) validateAndSetFile(file)
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
  if (fileInput.value) fileInput.value.value = ''
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
  return `${Math.round((bytes / Math.pow(k, i)) * 100) / 100} ${sizes[i]}`
}

onMounted(() => {
  taskStore.init()
  fetchHealthStats()
  statsTimer = setInterval(fetchHealthStats, 30000)
})

onBeforeUnmount(() => {
  if (statsTimer) clearInterval(statsTimer)
})
</script>
