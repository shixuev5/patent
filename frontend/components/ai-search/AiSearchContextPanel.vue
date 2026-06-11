<template>
  <section class="flex min-h-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
    <div class="border-b border-slate-200 px-3 py-3">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="text-[13px] font-semibold text-slate-900">审查上下文</p>
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
            导出
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
            aria-label="关闭审查上下文"
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
            <p class="context-section-title">核心关注</p>
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
              <span>待覆盖审查要点</span>
              <textarea
                v-model.trim="stopPolicyDraft.targetCoverage"
                rows="2"
                class="context-textarea"
                placeholder="例如：区别特征、技术效果、组合动机"
              />
            </label>
            <label class="core-field">
              <span>可停止的判断口径</span>
              <textarea
                v-model.trim="stopPolicyDraft.stopWhen"
                rows="2"
                class="context-textarea"
                placeholder="例如：找到强相关主文献和可组合辅文献"
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
          <p class="context-section-title">常用动作</p>
          <div class="mt-2 grid grid-cols-2 gap-2">
            <button
              v-for="action in quickActions"
              :key="action.key"
              type="button"
              class="quick-action"
              :disabled="streaming"
              @click="$emit('quick-prompt', action.key)"
            >
              <component :is="action.icon" class="h-3.5 w-3.5" />
              <span>{{ action.label }}</span>
            </button>
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
import { AdjustmentsHorizontalIcon, ArrowPathIcon, ChevronDownIcon, DocumentTextIcon, MagnifyingGlassIcon, XMarkIcon } from '@heroicons/vue/24/outline'
import { computed, ref } from 'vue'

type DatabaseOption = {
  value: string
  label: string
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
}>(), {
  streaming: false,
  hasSession: false,
  phase: 'idle',
  closable: false,
})

defineEmits<{
  'apply-stop-policy': []
  'select-document': [documentId: string]
  'remove-document': [documentId: string]
  'quick-prompt': [key: string]
  'cancel-run': []
  'export-report': []
  'close': []
}>()

const quickActions = [
  { key: 'direct-search', label: '直接检索', icon: MagnifyingGlassIcon },
  { key: 'continue-search', label: '继续扩展', icon: ArrowPathIcon },
  { key: 'summarize-selected', label: '总结已选', icon: DocumentTextIcon },
  { key: 'tighten-stop', label: '收紧边界', icon: AdjustmentsHorizontalIcon },
]

const documentBucket = ref<'selected' | 'candidate'>('selected')

const visibleDocuments = computed(() => (
  documentBucket.value === 'selected'
    ? props.selectedDocuments
    : props.candidateDocuments
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
    String(doc.source_type || '').trim(),
  ].filter(Boolean)
  return segments.join(' · ') || '未提供来源信息'
}

const documentReason = (doc: Record<string, any>): string => (
  String(doc.evidence_summary || doc.reviewReason || doc.agent_reason || '').trim()
)
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
