<template>
  <div class="group relative">
    <button
      v-if="canCopy"
      type="button"
      class="absolute right-0 top-0 inline-flex h-8 w-8 items-center justify-center rounded-full border border-slate-200/80 bg-white/90 text-slate-500 shadow-sm transition md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100"
      aria-label="复制消息内容"
      title="复制"
      @click="copyContent"
    >
      <ClipboardDocumentIcon class="h-4 w-4" />
    </button>

    <div
      ref="contentWrapperRef"
      class="relative overflow-hidden pr-10 transition-[max-height] duration-200 ease-out"
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
import { ClipboardDocumentIcon } from '@heroicons/vue/24/outline'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AiSearchMarkdown from '~/components/ai-search/AiSearchMarkdown.vue'

const COLLAPSED_MAX_HEIGHT = 18 * 16

const props = withDefaults(defineProps<{
  content?: string
  mode?: 'markdown' | 'plaintext'
  canCopy?: boolean
  fadeRgb?: string
}>(), {
  content: '',
  mode: 'markdown',
  canCopy: true,
  fadeRgb: '248,250,252',
})

const { showMessage } = useGlobalMessage()

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

const copyContent = async () => {
  const text = String(props.content || '')
  if (!text.trim()) return
  try {
    await navigator.clipboard.writeText(text)
    showMessage('success', '已复制消息内容')
  } catch (_error) {
    showMessage('error', '复制失败，请稍后重试。')
  }
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
