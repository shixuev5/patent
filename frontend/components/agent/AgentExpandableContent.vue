<template>
  <div class="group relative">
    <div
      ref="contentWrapperRef"
      class="relative overflow-hidden transition-[max-height] duration-200 ease-out"
      :style="contentWrapperStyle"
    >
      <div ref="contentInnerRef">
        <AgentMarkdown v-if="mode === 'markdown'" :content="content" :theme="theme" />
        <p v-else class="whitespace-pre-wrap break-words">{{ content }}</p>
      </div>
      <div
        v-if="showFadeOverlay"
        class="pointer-events-none absolute inset-x-0 bottom-0 h-20"
        :style="{ background: fadeBackground }"
      />
    </div>

    <div v-if="isOverflowing" class="mt-3 flex justify-end">
      <button
        type="button"
        class="text-sm font-medium transition"
        :class="expandButtonClass"
        @click="expanded = !expanded"
      >
        {{ expanded ? '收起' : '展示更多' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AgentMarkdown from '~/components/agent/AgentMarkdown.vue'

const DEFAULT_COLLAPSED_MAX_HEIGHT = 18 * 16

const props = withDefaults(defineProps<{
  content?: string
  mode?: 'markdown' | 'plaintext'
  theme?: 'slate' | 'bubble'
  fadeRgb?: string
  collapsedMaxHeight?: number
}>(), {
  content: '',
  mode: 'markdown',
  theme: 'slate',
  fadeRgb: '',
  collapsedMaxHeight: DEFAULT_COLLAPSED_MAX_HEIGHT,
})

const expanded = ref(false)
const isOverflowing = ref(false)
const contentWrapperRef = ref<HTMLElement | null>(null)
const contentInnerRef = ref<HTMLElement | null>(null)
let resizeObserver: ResizeObserver | null = null

const collapsedMaxHeight = computed(() => Math.max(120, Number(props.collapsedMaxHeight || DEFAULT_COLLAPSED_MAX_HEIGHT)))
const showFadeOverlay = computed(() => isOverflowing.value && !expanded.value)
const contentWrapperStyle = computed(() => ({
  maxHeight: !isOverflowing.value || expanded.value ? 'none' : `${collapsedMaxHeight.value}px`,
}))
const resolvedFadeRgb = computed(() => {
  if (String(props.fadeRgb || '').trim()) return props.fadeRgb
  return props.theme === 'bubble' ? '248,250,252' : '248,250,252'
})
const fadeBackground = computed(() => `linear-gradient(to top, rgba(${resolvedFadeRgb.value}, 1), rgba(${resolvedFadeRgb.value}, 0.96) 45%, rgba(${resolvedFadeRgb.value}, 0))`)
const expandButtonClass = computed(() => 'text-slate-400 hover:text-slate-600')

const measureOverflow = async () => {
  await nextTick()
  const inner = contentInnerRef.value
  if (!inner) {
    isOverflowing.value = false
    return
  }
  isOverflowing.value = inner.scrollHeight > collapsedMaxHeight.value + 8
  if (!isOverflowing.value) expanded.value = false
}

watch(() => [props.content, props.collapsedMaxHeight], () => {
  void measureOverflow()
})

onMounted(() => {
  void measureOverflow()
  if (typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver(() => {
    void measureOverflow()
  })
  if (contentInnerRef.value) resizeObserver.observe(contentInnerRef.value)
})

onBeforeUnmount(() => {
  if (resizeObserver) resizeObserver.disconnect()
})
</script>
