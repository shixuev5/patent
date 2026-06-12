<template>
  <section class="flex min-h-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
    <div class="border-b border-slate-200 px-3 py-3">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="text-[13px] font-semibold text-slate-900">证据与条件</p>
          <p class="mt-0.5 text-[11px] text-slate-500">
            已选 {{ selectedDocuments.length }} · 候选 {{ candidateDocuments.length }}
          </p>
        </div>
        <div class="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          <button
            type="button"
            class="panel-action"
            :disabled="streaming || !hasSession"
            @click="$emit('export-report')"
          >
            导出报告
          </button>
          <button
            type="button"
            class="panel-action"
            :disabled="streaming || !hasSession || selectedDocuments.length === 0"
            :title="selectedDocuments.length === 0 ? '选中至少 1 篇对比文献后生成通知书' : '基于已选文献生成审查意见通知书'"
            @click="$emit('export-office-action')"
          >
            生成通知书
          </button>
          <button
            v-if="phase === 'running'"
            type="button"
            class="panel-action border-rose-200 text-rose-700 hover:bg-rose-50"
            :disabled="!hasSession"
            @click="$emit('cancel-run')"
          >
            停止本轮
          </button>
          <span class="rounded-full px-2 py-1 text-[11px] font-medium" :class="statusClass">
            {{ statusLabel }}
          </span>
          <button
            v-if="closable"
            type="button"
            class="inline-flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 text-slate-500 transition hover:bg-slate-50 hover:text-slate-700"
            aria-label="关闭证据与条件"
            title="关闭"
            @click="$emit('close')"
          >
            <XMarkIcon class="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-y-auto px-3 py-3">
      <div class="space-y-4">
        <section>
          <div class="flex items-center justify-between gap-3">
            <p class="context-section-title">检索目标与停止</p>
            <button
              type="button"
              class="inline-flex h-7 items-center justify-center rounded-lg bg-cyan-700 px-2.5 text-[11px] font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
              :disabled="streaming || !hasSession"
              @click="$emit('apply-stop-policy')"
            >
              保存
            </button>
          </div>
          <div class="mt-2 space-y-2">
            <label class="core-field">
              <span>本轮覆盖问题</span>
              <textarea
                v-model.trim="stopPolicyDraft.targetCoverage"
                rows="2"
                class="context-textarea"
                placeholder="例如：覆盖 A+C 区别特征、技术效果、组合动机"
              />
            </label>
            <label class="core-field">
              <span>满足即停止</span>
              <textarea
                v-model.trim="stopPolicyDraft.stopWhen"
                rows="2"
                class="context-textarea"
                placeholder="例如：找到强相关主文献和可组合辅文献即可停止"
              />
            </label>
          </div>
          <div class="mt-2 grid grid-cols-2 gap-2">
            <div class="metric-tile">
              <p>强相关证据</p>
              <strong>{{ selectedDocuments.length }}</strong>
            </div>
            <div class="metric-tile">
              <p>待判断候选</p>
              <strong>{{ candidateDocuments.length }}</strong>
            </div>
          </div>
        </section>

        <section class="border-t border-slate-200 pt-3">
          <p class="context-section-title">快捷指令</p>
          <div class="mt-2 grid grid-cols-2 gap-2">
            <button
              v-for="action in quickActions"
              :key="action.key"
              type="button"
              class="quick-action"
              :disabled="!hasSession"
              :title="action.title"
              @click="$emit('quick-prompt', action.key)"
            >
              <component :is="action.icon" class="h-3.5 w-3.5" />
              <span>{{ action.label }}</span>
            </button>
          </div>
        </section>

        <section class="border-t border-slate-200 pt-3">
          <div class="flex items-center justify-between gap-3">
            <p class="context-section-title">补充文献</p>
            <button
              type="button"
              class="panel-action gap-1.5"
              :disabled="!hasSession || supplementDisabled || supplementBusy"
              :title="supplementDisabled ? '当前轮次结束后可补充文献' : '补充公开号或 PDF 文献'"
              @click="supplementOpen = !supplementOpen"
            >
              <PaperClipIcon class="h-3.5 w-3.5" />
              <span>{{ supplementOpen ? '收起' : '添加' }}</span>
            </button>
          </div>

          <div
            v-if="supplementFeedback"
            class="mt-2 rounded-lg border px-2.5 py-2 text-[11px] leading-5"
            :class="supplementFeedback.failedItems.length ? 'border-amber-200 bg-amber-50 text-amber-900' : 'border-cyan-100 bg-cyan-50 text-cyan-900'"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0 flex-1">
                <p class="font-semibold">导入结果</p>
                <p class="mt-0.5 text-current/80">{{ supplementFeedbackText }}</p>
              </div>
              <button
                type="button"
                class="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-current/60 transition hover:bg-white/60 hover:text-current"
                aria-label="关闭补充文献导入结果"
                title="关闭"
                @click="$emit('clear-supplement-feedback')"
              >
                <XMarkIcon class="h-3 w-3" />
              </button>
            </div>
            <div v-if="supplementFeedback.importedItems.length" class="mt-1.5 space-y-1">
              <p
                v-for="(item, index) in supplementFeedback.importedItems.slice(0, 4)"
                :key="`${supplementImportedItemName(item)}-${index}`"
                class="truncate text-current/80"
                :title="supplementImportedItemText(item)"
              >
                {{ supplementImportedItemText(item) }}
              </p>
              <p v-if="supplementFeedback.importedItems.length > 4" class="text-current/70">
                另有 {{ supplementFeedback.importedItems.length - 4 }} 项已导入。
              </p>
            </div>
            <div v-if="supplementFeedback.failedItems.length" class="mt-1.5 space-y-1 border-t border-current/15 pt-1.5">
              <p
                v-for="(item, index) in supplementFeedback.failedItems.slice(0, 4)"
                :key="`${supplementFailedItemName(item)}-${index}`"
                class="truncate text-current/80"
                :title="supplementFailedItemText(item)"
              >
                {{ supplementFailedItemText(item) }}
              </p>
              <p v-if="supplementFeedback.failedItems.length > 4" class="text-current/70">
                另有 {{ supplementFeedback.failedItems.length - 4 }} 项失败。
              </p>
            </div>
            <div v-if="canReviewSupplementFeedback" class="mt-2 flex justify-end border-t border-current/15 pt-2">
              <button
                type="button"
                class="inline-flex h-7 items-center justify-center rounded-lg bg-cyan-700 px-2.5 text-[11px] font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                :disabled="streaming || supplementBusy"
                @click="$emit('review-supplement', supplementFeedback.reviewPrompt)"
              >
                筛查这些文献
              </button>
            </div>
          </div>

          <div v-if="supplementOpen" class="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2.5">
            <input
              v-model.trim="supplementPatentNumbers"
              type="text"
              class="h-8 w-full rounded-lg border border-slate-200 bg-white px-2.5 text-[12px] text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100"
              :disabled="supplementDisabled || supplementBusy"
              placeholder="公开号：CN107219079A、CN106596008B"
            />
            <div class="mt-2">
              <button
                type="button"
                class="inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-[12px] font-medium text-slate-600 transition hover:border-cyan-200 hover:text-cyan-700 disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="supplementDisabled || supplementBusy"
                @click="fileInputRef?.click()"
              >
                <PaperClipIcon class="h-3.5 w-3.5" />
                <span>选择 PDF</span>
              </button>
            </div>
            <input
              ref="fileInputRef"
              type="file"
              accept="application/pdf,.pdf"
              multiple
              class="hidden"
              @change="onSupplementFilesChange"
            />
            <div v-if="supplementFiles.length" class="mt-2 flex flex-wrap gap-1.5">
              <span
                v-for="file in supplementFiles"
                :key="`${file.name}-${file.size}-${file.lastModified}`"
                class="inline-flex max-w-full items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-600"
              >
                <span class="truncate">{{ file.name }}</span>
                <button
                  type="button"
                  class="text-slate-400 hover:text-rose-600"
                  title="移除"
                  @click="removeSupplementFile(file)"
                >
                  <XMarkIcon class="h-3 w-3" />
                </button>
              </span>
            </div>
            <textarea
              v-model.trim="supplementReviewGoal"
              rows="2"
              class="mt-2 min-h-[3.5rem] w-full resize-none rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-[12px] leading-5 text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100"
              :disabled="supplementDisabled || supplementBusy"
              placeholder="筛查目标：判断命中点、缺口和是否应选为对比文献"
            />
            <div class="mt-2 flex items-center justify-end gap-2">
              <button
                type="button"
                class="inline-flex h-8 items-center justify-center rounded-lg border border-slate-200 bg-white px-3 text-[12px] font-medium text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="supplementBusy"
                @click="resetSupplementDraft"
              >
                清空
              </button>
              <button
                type="button"
                class="inline-flex h-8 items-center justify-center rounded-lg bg-cyan-700 px-3 text-[12px] font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                :disabled="!canSubmitSupplement"
                @click="submitSupplement"
              >
                {{ supplementBusy ? '导入中...' : '导入文献' }}
              </button>
            </div>
          </div>
        </section>

        <section class="border-t border-slate-200 pt-3">
          <div class="flex items-center justify-between gap-3">
            <p class="context-section-title">证据池</p>
            <div class="segmented-control">
              <button
                type="button"
                :class="documentBucket === 'selected' ? 'is-active' : ''"
                @click="documentBucket = 'selected'"
              >
                已选
              </button>
              <button
                type="button"
                :class="documentBucket === 'candidate' ? 'is-active' : ''"
                @click="documentBucket = 'candidate'"
              >
                候选
              </button>
            </div>
          </div>
          <div v-if="!visibleDocuments.length" class="empty-docs">
            {{ documentBucket === 'selected' ? '暂无已选文献' : '暂无候选结果' }}
          </div>
          <article
            v-for="doc in visibleDocuments"
            :key="doc.document_id || doc.canonical_id"
            class="doc-row"
          >
            <p class="line-clamp-2 text-[12px] font-semibold leading-5 text-slate-900">{{ documentTitle(doc) }}</p>
            <p class="mt-1 truncate text-[11px] text-slate-500">{{ documentMeta(doc) }}</p>
            <div v-if="documentTags(doc).length" class="mt-1.5 flex flex-wrap gap-1">
              <span
                v-for="tag in documentTags(doc)"
                :key="tag"
                class="rounded-md border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] font-medium text-slate-600"
              >
                {{ tag }}
              </span>
            </div>
            <p v-if="documentReason(doc)" class="mt-1 line-clamp-3 whitespace-pre-wrap text-[11px] leading-5 text-slate-600">
              {{ documentReason(doc) }}
            </p>
            <details v-if="doc.abstract" class="mt-1 text-[11px] leading-5 text-slate-600">
              <summary class="cursor-pointer text-slate-500">摘要</summary>
              <p class="mt-1 whitespace-pre-wrap">{{ doc.abstract }}</p>
            </details>
            <div class="mt-2 flex justify-end">
              <button
                v-if="documentBucket === 'candidate'"
                type="button"
                class="doc-action text-cyan-700 hover:bg-cyan-50"
                :disabled="streaming"
                @click="$emit('select-document', String(doc.document_id || ''))"
              >
                选中
              </button>
              <button
                v-else
                type="button"
                class="doc-action text-rose-700 hover:bg-rose-50"
                :disabled="streaming"
                @click="$emit('remove-document', String(doc.document_id || ''))"
              >
                移出
              </button>
            </div>
          </article>
        </section>

        <details class="context-details">
          <summary>
            <span>低频设置</span>
            <ChevronDownIcon class="h-4 w-4" />
          </summary>
          <div class="mt-3 space-y-3">
            <div class="grid grid-cols-2 gap-2">
              <label class="stop-field">
                <span>最多轮次</span>
                <input v-model.number="stopPolicyDraft.maxRounds" type="number" min="1" max="30" />
              </label>
              <label class="stop-field">
                <span>检索式上限</span>
                <input v-model.number="stopPolicyDraft.maxQueries" type="number" min="1" max="200" />
              </label>
              <label class="stop-field">
                <span>候选上限</span>
                <input v-model.number="stopPolicyDraft.maxCandidates" type="number" min="1" max="1000" />
              </label>
              <label class="stop-field">
                <span>强证据上限</span>
                <input v-model.number="stopPolicyDraft.maxSelectedDocuments" type="number" min="1" max="50" />
              </label>
              <label class="stop-field">
                <span>连续无新增</span>
                <input v-model.number="stopPolicyDraft.maxNoNewResultRounds" type="number" min="1" max="20" />
              </label>
              <label class="stop-field">
                <span>时间上限(秒)</span>
                <input v-model.number="stopPolicyDraft.deadlineSeconds" type="number" min="30" max="7200" />
              </label>
            </div>
            <div>
              <p class="mb-2 text-[11px] font-medium text-slate-600">检索源</p>
              <div class="flex flex-wrap gap-1.5">
                <label
                  v-for="source in databaseOptions"
                  :key="source.value"
                  class="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] text-slate-700"
                >
                  <input
                    v-model="stopPolicyDraft.databases"
                    type="checkbox"
                    :value="source.value"
                    class="h-3.5 w-3.5 rounded border-slate-300 text-cyan-700 focus:ring-cyan-500"
                  />
                  <span>{{ source.label }}</span>
                </label>
              </div>
            </div>
            <div class="flex justify-end">
              <button
                type="button"
                class="inline-flex h-8 items-center justify-center rounded-lg bg-cyan-700 px-3 text-[12px] font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                :disabled="streaming || !hasSession"
                @click="$emit('apply-stop-policy')"
              >
                应用设置
              </button>
            </div>
          </div>
        </details>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { AdjustmentsHorizontalIcon, ArrowPathIcon, ChevronDownIcon, DocumentTextIcon, MagnifyingGlassIcon, PaperClipIcon, XMarkIcon } from '@heroicons/vue/24/outline'
import { computed, ref } from 'vue'

type DatabaseOption = {
  value: string
  label: string
}

type SupplementFeedback = {
  importedCount: number
  patentCount: number
  pdfCount: number
  importedItems: Array<Record<string, any>>
  failedItems: Array<Record<string, any>>
  reviewPrompt: string
}

const props = withDefaults(defineProps<{
  stopPolicyDraft: Record<string, any>
  databaseOptions: DatabaseOption[]
  candidateDocuments: Array<Record<string, any>>
  selectedDocuments: Array<Record<string, any>>
  streaming?: boolean
  hasSession?: boolean
  phase?: string
  closable?: boolean
  supplementDisabled?: boolean
  supplementBusy?: boolean
  supplementFeedback?: SupplementFeedback | null
}>(), {
  streaming: false,
  hasSession: false,
  phase: 'idle',
  closable: false,
  supplementDisabled: false,
  supplementBusy: false,
  supplementFeedback: null,
})

const emit = defineEmits<{
  'apply-stop-policy': []
  'select-document': [documentId: string]
  'remove-document': [documentId: string]
  'quick-prompt': [key: string]
  'cancel-run': []
  'export-report': []
  'export-office-action': []
  supplement: [payload: { patentNumbers: string, reviewGoal: string, files: File[] }]
  'review-supplement': [prompt: string]
  'clear-supplement-feedback': []
  'close': []
}>()

const quickActions = [
  { key: 'direct-search', label: '直接检索', title: '目标已清楚时，立即开始检索。', icon: MagnifyingGlassIcon },
  { key: 'continue-search', label: '继续扩展', title: '已有候选但还不够时，从新方向补检。', icon: ArrowPathIcon },
  { key: 'summarize-selected', label: '总结已选', title: '汇总已选文献的命中点、证据差距和下一步。', icon: DocumentTextIcon },
  { key: 'tighten-stop', label: '收紧边界', title: '候选过多或范围发散时，缩小检索范围。', icon: AdjustmentsHorizontalIcon },
]

const documentBucket = ref<'selected' | 'candidate'>('selected')
const supplementOpen = ref(false)
const supplementPatentNumbers = ref('')
const supplementReviewGoal = ref('')
const supplementFiles = ref<File[]>([])
const fileInputRef = ref<HTMLInputElement | null>(null)

const visibleDocuments = computed(() => (
  documentBucket.value === 'selected'
    ? props.selectedDocuments
    : props.candidateDocuments
))

const canSubmitSupplement = computed(() => (
  props.hasSession
  && !props.supplementDisabled
  && !props.supplementBusy
  && (
    !!supplementPatentNumbers.value.trim()
    || supplementFiles.value.length > 0
  )
))

const supplementFeedbackText = computed(() => {
  const feedback = props.supplementFeedback
  if (!feedback) return ''
  const parts = [
    `已导入 ${feedback.importedCount} 篇`,
    `公开号 ${feedback.patentCount} 项`,
    `PDF ${feedback.pdfCount} 份`,
  ]
  if (feedback.failedItems.length) parts.push(`失败 ${feedback.failedItems.length} 项`)
  return `${parts.join('，')}。`
})

const canReviewSupplementFeedback = computed(() => (
  !!props.supplementFeedback
  && Number(props.supplementFeedback.importedCount || 0) > 0
  && !!String(props.supplementFeedback.reviewPrompt || '').trim()
))

const statusLabel = computed(() => (props.phase === 'running' ? '检索中' : '空闲'))
const statusClass = computed(() => (props.phase === 'running'
  ? 'bg-cyan-50 text-cyan-700'
  : 'bg-slate-100 text-slate-600'
))

const documentTitle = (doc: Record<string, any>): string => (
  String(doc.title || doc.pn || doc.doi || doc.external_id || '未命名文献').trim()
)

const documentMeta = (doc: Record<string, any>): string => {
  const segments = [
    String(doc.pn || doc.doi || doc.external_id || '').trim(),
    String(doc.publication_date || doc.application_date || '').trim(),
  ].filter(Boolean)
  return segments.join(' · ') || '未提供来源信息'
}

const documentTags = (doc: Record<string, any>): string[] => {
  const tags: string[] = []
  const sourceType = String(doc.source_type || '').trim()
  const detailSource = String(doc.detail_source || '').trim()
  if (doc.user_pinned || detailSource.startsWith('user_') || sourceType === 'user_pdf') tags.push('用户补充')
  if (sourceType === 'user_pdf') tags.push('PDF')
  else if (sourceType === 'patent' || doc.pn) tags.push('专利')
  else if (doc.doi) tags.push('论文')
  return [...new Set(tags)]
}

const documentReason = (doc: Record<string, any>): string => (
  String(doc.evidence_summary || doc.reviewReason || doc.agent_reason || '').trim()
)

const onSupplementFilesChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || []).filter(file => file.type === 'application/pdf' || /\.pdf$/i.test(file.name))
  const existing = new Map(supplementFiles.value.map(file => [`${file.name}-${file.size}-${file.lastModified}`, file]))
  files.forEach((file) => {
    existing.set(`${file.name}-${file.size}-${file.lastModified}`, file)
  })
  supplementFiles.value = Array.from(existing.values()).slice(0, 8)
  input.value = ''
}

const removeSupplementFile = (target: File) => {
  supplementFiles.value = supplementFiles.value.filter(file => file !== target)
}

const resetSupplementDraft = () => {
  supplementPatentNumbers.value = ''
  supplementReviewGoal.value = ''
  supplementFiles.value = []
}

const submitSupplement = () => {
  if (!canSubmitSupplement.value) return
  emit('supplement', {
    patentNumbers: supplementPatentNumbers.value.trim(),
    reviewGoal: supplementReviewGoal.value.trim(),
    files: [...supplementFiles.value],
  })
  resetSupplementDraft()
  supplementOpen.value = false
}

const supplementImportedItemName = (item: Record<string, any>): string => (
  String(item.pn || item.filename || item.title || '补充文献').trim()
)

const supplementImportedItemText = (item: Record<string, any>): string => {
  const name = supplementImportedItemName(item)
  const source = String(item.sourceType || '').trim() === 'user_pdf' ? 'PDF' : '公开号'
  if (String(item.status || '').trim() === 'target_detail') {
    return `${source} ${name}：已更新目标专利详情`
  }
  return `${source} ${name}：已导入候选文献`
}

const supplementFailedItemName = (item: Record<string, any>): string => (
  String(item.pn || item.filename || item.title || '补充文献').trim()
)

const supplementFailedItemText = (item: Record<string, any>): string => {
  const name = supplementFailedItemName(item)
  const reason = String(item.error || item.reason || item.message || '导入失败').trim()
  return `${name}：${reason}`
}
</script>

<style scoped>
.panel-action {
  @apply inline-flex h-7 items-center justify-center rounded-lg border border-slate-200 px-2 text-[11px] font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50;
}

.context-section-title {
  @apply text-[12px] font-semibold text-slate-900;
}

.core-field {
  @apply block text-[12px] text-slate-600;
}

.core-field span {
  @apply mb-1 block font-medium text-slate-700;
}

.context-textarea {
  @apply min-h-[4.5rem] w-full resize-none rounded-lg border border-slate-200 px-2.5 py-2 text-[12px] leading-5 text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100;
}

.metric-tile {
  @apply rounded-lg border border-slate-200 bg-slate-50 px-3 py-2;
}

.metric-tile p {
  @apply text-[11px] font-medium text-slate-500;
}

.metric-tile strong {
  @apply mt-1 block text-lg font-semibold leading-6 text-slate-900;
}

.quick-action {
  @apply inline-flex h-8 min-w-0 items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2 text-[11px] font-medium text-slate-700 transition hover:border-cyan-200 hover:bg-cyan-50 hover:text-cyan-700 disabled:cursor-not-allowed disabled:opacity-50;
}

.segmented-control {
  @apply inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5;
}

.segmented-control button {
  @apply h-7 rounded-md px-2 text-[11px] font-medium text-slate-500 transition hover:text-slate-800;
}

.segmented-control button.is-active {
  @apply bg-white text-cyan-700 shadow-sm;
}

.context-details {
  @apply border-t border-slate-200 pt-3;
}

.context-details > summary {
  @apply flex cursor-pointer list-none items-center justify-between text-[12px] font-semibold text-slate-900;
}

.context-details > summary::-webkit-details-marker {
  display: none;
}

.context-details[open] > summary svg {
  transform: rotate(180deg);
}

.context-details svg {
  transition: transform 0.18s ease;
}

.stop-field {
  @apply block text-[11px] text-slate-600;
}

.stop-field span {
  @apply mb-1 block;
}

.stop-field input {
  @apply h-8 w-full rounded-lg border border-slate-200 px-2 text-[12px] outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100;
}

.empty-docs {
  @apply mt-2 rounded-lg border border-dashed border-slate-200 px-3 py-5 text-center text-[12px] text-slate-500;
}

.doc-row {
  @apply mt-2 rounded-lg border border-slate-200 bg-white px-3 py-2;
}

.doc-action {
  @apply inline-flex h-7 items-center justify-center rounded-lg border border-slate-200 px-2 text-[11px] font-medium transition disabled:cursor-not-allowed disabled:opacity-50;
}
</style>
