<template>
  <div class="space-y-4">
    <transition name="toast">
      <div
        v-if="taskStore.globalNotice.show"
        class="fixed top-16 left-1/2 z-50 -translate-x-1/2 rounded-full border px-5 py-3 text-sm font-semibold shadow-lg backdrop-blur-md"
        :class="taskStore.globalNotice.type === 'success'
          ? 'border-emerald-200/70 bg-emerald-50/95 text-emerald-700 shadow-emerald-200/60'
          : taskStore.globalNotice.type === 'error'
          ? 'border-rose-200/70 bg-rose-50/95 text-rose-700 shadow-rose-200/60'
          : 'border-blue-200/70 bg-blue-50/95 text-blue-700 shadow-blue-200/60'"
      >
        {{ taskStore.globalNotice.text }}
      </div>
    </transition>

    <section class="rounded-3xl border border-slate-200 bg-white/92 p-4 shadow-sm shadow-slate-200 sm:p-5">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p class="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-700">AI Task Console</p>
          <h1 class="mt-1 text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">AI 任务台</h1>
          <p class="mt-2 text-xs leading-5 text-slate-600 sm:text-sm">
            {{ modeDescription }}
          </p>
        </div>
      </div>
    </section>

    <section class="rounded-3xl border border-slate-200/90 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
      <div class="inline-flex rounded-2xl border border-slate-200 bg-slate-50 p-1">
        <button
          type="button"
          class="rounded-xl px-4 py-2 text-sm font-semibold transition"
          :class="mode === 'patent_analysis' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'"
          @click="mode = 'patent_analysis'"
        >
          AI 分析
        </button>
        <button
          type="button"
          class="rounded-xl px-4 py-2 text-sm font-semibold transition"
          :class="mode === 'office_action_reply' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'"
          @click="mode = 'office_action_reply'"
        >
          AI 研判
        </button>
      </div>

      <div v-if="mode === 'patent_analysis'" class="mt-4 space-y-4">
        <div>
          <label class="mb-2 block text-sm font-medium text-slate-700">专利公开号</label>
          <input
            v-model="patentNumber"
            type="text"
            placeholder="例如：CN116745575A"
            class="w-full rounded-2xl border border-slate-200 px-4 py-2.5 text-sm text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100"
          />
          <p v-if="patentNumberError" class="mt-2 text-xs text-rose-600">{{ patentNumberError }}</p>
        </div>

        <div>
          <label class="mb-2 block text-sm font-medium text-slate-700">或上传专利 PDF</label>
          <input
            ref="patentFileInput"
            type="file"
            accept=".pdf"
            class="block w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 file:mr-4 file:rounded-xl file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-700 hover:file:bg-slate-200"
            @change="onPatentFileChange"
          />
          <p v-if="patentFileName" class="mt-2 text-xs text-slate-500">已选择：{{ patentFileName }}</p>
        </div>

        <button
          type="button"
          :disabled="loading || !canSubmitPatent"
          class="w-full rounded-2xl bg-cyan-700 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
          @click="submitPatentTask"
        >
          <span v-if="loading">提交中...</span>
          <span v-else>创建 AI 分析任务</span>
        </button>
      </div>

      <div v-else class="mt-4 space-y-4">
        <div class="grid gap-4 sm:grid-cols-2">
          <div>
            <label class="mb-2 block text-sm font-medium text-slate-700">
              <span class="mr-1 text-rose-500">*</span>审查意见通知书（.pdf/.docx）
            </label>
            <input
              ref="officeActionInput"
              type="file"
              accept=".pdf,.docx"
              class="block w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 file:mr-4 file:rounded-xl file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-700 hover:file:bg-slate-200"
              @change="onOfficeActionChange"
            />
            <p v-if="officeActionFile" class="mt-2 text-xs text-slate-500">{{ officeActionFile.name }}</p>
          </div>

          <div>
            <label class="mb-2 block text-sm font-medium text-slate-700">
              <span class="mr-1 text-rose-500">*</span>意见陈述书（.pdf/.docx）
            </label>
            <input
              ref="responseInput"
              type="file"
              accept=".pdf,.docx"
              class="block w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 file:mr-4 file:rounded-xl file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-700 hover:file:bg-slate-200"
              @change="onResponseChange"
            />
            <p v-if="responseFile" class="mt-2 text-xs text-slate-500">{{ responseFile.name }}</p>
          </div>
        </div>

        <div class="grid gap-4 sm:grid-cols-2">
          <div>
            <label class="mb-2 block text-sm font-medium text-slate-700">权利要求书（可选，.pdf/.docx）</label>
            <input
              ref="claimsInput"
              type="file"
              accept=".pdf,.docx"
              class="block w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 file:mr-4 file:rounded-xl file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-700 hover:file:bg-slate-200"
              @change="onClaimsChange"
            />
            <p v-if="claimsFile" class="mt-2 text-xs text-slate-500">{{ claimsFile.name }}</p>
          </div>

          <div>
            <label class="mb-2 block text-sm font-medium text-slate-700">对比文件（可选，多文件，.pdf/.docx）</label>
            <input
              ref="comparisonInput"
              type="file"
              accept=".pdf,.docx"
              multiple
              class="block w-full rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 file:mr-4 file:rounded-xl file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-700 hover:file:bg-slate-200"
              @change="onComparisonChange"
            />
            <p class="mt-2 text-xs leading-5 text-amber-700">
              仅上传非专利文件（如标准、论文、技术手册等）。
            </p>
            <p v-if="comparisonDocs.length" class="mt-1 text-xs text-slate-500">已选择 {{ comparisonDocs.length }} 个文件</p>
          </div>
        </div>

        <button
          type="button"
          :disabled="loading || !canSubmitOfficeAction"
          class="w-full rounded-2xl bg-cyan-700 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
          @click="submitOfficeActionTask"
        >
          <span v-if="loading">提交中...</span>
          <span v-else>创建 AI 研判任务</span>
        </button>
      </div>
    </section>

    <TaskPanel />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import TaskPanel from '~/components/task/TaskPanel.vue'
import { useTaskStore } from '~/stores/task'
import type { CreateTaskInput } from '~/types/task'

const taskStore = useTaskStore()

const mode = ref<'patent_analysis' | 'office_action_reply'>('patent_analysis')

const loading = ref(false)

const patentNumber = ref('')
const patentFile = ref<File | null>(null)
const patentFileInput = ref<HTMLInputElement>()

const officeActionFile = ref<File | null>(null)
const responseFile = ref<File | null>(null)
const claimsFile = ref<File | null>(null)
const comparisonDocs = ref<File[]>([])

const officeActionInput = ref<HTMLInputElement>()
const responseInput = ref<HTMLInputElement>()
const claimsInput = ref<HTMLInputElement>()
const comparisonInput = ref<HTMLInputElement>()

const patentNumberPattern = /^[A-Z]{2}\d{5,12}[A-Z0-9]{1,2}$/

const normalizedPatentNumber = computed(() => patentNumber.value.trim().toUpperCase())

const patentNumberError = computed(() => {
  if (!patentNumber.value.trim()) return ''
  return patentNumberPattern.test(normalizedPatentNumber.value)
    ? ''
    : '请输入有效的专利公开号（示例：CN116745575A、US20230123456A1）'
})

const patentFileName = computed(() => patentFile.value?.name || '')

const canSubmitPatent = computed(() => {
  const hasPn = !!normalizedPatentNumber.value && !patentNumberError.value
  const hasFile = !!patentFile.value
  return hasPn || hasFile
})

const canSubmitOfficeAction = computed(() => {
  return !!officeActionFile.value && !!responseFile.value
})

const modeDescription = computed(() => {
  if (mode.value === 'patent_analysis') {
    return '统一创建 AI 分析任务，自动完成专利解析、结构化提取、附图识别、形式缺陷检查与检索策略生成，并输出可下载分析报告。'
  }
  return '统一创建 AI 研判任务，自动执行补正追踪、支持依据核查、争议抽取与证据核验，生成可下载的审查意见答复研判报告。'
})

const isSupportedDoc = (file: File): boolean => {
  const name = file.name.toLowerCase()
  return name.endsWith('.pdf') || name.endsWith('.docx')
}

const onPatentFileChange = (event: Event) => {
  const target = event.target as HTMLInputElement
  const selected = target.files?.[0]
  if (!selected) return
  if (!selected.name.toLowerCase().endsWith('.pdf')) {
    taskStore.showGlobalNotice('error', 'AI 分析仅支持 PDF 文件。')
    target.value = ''
    return
  }
  if (selected.size > 50 * 1024 * 1024) {
    taskStore.showGlobalNotice('error', '文件大小不能超过 50MB。')
    target.value = ''
    return
  }
  patentFile.value = selected
}

const onOfficeActionChange = (event: Event) => {
  const target = event.target as HTMLInputElement
  const selected = target.files?.[0]
  if (!selected) return
  if (!isSupportedDoc(selected)) {
    taskStore.showGlobalNotice('error', '审查意见通知书仅支持 PDF 或 DOCX。')
    target.value = ''
    return
  }
  officeActionFile.value = selected
}

const onResponseChange = (event: Event) => {
  const target = event.target as HTMLInputElement
  const selected = target.files?.[0]
  if (!selected) return
  if (!isSupportedDoc(selected)) {
    taskStore.showGlobalNotice('error', '意见陈述书仅支持 PDF 或 DOCX。')
    target.value = ''
    return
  }
  responseFile.value = selected
}

const onClaimsChange = (event: Event) => {
  const target = event.target as HTMLInputElement
  const selected = target.files?.[0]
  if (!selected) {
    claimsFile.value = null
    return
  }
  if (!isSupportedDoc(selected)) {
    taskStore.showGlobalNotice('error', '权利要求书仅支持 PDF 或 DOCX。')
    target.value = ''
    return
  }
  claimsFile.value = selected
}

const onComparisonChange = (event: Event) => {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files || [])
  if (!files.length) {
    comparisonDocs.value = []
    return
  }

  const unsupported = files.find((file) => !isSupportedDoc(file))
  if (unsupported) {
    taskStore.showGlobalNotice('error', `不支持文件：${unsupported.name}，仅支持 PDF 或 DOCX。`)
    target.value = ''
    comparisonDocs.value = []
    return
  }

  comparisonDocs.value = files
}

const submitPatentTask = async () => {
  const hasProcessingTasks = taskStore.hasProcessingTasks
  if (hasProcessingTasks) {
    taskStore.showGlobalNotice('error', '有任务正在处理中，请等待完成后再创建新任务。')
    return
  }

  const payload: CreateTaskInput = {
    taskType: 'patent_analysis',
    patentNumber: normalizedPatentNumber.value || undefined,
    file: patentFile.value || undefined,
  }

  loading.value = true
  try {
    const result = await taskStore.createTask(payload)
    if (result.ok) {
      patentNumber.value = ''
      patentFile.value = null
      if (patentFileInput.value) patentFileInput.value.value = ''
      taskStore.showGlobalNotice('success', result.message || 'AI 分析任务已创建，正在处理。')
    } else {
      taskStore.showGlobalNotice('error', result.error || '任务创建失败，请重试。')
    }
  } finally {
    loading.value = false
  }
}

const submitOfficeActionTask = async () => {
  if (!officeActionFile.value || !responseFile.value) {
    taskStore.showGlobalNotice('error', '请上传审查意见通知书和意见陈述书。')
    return
  }

  const hasProcessingTasks = taskStore.hasProcessingTasks
  if (hasProcessingTasks) {
    taskStore.showGlobalNotice('error', '有任务正在处理中，请等待完成后再创建新任务。')
    return
  }

  const payload: CreateTaskInput = {
    taskType: 'office_action_reply',
    officeActionFile: officeActionFile.value,
    responseFile: responseFile.value,
    claimsFile: claimsFile.value || undefined,
    comparisonDocs: comparisonDocs.value,
  }

  loading.value = true
  try {
    const result = await taskStore.createTask(payload)
    if (result.ok) {
      officeActionFile.value = null
      responseFile.value = null
      claimsFile.value = null
      comparisonDocs.value = []
      if (officeActionInput.value) officeActionInput.value.value = ''
      if (responseInput.value) responseInput.value.value = ''
      if (claimsInput.value) claimsInput.value.value = ''
      if (comparisonInput.value) comparisonInput.value.value = ''
      taskStore.showGlobalNotice('success', result.message || 'AI 研判任务已创建，正在处理。')
    } else {
      taskStore.showGlobalNotice('error', result.error || '任务创建失败，请重试。')
    }
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  taskStore.init()
})
</script>

<style scoped>
.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translate(-50%, -6px);
}
</style>
