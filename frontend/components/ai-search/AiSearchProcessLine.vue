<template>
  <article class="my-[-0.3rem] flex justify-start pl-2">
    <div class="w-full max-w-full text-[11px] leading-4 text-slate-400">
      <button
        v-if="hasChildren"
        type="button"
        class="flex w-full items-start gap-1 rounded-lg px-0.5 py-0 text-left transition hover:bg-slate-50/70"
        @click="toggleExpanded"
      >
        <span class="inline-flex h-4 w-3 shrink-0 items-center justify-center">
          <span class="inline-flex h-1.5 w-1.5 shrink-0 rounded-full" :class="statusDotClass" />
        </span>
        <span class="min-w-0 flex-1">
          <span class="inline-flex max-w-full items-center gap-1 align-top">
            <span class="break-words leading-4" :class="titleClass">
              {{ node.title }}
            </span>
            <ChevronRightIcon
              class="mt-[1px] h-3 w-3 shrink-0 text-slate-300 transition-transform"
              :class="{ 'rotate-90': expanded }"
            />
          </span>
        </span>
      </button>
      <div v-else class="flex w-full items-start gap-1 px-0.5 py-0">
        <span class="inline-flex h-4 w-3 shrink-0 items-center justify-center">
          <span class="inline-flex h-1.5 w-1.5 shrink-0 rounded-full" :class="statusDotClass" />
        </span>
        <span class="min-w-0 flex-1">
          <span class="block break-words leading-4" :class="titleClass">
            {{ node.title }}
          </span>
        </span>
      </div>

      <div v-if="hasChildren && expanded" class="ml-3 mt-0 space-y-0">
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
import { ChevronRightIcon } from '@heroicons/vue/24/outline'
import { computed, ref, watch } from 'vue'

defineOptions({ name: 'AiSearchProcessLine' })

type ProcessRenderNode = {
  id: string
  title: string
  status?: string
  level?: number
  isGroup?: boolean
  collapsible?: boolean
  defaultExpanded?: boolean
  autoCollapseOnTerminal?: boolean
  children?: ProcessRenderNode[]
}

const props = defineProps<{
  node: ProcessRenderNode
}>()

const status = computed(() => String(props.node?.status || '').trim())
const children = computed(() => Array.isArray(props.node?.children) ? props.node.children : [])
const hasChildren = computed(() => !!children.value.length && props.node?.collapsible !== false)
const isTerminal = computed(() => status.value === 'completed' || status.value === 'failed')
const expanded = ref(hasChildren.value ? !!props.node?.defaultExpanded : false)

const statusDotClass = computed(() => {
  if (status.value === 'completed') return 'bg-emerald-400/95'
  if (status.value === 'failed') return 'bg-rose-400/95'
  return 'bg-cyan-400/95'
})

const titleClass = computed(() => (
  props.node?.isGroup
    ? 'font-medium text-slate-500'
    : 'font-normal text-slate-400'
))

const toggleExpanded = () => {
  if (!hasChildren.value) return
  expanded.value = !expanded.value
}

watch(
  [hasChildren, () => props.node?.defaultExpanded, isTerminal, () => props.node?.autoCollapseOnTerminal],
  ([nextHasChildren, defaultExpanded, terminal, autoCollapse], [previousHasChildren, previousDefaultExpanded, previousTerminal]) => {
    if (!nextHasChildren) {
      expanded.value = false
      return
    }
    if (terminal && autoCollapse !== false) {
      expanded.value = false
      return
    }
    if (!previousHasChildren || previousTerminal || defaultExpanded !== previousDefaultExpanded) {
      expanded.value = !!defaultExpanded
    }
  },
  { immediate: true },
)
</script>
