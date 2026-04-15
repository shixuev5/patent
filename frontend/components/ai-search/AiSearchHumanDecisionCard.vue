<template>
  <section class="space-y-4 rounded-2xl border border-slate-200 bg-slate-50/55 px-4 py-4">
    <div class="space-y-3 border-b border-slate-200 pb-3">
      <div class="min-w-0">
        <p class="text-[13px] font-semibold text-slate-900">自动检索已暂停，等待人工决策</p>
        <p v-if="summary" class="mt-1 text-xs leading-5 text-slate-700">
          {{ summary }}
        </p>
        <p v-if="reason" class="mt-1 text-[11px] leading-5 text-slate-500">
          {{ reason }}
        </p>
      </div>
      <div class="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <p class="text-[11px] text-slate-500">
          已执行 {{ roundCount }} 轮 · 连续无进展 {{ noProgressRoundCount }} 轮 · 当前已选 {{ selectedCount }} 篇文献
        </p>
        <div class="flex shrink-0 flex-wrap justify-end gap-2">
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[12px] font-semibold text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
            :disabled="disabled"
            @click="$emit('continue-search')"
          >
            继续检索
          </button>
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-lg bg-slate-900 px-3 py-1.5 text-[12px] font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
            :disabled="disabled || selectedCount <= 0"
            @click="$emit('complete-current-results')"
          >
            按当前结果完成
          </button>
        </div>
      </div>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <section>
        <p class="text-sm font-semibold text-slate-900">已选对比文件（{{ selectedDocs.length }}）</p>

        <div v-if="selectedDocs.length" class="mt-4 space-y-3">
          <article
            v-for="item in selectedDocs"
            :key="documentId(item)"
            class="rounded-xl border border-slate-200 bg-white px-3 py-3"
          >
            <div class="min-w-0">
              <p class="text-sm font-semibold text-slate-900">{{ documentTitle(item) }}</p>
              <p class="mt-1 text-[11px] text-slate-500">{{ metaLine(item) }}</p>
              <p v-if="selectedDecisionLine(item)" class="mt-2 text-[12px] leading-5 text-slate-700">
                {{ selectedDecisionLine(item) }}
              </p>
            </div>
            <div class="mt-3 flex justify-end">
              <button
                type="button"
                class="inline-flex items-center justify-center rounded-lg border border-rose-200 bg-white px-3 py-1.5 text-[12px] font-semibold text-rose-700 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
                :disabled="disabled || item.manualAction !== 'can_remove'"
                @click="$emit('remove-selected', documentId(item))"
              >
                移出已选
              </button>
            </div>
          </article>
        </div>
        <div v-else class="mt-4 rounded-xl border border-dashed border-slate-200 px-3 py-5 text-center text-[12px] text-slate-500">
          当前无已选对比文献。
        </div>
      </section>

      <section>
        <p class="text-sm font-semibold text-slate-900">候选复核区（{{ reviewCandidates.length }}）</p>

        <div v-if="reviewCandidates.length" class="mt-4 space-y-3">
          <article
            v-for="item in reviewCandidates"
            :key="documentId(item)"
            class="rounded-xl border border-slate-200 bg-white px-3 py-3"
          >
            <div class="min-w-0">
              <p class="text-sm font-semibold text-slate-900">{{ documentTitle(item) }}</p>
              <p class="mt-1 text-[11px] text-slate-500">{{ metaLine(item) }}</p>
              <p v-if="candidateDecisionLine(item)" class="mt-2 text-[12px] leading-5 text-slate-700">
                {{ candidateDecisionLine(item) }}
              </p>
            </div>
            <div class="mt-3 flex justify-end">
              <button
                type="button"
                class="inline-flex items-center justify-center rounded-lg border border-cyan-200 bg-white px-3 py-1.5 text-[12px] font-semibold text-cyan-700 transition hover:bg-cyan-50 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
                :disabled="disabled || item.manualAction !== 'can_review'"
                @click="$emit('request-review', documentId(item))"
              >
                送审复核
              </button>
            </div>
          </article>
        </div>
        <div v-else class="mt-4 rounded-xl border border-dashed border-slate-200 px-3 py-5 text-center text-[12px] text-slate-500">
          当前没有可送审的 shortlisted 文献。
        </div>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  action?: Record<string, any> | null
  disabled?: boolean
  selectedDocs?: Array<Record<string, any>>
  candidateDocs?: Array<Record<string, any>>
}>()

defineEmits<{
  'continue-search': []
  'complete-current-results': []
  'request-review': [documentId: string]
  'remove-selected': [documentId: string]
}>()

const summary = computed(() => String(props.action?.summary || '').trim())
const reason = computed(() => String(props.action?.reason || '').trim())
const roundCount = computed(() => Number(props.action?.roundCount || 0))
const noProgressRoundCount = computed(() => Number(props.action?.noProgressRoundCount || 0))
const selectedCount = computed(() => Number(props.action?.selectedCount || 0))
const selectedDocs = computed(() => Array.isArray(props.selectedDocs) ? props.selectedDocs : [])
const reviewCandidates = computed(() => {
  const items = Array.isArray(props.candidateDocs) ? props.candidateDocs : []
  return items.filter(item => String(item?.manualAction || '').trim() === 'can_review')
})

const documentId = (item: Record<string, any>): string => String(item?.documentId || item?.document_id || '').trim()
const documentTitle = (item: Record<string, any>): string => {
  return String(item?.title || item?.pn || item?.doi || documentId(item) || '未命名文献').trim()
}
const metaLine = (item: Record<string, any>): string => {
  const parts = [
    String(item?.source_type || '').trim(),
    String(item?.publication_date || item?.application_date || '').trim(),
    String(item?.pn || item?.doi || '').trim(),
  ].filter(Boolean)
  return parts.join(' · ') || '无元数据'
}
const evidenceSummary = (item: Record<string, any>): string => String(item?.evidence_summary || '').trim()
const reviewReason = (item: Record<string, any>): string => String(item?.reviewReason || item?.close_read_reason || item?.agent_reason || '').trim()
const firstPassage = (item: Record<string, any>): string => {
  const values = Array.isArray(item?.key_passages_json) ? item.key_passages_json : []
  const entry = values.find((candidate: Record<string, any>) => {
    return !!String(candidate?.reason || '').trim() || !!String(candidate?.passage || '').trim()
  })
  if (!entry) return ''
  const reasonText = String(entry?.reason || '').trim()
  const passageText = String(entry?.passage || '').trim()
  return [reasonText, passageText].filter(Boolean).join('：')
}
const abstractPreview = (item: Record<string, any>): string => String(item?.abstract || '').trim().slice(0, 180)
const selectedDecisionLine = (item: Record<string, any>): string => {
  return reviewReason(item) || evidenceSummary(item) || firstPassage(item)
}
const candidateDecisionLine = (item: Record<string, any>): string => {
  return reviewReason(item) || evidenceSummary(item) || abstractPreview(item) || firstPassage(item)
}
</script>
