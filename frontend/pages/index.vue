<template>
  <div class="min-h-screen flex flex-col bg-gradient-to-b from-slate-50 via-blue-50 to-indigo-50 relative overflow-hidden">
    <div class="absolute inset-0 pointer-events-none">
      <div class="absolute -top-40 left-1/2 -translate-x-1/2 w-[1000px] h-[1000px] bg-gradient-to-br from-cyan-200/30 via-blue-200/20 to-purple-100/20 rounded-full blur-3xl"></div>
      <div class="absolute top-32 right-[-100px] w-96 h-96 bg-gradient-to-br from-sky-200/25 to-indigo-200/25 rounded-full blur-3xl"></div>
      <div class="absolute bottom-[-150px] left-[-150px] w-[500px] h-[500px] bg-gradient-to-tr from-blue-200/20 to-cyan-200/20 rounded-full blur-3xl"></div>
    </div>

    <main class="relative z-10 flex-1 flex flex-col items-center px-4 py-16 md:py-24">
      <!-- 服务状态和统计信息 - 调整到上传区域下方 -->

      <div class="mb-8">
        <div class="inline-flex items-center gap-2 px-5 py-3 bg-gradient-to-r from-blue-500/10 to-indigo-500/10 border border-blue-200/50 rounded-full backdrop-blur-sm">
          <BoltIcon class="w-5 h-5 text-blue-600" />
          <span class="text-sm font-semibold text-blue-700">AI 驱动的智能分析</span>
        </div>
      </div>

      <h1 class="text-5xl md:text-7xl font-bold text-center text-slate-900 mb-6 tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-slate-900 to-slate-700">
        专利智能分析平台
      </h1>

      <p class="text-xl md:text-2xl text-center text-slate-600 mb-12 max-w-2xl leading-relaxed">
        输入专利公开号或上传 PDF，系统自动完成解析、检索与报告生成，让专利分析更简单。
      </p>

      <div class="w-full max-w-xl space-y-6">
        <div class="bg-white/80 backdrop-blur-xl rounded-3xl shadow-xl shadow-slate-200/60 border border-slate-100/50 p-3">
          <div class="flex items-center gap-2">
            <div class="pl-4 text-slate-400">
              <MagnifyingGlassIcon class="w-6 h-6" />
            </div>
            <input
              v-model="patentNumber"
              type="text"
              placeholder="请输入专利公开号（如：CN116745575A）"
              class="flex-1 py-4 px-2 text-slate-900 placeholder-slate-400 bg-transparent border-none outline-none text-base"
              @keyup.enter="submitPatent"
            />
            <button
              :disabled="!patentNumber.trim() || loading"
              class="px-8 py-3.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 disabled:from-blue-300 disabled:to-blue-300 text-white font-semibold rounded-2xl transition-all duration-300 flex items-center gap-2 shadow-lg shadow-blue-600/30 hover:shadow-xl hover:-translate-y-0.5"
              @click="submitPatent"
            >
              <span v-if="loading" class="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
              <span>开始分析</span>
            </button>
          </div>
        </div>

        <div class="flex items-center gap-4">
          <div class="flex-1 h-px bg-gradient-to-r from-transparent via-slate-300 to-transparent"></div>
          <span class="text-sm text-slate-400 font-semibold">或</span>
          <div class="flex-1 h-px bg-gradient-to-r from-transparent via-slate-300 to-transparent"></div>
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
              'relative border-2 border-dashed rounded-3xl p-10 text-center transition-all duration-300 cursor-pointer overflow-hidden backdrop-blur-sm',
              isDragging ? 'border-blue-500 bg-blue-50/80' : 'border-slate-300/70 hover:border-slate-400 hover:bg-slate-50/80',
            ]"
            @click="!selectedFile && $refs.fileInput?.click()"
          >
            <div
              v-if="isDragging"
              class="absolute inset-0 bg-gradient-to-br from-blue-200/30 to-indigo-200/30 animate-pulse"
            ></div>

            <div class="relative z-10">
              <div v-if="!selectedFile" class="space-y-4">
                <div class="mx-auto w-20 h-20 rounded-3xl bg-gradient-to-br from-slate-100 to-slate-200 flex items-center justify-center group-hover:from-slate-200 group-hover:to-slate-300 transition-all duration-300">
                  <DocumentArrowUpIcon class="w-10 h-10 text-slate-400" />
                </div>
                <div>
                  <p class="text-lg font-semibold text-slate-900">拖拽 PDF 文件到此处</p>
                  <p class="text-sm text-slate-500 mt-1">或点击选择文件</p>
                </div>
                <p class="text-xs text-slate-400">支持 .pdf 格式，最大 50MB</p>
              </div>

              <div v-else class="flex items-center gap-4 p-6 bg-white/90 rounded-2xl shadow-sm border border-slate-100">
                <div class="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-100 to-indigo-100 flex items-center justify-center flex-shrink-0">
                  <DocumentTextIcon class="w-8 h-8 text-blue-600" />
                </div>
                <div class="flex-1 min-w-0 text-left">
                  <p class="text-base font-semibold text-slate-900 truncate">{{ selectedFile.name }}</p>
                  <p class="text-sm text-slate-500">{{ formatFileSize(selectedFile.size) }}</p>
                </div>
                <button
                  type="button"
                  class="p-3 hover:bg-slate-100 rounded-xl transition-all duration-200"
                  @click.stop="clearFile"
                >
                  <XMarkIcon class="w-6 h-6 text-slate-400" />
                </button>
              </div>
            </div>
          </div>
        </div>

        <button
          v-if="selectedFile"
          :disabled="loading"
          class="w-full py-5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 disabled:from-blue-300 disabled:to-blue-300 text-white font-semibold rounded-2xl transition-all duration-300 flex items-center justify-center gap-2 shadow-xl shadow-blue-600/30 hover:shadow-2xl hover:-translate-y-1"
          @click="submitFile"
        >
          <span v-if="loading" class="w-6 h-6 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
          <span>上传并分析</span>
        </button>
      </div>

      <!-- 服务状态和统计信息 -->
      <div class="w-full max-w-6xl mt-10 flex flex-wrap justify-center gap-5">
        <div class="inline-flex items-center gap-2 rounded-full border border-emerald-200/80 bg-emerald-50/80 px-5 py-3 text-sm text-emerald-700 backdrop-blur-sm">
          <span class="inline-block w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse"></span>
          服务状态正常
        </div>

        <div class="flex items-center gap-3 rounded-2xl border border-slate-200/80 bg-white/90 backdrop-blur-xl px-6 py-3">
          <CheckCircleIcon class="w-5 h-5 text-blue-600" />
          <span class="text-sm text-slate-500">已分析专利数</span>
          <span class="text-xl font-bold text-slate-900 tabular-nums">{{ analyzedPatentCountText }}</span>
        </div>
      </div>

      <!-- 核心特性卡片 -->
      <div class="w-full max-w-7xl mt-24">
        <h2 class="text-3xl md:text-4xl font-bold text-center text-slate-900 mb-12 bg-clip-text text-transparent bg-gradient-to-r from-slate-900 to-slate-700">核心功能</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
          <div class="bg-white/90 backdrop-blur-xl rounded-3xl p-8 shadow-xl shadow-slate-200/50 border border-slate-100/50 hover:shadow-2xl hover:-translate-y-2 transition-all duration-300">
            <div class="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-100 to-blue-200 flex items-center justify-center mb-6">
              <SparklesIcon class="w-8 h-8 text-blue-600" />
            </div>
            <h3 class="text-2xl font-semibold text-slate-900 mb-3">智能解析</h3>
            <p class="text-slate-600 leading-relaxed">自动识别专利结构，提取关键信息，生成结构化数据，包括技术领域、问题、方案和效果分析</p>
          </div>
          <div class="bg-white/90 backdrop-blur-xl rounded-3xl p-8 shadow-xl shadow-slate-200/50 border border-slate-100/50 hover:shadow-2xl hover:-translate-y-2 transition-all duration-300">
            <div class="w-16 h-16 rounded-2xl bg-gradient-to-br from-green-100 to-green-200 flex items-center justify-center mb-6">
              <MagnifyingGlassIcon class="w-8 h-8 text-green-600" />
            </div>
            <h3 class="text-2xl font-semibold text-slate-900 mb-3">形式缺陷审查</h3>
            <p class="text-slate-600 leading-relaxed">检查专利文件的形式缺陷，验证附图标记一致性，提高申请质量</p>
          </div>
          <div class="bg-white/90 backdrop-blur-xl rounded-3xl p-8 shadow-xl shadow-slate-200/50 border border-slate-100/50 hover:shadow-2xl hover:-translate-y-2 transition-all duration-300">
            <div class="w-16 h-16 rounded-2xl bg-gradient-to-br from-purple-100 to-purple-200 flex items-center justify-center mb-6">
              <DocumentTextIcon class="w-8 h-8 text-purple-600" />
            </div>
            <h3 class="text-2xl font-semibold text-slate-900 mb-3">检索策略建议</h3>
            <p class="text-slate-600 leading-relaxed">基于技术方案拆解的核心概念、多语言扩展词表及关联分类号，生成专业检索策略</p>
          </div>
          <div class="bg-white/90 backdrop-blur-xl rounded-3xl p-8 shadow-xl shadow-slate-200/50 border border-slate-100/50 hover:shadow-2xl hover:-translate-y-2 transition-all duration-300">
            <div class="w-16 h-16 rounded-2xl bg-gradient-to-br from-orange-100 to-orange-200 flex items-center justify-center mb-6">
              <BoltIcon class="w-8 h-8 text-orange-600" />
            </div>
            <h3 class="text-2xl font-semibold text-slate-900 mb-3">技术特征分析</h3>
            <p class="text-slate-600 leading-relaxed">提取技术特征列表，识别区别特征和前序特征，分析技术效果和贡献度</p>
          </div>
          <div class="bg-white/90 backdrop-blur-xl rounded-3xl p-8 shadow-xl shadow-slate-200/50 border border-slate-100/50 hover:shadow-2xl hover:-translate-y-2 transition-all duration-300">
            <div class="w-16 h-16 rounded-2xl bg-gradient-to-br from-teal-100 to-teal-200 flex items-center justify-center mb-6">
              <CheckCircleIcon class="w-8 h-8 text-teal-600" />
            </div>
            <h3 class="text-2xl font-semibold text-slate-900 mb-3">图解说明</h3>
            <p class="text-slate-600 leading-relaxed">自动处理专利附图，提取部件信息，生成详细的图解说明和部件清单</p>
          </div>
          <div class="bg-white/90 backdrop-blur-xl rounded-3xl p-8 shadow-xl shadow-slate-200/50 border border-slate-100/50 hover:shadow-2xl hover:-translate-y-2 transition-all duration-300">
            <div class="w-16 h-16 rounded-2xl bg-gradient-to-br from-red-100 to-red-200 flex items-center justify-center mb-6">
              <DocumentArrowUpIcon class="w-8 h-8 text-red-600" />
            </div>
            <h3 class="text-2xl font-semibold text-slate-900 mb-3">报告生成</h3>
            <p class="text-slate-600 leading-relaxed">自动生成专业的专利分析报告、形式缺陷审查报告和检索策略建议书</p>
          </div>
        </div>
      </div>

      <!-- 用户评价卡片 -->
      <div class="w-full max-w-7xl mt-24">
        <h2 class="text-3xl md:text-4xl font-bold text-center text-slate-900 mb-12 bg-clip-text text-transparent bg-gradient-to-r from-slate-900 to-slate-700">用户评价</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8">
          <div class="bg-white/90 backdrop-blur-xl rounded-3xl p-8 shadow-xl shadow-slate-200/50 border border-slate-100/50 hover:shadow-2xl transition-all duration-300">
            <div class="flex items-center gap-2 mb-6">
              <span class="text-yellow-400 text-xl">★★★★★</span>
            </div>
            <p class="text-slate-600 mb-6 leading-relaxed">"系统分析速度快，报告内容详细，特别是技术特征提取功能，大大提高了我们的工作效率。"</p>
            <div class="flex items-center gap-4">
              <div class="w-12 h-12 rounded-full bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center text-white font-semibold">张</div>
              <div>
                <p class="text-base font-semibold text-slate-900">张明</p>
                <p class="text-sm text-slate-500">某科技公司专利工程师</p>
              </div>
            </div>
          </div>
          <div class="bg-white/90 backdrop-blur-xl rounded-3xl p-8 shadow-xl shadow-slate-200/50 border border-slate-100/50 hover:shadow-2xl transition-all duration-300">
            <div class="flex items-center gap-2 mb-6">
              <span class="text-yellow-400 text-xl">★★★★★</span>
            </div>
            <p class="text-slate-600 mb-6 leading-relaxed">"界面简洁易用，分析结果准确，形式缺陷审查功能帮助我们提前发现问题，是专利分析的好帮手。"</p>
            <div class="flex items-center gap-4">
              <div class="w-12 h-12 rounded-full bg-gradient-to-br from-green-500 to-green-600 flex items-center justify-center text-white font-semibold">李</div>
              <div>
                <p class="text-base font-semibold text-slate-900">李华</p>
                <p class="text-sm text-slate-500">某高校研究员</p>
              </div>
            </div>
          </div>
          <div class="bg-white/90 backdrop-blur-xl rounded-3xl p-8 shadow-xl shadow-slate-200/50 border border-slate-100/50 hover:shadow-2xl transition-all duration-300">
            <div class="flex items-center gap-2 mb-6">
              <span class="text-yellow-400 text-xl">★★★★☆</span>
            </div>
            <p class="text-slate-600 mb-6 leading-relaxed">"功能强大，报告质量高，检索策略建议很专业，但希望能增加更多导出格式。"</p>
            <div class="flex items-center gap-4">
              <div class="w-12 h-12 rounded-full bg-gradient-to-br from-purple-500 to-purple-600 flex items-center justify-center text-white font-semibold">王</div>
              <div>
                <p class="text-base font-semibold text-slate-900">王强</p>
                <p class="text-sm text-slate-500">知识产权代理师</p>
              </div>
            </div>
          </div>
          <div class="bg-white/90 backdrop-blur-xl rounded-3xl p-8 shadow-xl shadow-slate-200/50 border border-slate-100/50 hover:shadow-2xl transition-all duration-300">
            <div class="flex items-center gap-2 mb-6">
              <span class="text-yellow-400 text-xl">★★★★★</span>
            </div>
            <p class="text-slate-600 mb-6 leading-relaxed">"自动化分析流程节省了大量时间，图解说明功能让专利内容更易懂，值得推荐。"</p>
            <div class="flex items-center gap-4">
              <div class="w-12 h-12 rounded-full bg-gradient-to-br from-orange-500 to-orange-600 flex items-center justify-center text-white font-semibold">刘</div>
              <div>
                <p class="text-base font-semibold text-slate-900">刘芳</p>
                <p class="text-sm text-slate-500">企业知识产权总监</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 页面底部区域 -->
      <footer class="w-full max-w-7xl mt-16 mb-6 text-center text-slate-500 text-sm">
        <div class="bg-white/90 backdrop-blur-xl rounded-3xl p-6 shadow-xl shadow-slate-200/50 border border-slate-100/50">
          <p>© 2026 专利智能分析平台. All rights reserved.</p>
        </div>
      </footer>

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

<style scoped>
</style>
