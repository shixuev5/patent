<template>
  <article class="flex justify-start">
    <div class="max-w-full py-1 text-[12px] leading-6 text-slate-500">
      <div class="flex items-start gap-2">
        <span class="mt-2 inline-flex h-1.5 w-1.5 shrink-0 rounded-full" :class="statusDotClass" />
        <div class="min-w-0">
          <p class="break-words">
            <span v-if="prefix" class="text-slate-400">{{ prefix }} · </span>
            <span>{{ summary }}</span>
          </p>
          <p class="text-[11px] text-slate-400">{{ statusText }}</p>
        </div>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  entry: Record<string, any>
}>()

const metadata = computed<Record<string, any>>(() => {
  const value = props.entry?.metadata
  return value && typeof value === 'object' ? value as Record<string, any> : {}
})

const summary = computed(() => String(metadata.value.summary || props.entry?.content || '执行过程').trim())
const status = computed(() => String(metadata.value.status || '').trim())
const prefix = computed(() => {
  const processType = String(metadata.value.processType || '').trim()
  if (processType === 'tool') {
    return String(metadata.value.subagentLabel || '').trim()
  }
  return ''
})
const statusText = computed(() => {
  if (status.value === 'completed') return '已完成'
  if (status.value === 'failed') return String(metadata.value.errorMessage || '失败').trim()
  return '进行中'
})
const statusDotClass = computed(() => {
  if (status.value === 'completed') return 'bg-emerald-400'
  if (status.value === 'failed') return 'bg-rose-400'
  return 'bg-slate-400'
})
</script>
