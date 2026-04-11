<template>
  <div class="group relative">
    <div
      ref="contentWrapperRef"
      class="relative overflow-hidden transition-[max-height] duration-200 ease-out"
      :style="contentWrapperStyle"
    >
      <div ref="contentInnerRef">
        <AiSearchMarkdown v-if="mode === 'markdown'" :content="content" />
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
        class="text-sm font-medium text-slate-500 transition hover:text-slate-700"
        @click="expanded = !expanded"
      >
        {{ expanded ? '收起' : '展示更多' }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AiSearchMarkdown from '~/components/ai-search/AiSearchMarkdown.vue'

const COLLAPSED_MAX_HEIGHT = 18 * 16

const props = withDefaults(defineProps<{
  content?: string
  mode?: 'markdown' | 'plaintext'
  fadeRgb?: string
}>(), {
  content: '',
  mode: 'markdown',
  fadeRgb: '248,250,252',
})

const expanded = ref(false)
const isOverflowing = ref(false)
const contentWrapperRef = ref<HTMLElement | null>(null)
const contentInnerRef = ref<HTMLElement | null>(null)
let resizeObserver: ResizeObserver | null = null

const showFadeOverlay = computed(() => isOverflowing.value && !expanded.value)
const contentWrapperStyle = computed(() => ({
  maxHeight: !isOverflowing.value || expanded.value ? 'none' : `${COLLAPSED_MAX_HEIGHT}px`,
}))
const fadeBackground = computed(() => `linear-gradient(to top, rgba(${props.fadeRgb}, 1), rgba(${props.fadeRgb}, 0.96) 45%, rgba(${props.fadeRgb}, 0))`)

const measureOverflow = async () => {
  await nextTick()
  const inner = contentInnerRef.value
  if (!inner) {
    isOverflowing.value = false
    return
  }
  isOverflowing.value = inner.scrollHeight > COLLAPSED_MAX_HEIGHT + 8
  if (!isOverflowing.value) expanded.value = false
}

watch(() => props.content, () => {
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
