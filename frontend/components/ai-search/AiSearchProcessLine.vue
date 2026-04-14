<template>
  <article class="flex justify-start">
    <div class="w-full max-w-full py-1 text-[12px] leading-6 text-slate-500">
      <div class="rounded-2xl border border-slate-200/80 bg-slate-50/70 px-3 py-2" :class="containerClass">
        <div class="flex items-start gap-2.5">
          <span class="mt-1.5 inline-flex h-2 w-2 shrink-0 rounded-full" :class="statusDotClass" />
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <p class="min-w-0 break-words text-[13px] font-medium" :class="titleClass">
                {{ node.title }}
              </p>
              <span class="rounded-full px-2 py-0.5 text-[10px] font-semibold" :class="statusBadgeClass">
                {{ statusLabel }}
              </span>
            </div>
          </div>
        </div>
      </div>
      <div v-if="children.length" class="mt-2 space-y-1.5 border-l border-slate-200 pl-4">
        <AiSearchProcessLine
          v-for="child in children"
          :key="child.id"
          :node="child"
        />
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'

defineOptions({ name: 'AiSearchProcessLine' })

type ProcessRenderNode = {
  id: string
  title: string
  status?: string
  level?: number
  isGroup?: boolean
  children?: ProcessRenderNode[]
}

const props = defineProps<{
  node: ProcessRenderNode
}>()

const status = computed(() => String(props.node?.status || '').trim())
const children = computed(() => Array.isArray(props.node?.children) ? props.node.children : [])
const statusLabel = computed(() => {
  if (status.value === 'completed') return '已完成'
  if (status.value === 'failed') return '失败'
  return '进行中'
})
const statusDotClass = computed(() => {
  if (status.value === 'completed') return 'bg-emerald-400'
  if (status.value === 'failed') return 'bg-rose-400'
  return 'bg-cyan-500'
})
const statusBadgeClass = computed(() => {
  if (status.value === 'completed') return 'bg-emerald-100 text-emerald-700'
  if (status.value === 'failed') return 'bg-rose-100 text-rose-700'
  return 'bg-cyan-100 text-cyan-700'
})
const titleClass = computed(() => (props.node?.isGroup ? 'text-slate-800' : 'text-slate-700'))
const containerClass = computed(() => (props.node?.isGroup ? 'bg-slate-50 border-slate-200' : 'bg-white border-slate-200/70'))
</script>
