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

        <div class="space-y-3">
          <p class="text-[11px] font-semibold tracking-[0.16em] text-slate-400">进一步检索</p>
          <div v-if="plan.followUps.length" class="space-y-3">
            <article
              v-for="followUp in plan.followUps"
              :key="followUp.batchId"
              class="rounded-2xl border border-slate-200 bg-slate-50/70 px-3 py-3"
            >
              <p class="text-sm font-semibold text-slate-900">{{ followUp.label }}</p>
              <p class="mt-2 text-[13px] leading-6 text-slate-700">{{ followUp.purpose }}</p>
              <p v-if="followUp.semanticText" class="mt-2 text-[13px] leading-6 text-slate-600">
                {{ followUp.semanticText }}
              </p>
              <div class="mt-3 overflow-x-auto rounded-2xl border border-slate-200 bg-white">
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
                    <tr v-for="element in followUp.elements" :key="`${followUp.batchId}-${element.blockId}-${element.elementName}`">
                      <td class="whitespace-nowrap px-3 py-2 align-top text-slate-500">Block {{ element.blockId }}</td>
                      <td class="whitespace-pre-wrap px-3 py-2 align-top">{{ element.elementName }}</td>
                      <td class="whitespace-pre-wrap px-3 py-2 align-top">{{ formatTerms(element.keywordsZh) }}</td>
                      <td class="whitespace-pre-wrap px-3 py-2 align-top">{{ formatTerms(element.keywordsEn) }}</td>
                      <td class="whitespace-pre-wrap px-3 py-2 align-top">{{ formatTerms(element.ipcCpcRef) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </article>
          </div>
          <p v-else class="text-[13px] leading-6 text-slate-500">当前未拆出从属于该 5 分效果的 4 分进一步检索。</p>
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

const formatTerms = (terms: string[]): string => terms.length ? terms.join('；') : '-'

const normalizedPlans = computed(() => {
  const rawPlans = Array.isArray(props.executionSpec?.sub_plans)
    ? props.executionSpec?.sub_plans
    : Array.isArray(props.executionSpec?.subPlans)
      ? props.executionSpec?.subPlans
      : []
  return rawPlans
    .filter((item): item is Record<string, any> => !!item && typeof item === 'object')
    .map((plan, index) => {
      const blueprints = Array.isArray(plan.query_blueprints)
        ? plan.query_blueprints
        : Array.isArray(plan.queryBlueprints)
          ? plan.queryBlueprints
          : []
      const blueprintMap = new Map(
        blueprints
          .filter((item): item is Record<string, any> => !!item && typeof item === 'object')
          .map((item) => [String(item.batch_id || item.batchId || '').trim(), item]),
      )
      const retrievalSteps = Array.isArray(plan.retrieval_steps)
        ? plan.retrieval_steps
        : Array.isArray(plan.retrievalSteps)
          ? plan.retrievalSteps
          : []
      const mainStep = retrievalSteps[0] && typeof retrievalSteps[0] === 'object' ? retrievalSteps[0] : null
      const followUps = retrievalSteps
        .slice(1)
        .filter((item): item is Record<string, any> => !!item && typeof item === 'object')
        .map((step) => {
          const refs = Array.isArray(step.query_blueprint_refs)
            ? step.query_blueprint_refs
            : Array.isArray(step.queryBlueprintRefs)
              ? step.queryBlueprintRefs
              : []
          const batchId = String(refs[0] || '').trim()
          const blueprint = blueprintMap.get(batchId) || {}
          const rawElements = Array.isArray(blueprint.display_search_elements)
            ? blueprint.display_search_elements
            : Array.isArray(blueprint.displaySearchElements)
              ? blueprint.displaySearchElements
              : []
          return {
            batchId: batchId || String(step.step_id || step.stepId || '').trim(),
            label: String(blueprint.display_label || blueprint.displayLabel || step.title || '进一步检索').trim(),
            purpose: String(step.purpose || '').trim() || '围绕该 4 分效果做进一步检索。',
            semanticText: String(blueprint.semantic_text || blueprint.semanticText || '').trim(),
            elements: rawElements
              .filter((item): item is RawElement => !!item && typeof item === 'object')
              .map(normalizeElement),
          }
        })
      const strategyParts = [
        `语言：${String(mainStep?.language_strategy || mainStep?.languageStrategy || '未填写').trim() || '未填写'}`,
        `IPC/CPC：${String(mainStep?.ipc_cpc_mode || mainStep?.ipcCpcMode || '未填写').trim() || '未填写'}`,
        `目标召回：${String(mainStep?.expected_recall || mainStep?.expectedRecall || '未填写').trim() || '未填写'}`,
      ]
      return {
        index: index + 1,
        subPlanId: String(plan.sub_plan_id || plan.subPlanId || `sub_plan_${index + 1}`).trim(),
        goal: String(plan.goal || plan.title || `方案 ${index + 1}`).trim(),
        semanticQueryText: String(plan.semantic_query_text || plan.semanticQueryText || '').trim(),
        mainElements: (Array.isArray(plan.search_elements) ? plan.search_elements : [])
          .filter((item): item is RawElement => !!item && typeof item === 'object')
          .map(normalizeElement),
        followUps,
        strategySummary: strategyParts.join('；'),
      }
    })
})
</script>
