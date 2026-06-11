<template>
  <div class="agent-markdown" :class="`theme-${theme}`" v-html="renderedHtml" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { aiSearchMarkdownRenderer } from '~/utils/aiSearchMarkdown'

const props = withDefaults(defineProps<{
  content?: string
  theme?: 'slate' | 'bubble'
}>(), {
  content: '',
  theme: 'slate',
})

const renderedHtml = computed(() => aiSearchMarkdownRenderer.render(String(props.content || '')))
</script>

<style scoped>
.agent-markdown {
  --agent-md-text: inherit;
  --agent-md-heading: #0f172a;
  --agent-md-muted: #475569;
  --agent-md-border: #cbd5e1;
  --agent-md-code-bg: rgba(226, 232, 240, 0.8);
  --agent-md-code-text: inherit;
  --agent-md-pre-bg: #0f172a;
  --agent-md-pre-text: #f8fafc;
  --agent-md-link: #0f7490;
  --agent-md-link-hover: #155e75;
  --agent-md-link-decoration: #67e8f9;
  --agent-md-table-border: #e2e8f0;
  --agent-md-table-head-bg: #f8fafc;
  --agent-md-table-head-text: #475569;
  --agent-md-table-cell-text: #334155;
  @apply break-words text-[14px] leading-6;
  color: var(--agent-md-text);
}

.agent-markdown.theme-bubble {
  --agent-md-text: #0f172a;
  --agent-md-heading: #0f172a;
  --agent-md-muted: #475569;
  --agent-md-border: #cbd5e1;
  --agent-md-code-bg: rgba(226, 232, 240, 0.95);
  --agent-md-link: #0f7490;
}

.agent-markdown :deep(:first-child) {
  @apply mt-0;
}

.agent-markdown :deep(:last-child) {
  @apply mb-0;
}

.agent-markdown :deep(p) {
  @apply my-1.5;
}

.agent-markdown :deep(ul),
.agent-markdown :deep(ol) {
  @apply my-1.5 list-inside pl-0;
}

.agent-markdown :deep(li) {
  @apply my-0.5;
}

.agent-markdown :deep(blockquote) {
  @apply my-2 border-l-2 pl-2.5;
  border-left-color: var(--agent-md-border);
  color: var(--agent-md-muted);
}

.agent-markdown :deep(pre) {
  @apply my-2 overflow-x-auto rounded-xl px-3 py-2.5 text-[12px] leading-5;
  background: var(--agent-md-pre-bg);
  color: var(--agent-md-pre-text);
}

.agent-markdown :deep(code) {
  @apply rounded px-1 py-0.5 text-[13px];
  background: var(--agent-md-code-bg);
  color: var(--agent-md-code-text);
}

.agent-markdown :deep(pre code) {
  @apply bg-transparent px-0 py-0 text-inherit;
}

.agent-markdown :deep(table) {
  @apply my-2 min-w-full overflow-hidden rounded-xl border text-left text-xs;
  border-color: var(--agent-md-table-border);
}

.agent-markdown :deep(thead) {
  background: var(--agent-md-table-head-bg);
  color: var(--agent-md-table-head-text);
}

.agent-markdown :deep(th) {
  @apply whitespace-nowrap px-3 py-1.5 font-semibold;
  border-bottom: 1px solid var(--agent-md-table-border);
}

.agent-markdown :deep(td) {
  @apply whitespace-pre-wrap px-3 py-1.5 align-top;
  border-bottom: 1px solid var(--agent-md-table-border);
  color: var(--agent-md-table-cell-text);
}

.agent-markdown :deep(a) {
  @apply font-medium underline underline-offset-2 transition;
  color: var(--agent-md-link);
  text-decoration-color: var(--agent-md-link-decoration);
}

.agent-markdown :deep(a:hover) {
  color: var(--agent-md-link-hover);
}

.agent-markdown :deep(h1),
.agent-markdown :deep(h2),
.agent-markdown :deep(h3) {
  @apply mt-3;
  color: var(--agent-md-heading);
}

.agent-markdown :deep(h1) {
  @apply text-lg font-semibold;
}

.agent-markdown :deep(h2) {
  @apply text-base font-semibold;
}

.agent-markdown :deep(h3) {
  @apply text-sm font-semibold;
}
</style>
