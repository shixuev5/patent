<template>
  <section v-if="items.length" class="mb-4 space-y-1.5">
    <div class="flex items-center gap-2 px-0.5">
      <span class="text-[11px] font-medium tracking-[0.14em] text-slate-400">AGENT ACTIVITY</span>
      <span v-if="activeCount > 0" class="text-[11px] text-slate-400">· {{ activeCount }} 项进行中</span>
    </div>

    <ol class="space-y-1.5">
      <li
        v-for="item in items"
        :key="item.traceId"
        class="grid grid-cols-[1rem,minmax(0,1fr),auto] items-start gap-3 rounded-xl px-0.5 py-1"
      >
        <span class="mt-1 inline-flex h-4 w-4 items-center justify-center">
          <ArrowPathIcon
            v-if="item.status === 'running' && item.traceType === 'thinking'"
            class="h-4 w-4 animate-spin text-slate-500"
          />
          <CpuChipIcon
            v-else-if="item.traceType === 'agent'"
            class="h-4 w-4"
            :class="item.status === 'failed' ? 'text-rose-500' : item.status === 'running' ? 'text-slate-500' : 'text-slate-400'"
          />
          <WrenchScrewdriverIcon
            v-else-if="item.traceType === 'tool'"
            class="h-4 w-4"
            :class="item.status === 'failed' ? 'text-rose-500' : item.status === 'running' ? 'text-slate-500' : 'text-slate-400'"
          />
          <CheckCircleIcon
            v-else-if="item.status === 'completed'"
            class="h-4 w-4 text-slate-400"
          />
          <XCircleIcon
            v-else-if="item.status === 'failed'"
            class="h-4 w-4 text-rose-500"
          />
          <div
            v-else
            class="h-2.5 w-2.5 rounded-full"
            :class="item.status === 'running' ? 'bg-cyan-500' : 'bg-slate-300'"
          />
        </span>

        <div class="min-w-0">
          <div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <p class="truncate text-[13px] leading-5 text-slate-700">
              {{ summaryText(item) }}
            </p>
            <span class="text-[11px]" :class="statusClass(item)">
              {{ statusLabel(item) }}
            </span>
          </div>
          <p v-if="item.detail" class="mt-0.5 text-[12px] leading-5 text-slate-400">
            {{ item.detail }}
          </p>
        </div>

        <p class="whitespace-nowrap pt-0.5 text-[11px] text-slate-400">
          {{ durationText(item) }}
        </p>
      </li>
    </ol>
  </section>
</template>

<script setup lang="ts">
import { ArrowPathIcon, CheckCircleIcon, CpuChipIcon, WrenchScrewdriverIcon, XCircleIcon } from '@heroicons/vue/24/outline'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { AiSearchActivityTrace } from '~/types/aiSearch'

const props = defineProps<{
  items: AiSearchActivityTrace[]
}>()

const nowTick = ref(Date.now())
let nowTimer: ReturnType<typeof setInterval> | null = null

const activeCount = computed(() => props.items.filter(item => item.status === 'running').length)

const toMillis = (value?: string | null): number => {
  const ts = Date.parse(String(value || ''))
  return Number.isFinite(ts) ? ts : 0
}

const formatDuration = (durationMs: number): string => {
  const totalSeconds = Math.max(1, Math.floor(durationMs / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes > 0) return `${minutes}分${seconds}秒`
  return `${seconds}秒`
}

const durationText = (item: AiSearchActivityTrace): string => {
  const startedAt = toMillis(item.startedAt)
  if (!startedAt) return ''
  const endedAt = toMillis(item.endedAt)
  if (endedAt > startedAt) return formatDuration(endedAt - startedAt)
  if (item.status === 'running') return `进行中 · ${formatDuration(nowTick.value - startedAt)}`
  return ''
}

const statusLabel = (item: AiSearchActivityTrace): string => {
  if (item.status === 'failed') return '失败'
  if (item.status === 'completed') return '已完成'
  if (item.traceType === 'thinking') return '思考中'
  return '运行中'
}

const statusClass = (item: AiSearchActivityTrace): string => {
  if (item.status === 'failed') return 'text-rose-500'
  if (item.status === 'completed') return 'text-slate-400'
  return 'text-slate-500'
}

const summaryText = (item: AiSearchActivityTrace): string => {
  if (item.traceType === 'thinking') return item.label || '主 agent 思考中'
  if (item.traceType === 'agent') {
    return item.actorName ? `${item.label} · ${item.actorName}` : item.label
  }
  return item.label
}

onMounted(() => {
  nowTimer = setInterval(() => {
    nowTick.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => {
  if (nowTimer) {
    clearInterval(nowTimer)
    nowTimer = null
  }
})
</script>
