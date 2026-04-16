<template>
  <div class="ai-search-markdown" :class="`theme-${theme}`" v-html="renderedHtml" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { aiSearchMarkdownRenderer } from '~/utils/aiSearchMarkdown'

const props = defineProps<{
  content?: string
  theme?: 'slate' | 'cyan'
}>()

const renderedHtml = computed(() => aiSearchMarkdownRenderer.render(String(props.content || '')))
const theme = computed(() => props.theme || 'slate')
</script>

<style scoped>
.ai-search-markdown {
  --ai-md-text: inherit;
  --ai-md-heading: #0f172a;
  --ai-md-muted: #475569;
  --ai-md-border: #cbd5e1;
  --ai-md-code-bg: rgba(226, 232, 240, 0.8);
  --ai-md-code-text: inherit;
  --ai-md-pre-bg: #0f172a;
  --ai-md-pre-text: #f8fafc;
  --ai-md-link: #0f7490;
  --ai-md-link-hover: #155e75;
  --ai-md-link-decoration: #67e8f9;
  --ai-md-table-border: #e2e8f0;
  --ai-md-table-head-bg: #f8fafc;
  --ai-md-table-head-text: #475569;
  --ai-md-table-cell-text: #334155;
  @apply break-words text-[13px] leading-5;
  color: var(--ai-md-text);
}

.ai-search-markdown.theme-cyan {
  --ai-md-text: rgba(240, 253, 250, 0.96);
  --ai-md-heading: #ffffff;
  --ai-md-muted: rgba(236, 254, 255, 0.9);
  --ai-md-border: rgba(236, 254, 255, 0.35);
  --ai-md-code-bg: rgba(8, 47, 73, 0.35);
  --ai-md-code-text: #ffffff;
  --ai-md-pre-bg: rgba(8, 47, 73, 0.55);
  --ai-md-pre-text: #f0fdfa;
  --ai-md-link: #ffffff;
  --ai-md-link-hover: #cffafe;
  --ai-md-link-decoration: rgba(236, 254, 255, 0.65);
  --ai-md-table-border: rgba(236, 254, 255, 0.22);
  --ai-md-table-head-bg: rgba(255, 255, 255, 0.08);
  --ai-md-table-head-text: #ecfeff;
  --ai-md-table-cell-text: rgba(240, 253, 250, 0.96);
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
  @apply my-1.5 list-inside pl-0;
}

.ai-search-markdown :deep(li) {
  @apply my-0.5;
}

.ai-search-markdown :deep(blockquote) {
  @apply my-2 border-l-2 pl-2.5;
  border-left-color: var(--ai-md-border);
  color: var(--ai-md-muted);
}

.ai-search-markdown :deep(pre) {
  @apply my-2 overflow-x-auto rounded-2xl px-3 py-2.5 text-[12px] leading-5;
  background: var(--ai-md-pre-bg);
  color: var(--ai-md-pre-text);
}

.ai-search-markdown :deep(code) {
  @apply rounded px-1 py-0.5 text-[13px];
  background: var(--ai-md-code-bg);
  color: var(--ai-md-code-text);
}

.ai-search-markdown :deep(pre code) {
  @apply bg-transparent px-0 py-0 text-inherit;
}

.ai-search-markdown :deep(table) {
  @apply my-2 min-w-full overflow-hidden rounded-2xl border text-left text-xs;
  border-color: var(--ai-md-table-border);
}

.ai-search-markdown :deep(thead) {
  background: var(--ai-md-table-head-bg);
  color: var(--ai-md-table-head-text);
}

.ai-search-markdown :deep(th) {
  @apply whitespace-nowrap px-3 py-1.5 font-semibold;
  border-bottom: 1px solid var(--ai-md-table-border);
}

.ai-search-markdown :deep(td) {
  @apply whitespace-pre-wrap px-3 py-1.5 align-top;
  border-bottom: 1px solid var(--ai-md-table-border);
  color: var(--ai-md-table-cell-text);
}

.ai-search-markdown :deep(a) {
  @apply font-medium underline underline-offset-2 transition;
  color: var(--ai-md-link);
  text-decoration-color: var(--ai-md-link-decoration);
}

.ai-search-markdown :deep(a:hover) {
  color: var(--ai-md-link-hover);
}

.ai-search-markdown :deep(h1),
.ai-search-markdown :deep(h2),
.ai-search-markdown :deep(h3) {
  @apply mt-3;
  color: var(--ai-md-heading);
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
