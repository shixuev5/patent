<template>
  <div class="space-y-4">
    <section
      v-for="plan in normalizedPlans"
      :key="plan.subPlanId"
      class="rounded-2xl border border-slate-200 bg-white/80"
    >
      <div class="border-b border-slate-100 px-4 py-3">
        <div class="flex items-center justify-between gap-3">
          <p class="text-sm font-semibold text-slate-900">方案 {{ plan.index }}</p>
          <span class="rounded-full border border-cyan-200 bg-cyan-50 px-2.5 py-1 text-[11px] font-medium text-cyan-700">
            5 分主方案
          </span>
        </div>
        <p class="mt-2 text-sm leading-6 text-slate-700">{{ plan.goal }}</p>
      </div>

      <div class="space-y-4 px-4 py-4">
        <div class="space-y-2">
          <p class="text-[11px] font-semibold tracking-[0.16em] text-slate-400">步骤安排</p>
          <div class="space-y-2">
            <div
              v-for="step in plan.steps"
              :key="`${plan.subPlanId}-${step.stepId}`"
              class="rounded-2xl border border-slate-200 bg-slate-50 px-3 py-3"
            >
              <div class="flex flex-wrap items-center gap-2">
                <p class="text-sm font-semibold text-slate-800">{{ step.title }}</p>
                <span
                  class="rounded-full px-2.5 py-1 text-[11px] font-medium"
                  :class="step.activationMode === 'conditional'
                    ? 'border border-amber-200 bg-amber-50 text-amber-700'
                    : 'border border-emerald-200 bg-emerald-50 text-emerald-700'"
                >
                  {{ step.activationMode === 'conditional' ? '条件触发' : '立即执行' }}
                </span>
              </div>
              <p v-if="step.purpose" class="mt-2 text-[13px] leading-6 text-slate-700">
                {{ step.purpose }}
              </p>
              <p v-if="step.activationSummary" class="mt-2 text-[12px] leading-5 text-slate-500">
                触发说明：{{ step.activationSummary }}
              </p>
            </div>
          </div>
        </div>

        <div class="space-y-2">
          <p class="text-[11px] font-semibold tracking-[0.16em] text-slate-400">主检索</p>
          <p class="whitespace-pre-wrap rounded-2xl bg-slate-50 px-3 py-3 text-[13px] leading-6 text-slate-700">
            {{ plan.semanticQueryText || '未生成语义检索文本。' }}
          </p>
          <div class="overflow-x-auto rounded-2xl border border-slate-200">
            <table class="min-w-full divide-y divide-slate-200 text-left text-xs">
              <thead class="bg-slate-50 text-slate-600">
                <tr>
                  <th class="whitespace-nowrap px-3 py-2 font-semibold">逻辑块</th>
                  <th class="whitespace-nowrap px-3 py-2 font-semibold">检索要素</th>
                  <th class="whitespace-nowrap px-3 py-2 font-semibold">中文扩展</th>
                  <th class="whitespace-nowrap px-3 py-2 font-semibold">英文扩展</th>
                  <th class="whitespace-nowrap px-3 py-2 font-semibold">分类号 (IPC/CPC)</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100 bg-white text-slate-700">
                <tr v-for="element in plan.mainElements" :key="`${plan.subPlanId}-${element.blockId}-${element.elementName}`">
                  <td class="whitespace-nowrap px-3 py-2 align-top text-slate-500">Block {{ element.blockId }}</td>
                  <td class="whitespace-pre-wrap px-3 py-2 align-top">{{ element.elementName }}</td>
                  <td class="whitespace-pre-wrap px-3 py-2 align-top">{{ formatTerms(element.keywordsZh) }}</td>
                  <td class="whitespace-pre-wrap px-3 py-2 align-top">{{ formatTerms(element.keywordsEn) }}</td>
                  <td class="whitespace-pre-wrap px-3 py-2 align-top">{{ formatTerms(element.ipcCpcRef) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="rounded-2xl bg-slate-50 px-3 py-3 text-[12px] leading-6 text-slate-600">
          {{ plan.strategySummary }}
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

type RawElement = Record<string, any>

type DisplayElement = {
  blockId: string
  elementName: string
  keywordsZh: string[]
  keywordsEn: string[]
  ipcCpcRef: string[]
}

type DisplayStep = {
  stepId: string
  title: string
  purpose: string
  activationMode: string
  activationSummary: string
}

const props = defineProps<{
  executionSpec?: Record<string, any> | null
}>()

const normalizeTerms = (value: any): string[] => (
  Array.isArray(value)
    ? value.map((item) => String(item || '').trim()).filter(Boolean)
    : []
)

const normalizeElement = (item: RawElement): DisplayElement => ({
  blockId: String(item.block_id || item.blockId || '').trim().toUpperCase() || '-',
  elementName: String(item.element_name || item.elementName || '').trim() || '未命名要素',
  keywordsZh: normalizeTerms(item.keywords_zh || item.keywordsZh),
  keywordsEn: normalizeTerms(item.keywords_en || item.keywordsEn),
  ipcCpcRef: normalizeTerms(item.ipc_cpc_ref || item.ipcCpcRef),
})

const normalizeStep = (item: RawElement, index: number): DisplayStep => ({
  stepId: String(item.step_id || item.stepId || `step_${index + 1}`).trim(),
  title: String(item.title || `步骤 ${index + 1}`).trim(),
  purpose: String(item.purpose || '').trim(),
  activationMode: String(item.activation_mode || item.activationMode || 'immediate').trim(),
  activationSummary: String(item.activation_summary || item.activationSummary || '').trim(),
})

const formatTerms = (terms: string[]): string => terms.length ? terms.join('；') : '-'

const normalizedPlans = computed(() => {
  const rawPlans = Array.isArray(props.executionSpec?.sub_plans) ? props.executionSpec.sub_plans : []
  return rawPlans
    .filter((item): item is Record<string, any> => !!item && typeof item === 'object')
    .map((plan, index) => {
      const retrievalSteps = Array.isArray(plan.retrieval_steps) ? plan.retrieval_steps : []
      const mainStep = retrievalSteps[0] && typeof retrievalSteps[0] === 'object' ? retrievalSteps[0] : null
      const queryBlueprints = Array.isArray(plan.query_blueprints) ? plan.query_blueprints : []
      const mainBlueprint = queryBlueprints[0] && typeof queryBlueprints[0] === 'object' ? queryBlueprints[0] : null
      const blueprintElements = Array.isArray(mainBlueprint?.display_search_elements)
        ? mainBlueprint.display_search_elements
        : []
      const strategyParts = [
        `语言：${String(mainStep?.language_strategy || '未填写').trim() || '未填写'}`,
        `IPC/CPC：${String(mainStep?.ipc_cpc_mode || '未填写').trim() || '未填写'}`,
        `目标召回：${String(mainStep?.expected_recall || '未填写').trim() || '未填写'}`,
      ]
      return {
        index: index + 1,
        subPlanId: String(plan.sub_plan_id || `sub_plan_${index + 1}`).trim(),
        goal: String(plan.goal || plan.title || `方案 ${index + 1}`).trim(),
        semanticQueryText: String(mainBlueprint?.semantic_text || plan.semantic_query_text || '').trim(),
        mainElements: (blueprintElements.length ? blueprintElements : (Array.isArray(plan.search_elements) ? plan.search_elements : []))
          .filter((item): item is RawElement => !!item && typeof item === 'object')
          .map(normalizeElement),
        strategySummary: strategyParts.join('；'),
        steps: retrievalSteps
          .filter((item): item is RawElement => !!item && typeof item === 'object')
          .map(normalizeStep),
      }
    })
})
</script>
