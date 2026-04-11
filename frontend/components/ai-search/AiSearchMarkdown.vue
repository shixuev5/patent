<template>
  <div class="ai-search-markdown" v-html="renderedHtml" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { aiSearchMarkdownRenderer } from '~/utils/aiSearchMarkdown'

const props = defineProps<{
  content?: string
}>()

const renderedHtml = computed(() => aiSearchMarkdownRenderer.render(String(props.content || '')))
</script>

<style scoped>
.ai-search-markdown {
  @apply break-words text-[13px] leading-5 text-inherit;
}

.ai-search-markdown :deep(:first-child) {
  @apply mt-0;
}

.ai-search-markdown :deep(:last-child) {
  @apply mb-0;
}

.ai-search-markdown :deep(p) {
  @apply my-1.5;
}

.ai-search-markdown :deep(ul),
.ai-search-markdown :deep(ol) {
  @apply my-1.5 pl-4;
}

.ai-search-markdown :deep(li) {
  @apply my-0.5;
}

.ai-search-markdown :deep(blockquote) {
  @apply my-2 border-l-2 border-slate-300 pl-2.5 text-slate-600;
}

.ai-search-markdown :deep(pre) {
  @apply my-2 overflow-x-auto rounded-2xl bg-slate-900 px-3 py-2.5 text-[12px] leading-5 text-slate-100;
}

.ai-search-markdown :deep(code) {
  @apply rounded bg-slate-200/80 px-1 py-0.5 text-[13px];
}

.ai-search-markdown :deep(pre code) {
  @apply bg-transparent px-0 py-0 text-inherit;
}

.ai-search-markdown :deep(table) {
  @apply my-2 min-w-full divide-y divide-slate-200 overflow-hidden rounded-2xl border border-slate-200 text-left text-xs;
}

.ai-search-markdown :deep(thead) {
  @apply bg-slate-50 text-slate-600;
}

.ai-search-markdown :deep(th) {
  @apply whitespace-nowrap px-3 py-1.5 font-semibold;
}

.ai-search-markdown :deep(td) {
  @apply whitespace-pre-wrap px-3 py-1.5 align-top text-slate-700;
}

.ai-search-markdown :deep(a) {
  @apply font-medium text-cyan-700 underline decoration-cyan-300 underline-offset-2 transition hover:text-cyan-800;
}

.ai-search-markdown :deep(h1),
.ai-search-markdown :deep(h2),
.ai-search-markdown :deep(h3) {
  @apply mt-3 text-slate-900;
}

.ai-search-markdown :deep(h1) {
  @apply text-lg font-semibold;
}

.ai-search-markdown :deep(h2) {
  @apply text-base font-semibold;
}

.ai-search-markdown :deep(h3) {
  @apply text-sm font-semibold;
}
</style>
