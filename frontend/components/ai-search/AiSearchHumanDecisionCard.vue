<template>
  <section class="space-y-4 rounded-2xl border border-slate-300 bg-slate-50 px-4 py-4">
    <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
      <div class="min-w-0">
        <p class="text-sm font-semibold text-slate-900">自动检索已暂停，等待人工决策</p>
        <p v-if="summary" class="mt-1 text-xs leading-5 text-slate-700">
          {{ summary }}
        </p>
        <p v-if="reason" class="mt-2 text-[11px] text-slate-500">
          原因：{{ reason }}
        </p>
        <p class="mt-2 text-[11px] text-slate-500">
          已执行 {{ roundCount }} 轮，连续无进展 {{ noProgressRoundCount }} 轮，当前已选 {{ selectedCount }} 篇文献。
        </p>
      </div>
      <div class="flex shrink-0 flex-wrap gap-2">
        <button
          type="button"
          class="rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:bg-slate-200"
          :disabled="disabled"
          @click="$emit('continue-search')"
        >
          继续检索
        </button>
        <button
          type="button"
          class="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
          :disabled="disabled || selectedCount <= 0"
          @click="$emit('complete-current-results')"
        >
          按当前结果完成
        </button>
      </div>
    </div>

    <div class="grid gap-4 xl:grid-cols-2">
      <section class="rounded-2xl border border-slate-200 bg-white px-4 py-4">
        <div class="flex items-center justify-between gap-3">
          <div>
            <p class="text-sm font-semibold text-slate-900">已选对比文件</p>
            <p class="mt-1 text-[11px] text-slate-500">可将当前已选文献移出，回到候选复核区。</p>
          </div>
          <span class="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-600">
            {{ selectedDocs.length }} 篇
          </span>
        </div>

        <div v-if="selectedDocs.length" class="mt-4 space-y-3">
          <article
            v-for="item in selectedDocs"
            :key="documentId(item)"
            class="rounded-2xl border border-slate-200 bg-slate-50/70 px-3 py-3"
          >
            <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div class="min-w-0">
                <p class="text-sm font-semibold text-slate-900">{{ documentTitle(item) }}</p>
                <p class="mt-1 text-[11px] text-slate-500">{{ metaLine(item) }}</p>
                <p v-if="claimLine(item)" class="mt-2 text-[11px] text-slate-600">命中权利要求：{{ claimLine(item) }}</p>
                <p v-if="reviewReason(item)" class="mt-2 text-[11px] text-slate-600">当前判定：{{ reviewReason(item) }}</p>
                <p v-if="evidenceSummary(item)" class="mt-2 text-[11px] leading-5 text-slate-700">证据摘要：{{ evidenceSummary(item) }}</p>
                <div v-if="passages(item).length" class="mt-2 space-y-2">
                  <p
                    v-for="(passage, index) in passages(item)"
                    :key="`${documentId(item)}-passage-${index}`"
                    class="rounded-xl border border-slate-200 bg-white px-3 py-2 text-[11px] leading-5 text-slate-700"
                  >
                    {{ passage }}
                  </p>
                </div>
              </div>
              <button
                type="button"
                class="shrink-0 rounded-xl border border-rose-200 bg-white px-3 py-2 text-xs font-semibold text-rose-700 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
                :disabled="disabled || item.manualAction !== 'can_remove'"
                @click="$emit('remove-selected', documentId(item))"
              >
                移出已选
              </button>
            </div>
          </article>
        </div>
        <div v-else class="mt-4 rounded-2xl border border-dashed border-slate-200 px-3 py-5 text-center text-[12px] text-slate-500">
          当前无已选对比文献。请从右侧候选中送审复核，或继续检索。
        </div>
      </section>

      <section class="rounded-2xl border border-slate-200 bg-white px-4 py-4">
        <div class="flex items-center justify-between gap-3">
          <div>
            <p class="text-sm font-semibold text-slate-900">候选复核区</p>
            <p class="mt-1 text-[11px] text-slate-500">仅支持将 shortlisted 文献送入一轮人工触发的精读复核。</p>
          </div>
          <span class="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-600">
            {{ reviewCandidates.length }} 篇
          </span>
        </div>

        <div v-if="reviewCandidates.length" class="mt-4 space-y-3">
          <article
            v-for="item in reviewCandidates"
            :key="documentId(item)"
            class="rounded-2xl border border-slate-200 bg-slate-50/70 px-3 py-3"
          >
            <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <p class="text-sm font-semibold text-slate-900">{{ documentTitle(item) }}</p>
                  <span
                    class="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                    :class="item.evidenceReady ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'"
                  >
                    {{ item.evidenceReady ? '已有证据' : '待补证据' }}
                  </span>
                </div>
                <p class="mt-1 text-[11px] text-slate-500">{{ metaLine(item) }}</p>
                <p v-if="reviewReason(item)" class="mt-2 text-[11px] text-slate-600">当前判定：{{ reviewReason(item) }}</p>
                <p v-if="evidenceSummary(item)" class="mt-2 text-[11px] leading-5 text-slate-700">证据摘要：{{ evidenceSummary(item) }}</p>
                <p v-else-if="abstractPreview(item)" class="mt-2 text-[11px] leading-5 text-slate-700">摘要预览：{{ abstractPreview(item) }}</p>
              </div>
              <button
                type="button"
                class="shrink-0 rounded-xl border border-cyan-200 bg-white px-3 py-2 text-xs font-semibold text-cyan-700 transition hover:bg-cyan-50 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
                :disabled="disabled || item.manualAction !== 'can_review'"
                @click="$emit('request-review', documentId(item))"
              >
                送审复核
              </button>
            </div>
          </article>
        </div>
        <div v-else class="mt-4 rounded-2xl border border-dashed border-slate-200 px-3 py-5 text-center text-[12px] text-slate-500">
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
const claimLine = (item: Record<string, any>): string => {
  const values = Array.isArray(item?.claim_ids_json) ? item.claim_ids_json : []
  return values.map((value: unknown) => String(value || '').trim()).filter(Boolean).join(', ')
}
const evidenceSummary = (item: Record<string, any>): string => String(item?.evidence_summary || '').trim()
const reviewReason = (item: Record<string, any>): string => String(item?.reviewReason || item?.close_read_reason || item?.agent_reason || '').trim()
const passages = (item: Record<string, any>): string[] => {
  const values = Array.isArray(item?.key_passages_json) ? item.key_passages_json : []
  return values.slice(0, 2).map((entry: Record<string, any>) => {
    const reasonText = String(entry?.reason || '').trim()
    const passageText = String(entry?.passage || '').trim()
    return [reasonText, passageText].filter(Boolean).join('：')
  }).filter(Boolean)
}
const abstractPreview = (item: Record<string, any>): string => String(item?.abstract || '').trim().slice(0, 180)
</script>
