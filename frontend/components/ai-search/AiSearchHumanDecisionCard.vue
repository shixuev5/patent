<template>
  <section class="rounded-2xl border border-slate-300 bg-slate-50 px-4 py-4">
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
          :disabled="disabled"
          @click="$emit('complete-current-results')"
        >
          按当前结果完成
        </button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  action?: Record<string, any> | null
  disabled?: boolean
}>()

defineEmits<{
  'continue-search': []
  'complete-current-results': []
}>()

const summary = computed(() => String(props.action?.summary || '').trim())
const reason = computed(() => String(props.action?.reason || '').trim())
const roundCount = computed(() => Number(props.action?.roundCount || 0))
const noProgressRoundCount = computed(() => Number(props.action?.noProgressRoundCount || 0))
const selectedCount = computed(() => Number(props.action?.selectedCount || 0))
</script>
