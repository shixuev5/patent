<template>
  <button
    type="button"
    class="flex w-full min-w-0 items-center justify-between gap-2.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-left transition hover:border-slate-300 hover:bg-slate-50"
    @click="$emit('download')"
  >
    <div class="flex min-w-0 items-center gap-2.5">
      <span class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg" :class="iconBadgeClass">
        <component :is="fileIcon" class="h-4 w-4" />
      </span>
      <div class="min-w-0">
        <p class="truncate text-[12px] font-medium text-slate-800">{{ attachment.name }}</p>
        <p class="text-[11px] text-slate-400">{{ subtitle }}</p>
      </div>
    </div>
    <ArrowDownTrayIcon class="h-3.5 w-3.5 shrink-0 text-slate-400" />
  </button>
</template>

<script setup lang="ts">
import {
  ArchiveBoxIcon,
  ArrowDownTrayIcon,
  DocumentIcon,
  DocumentTextIcon,
  TableCellsIcon,
} from '@heroicons/vue/24/outline'
import { computed } from 'vue'
import type { AiSearchArtifactAttachment } from '~/types/aiSearch'

const props = defineProps<{
  attachment: AiSearchArtifactAttachment
}>()

defineEmits<{
  download: []
}>()

const normalizedExtension = computed(() => {
  const name = String(props.attachment.name || '').trim().toLowerCase()
  const matched = name.match(/\.([a-z0-9]+)$/)
  return matched?.[1] || ''
})

const fileKind = computed<'pdf' | 'sheet' | 'word' | 'zip' | 'generic'>(() => {
  const extension = normalizedExtension.value
  const mediaType = String(props.attachment.mediaType || '').toLowerCase()
  if (extension === 'pdf' || mediaType.includes('pdf')) return 'pdf'
  if (['csv', 'xls', 'xlsx'].includes(extension) || mediaType.includes('csv') || mediaType.includes('spreadsheet') || mediaType.includes('excel')) return 'sheet'
  if (['doc', 'docx'].includes(extension) || mediaType.includes('word') || mediaType.includes('document')) return 'word'
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(extension) || mediaType.includes('zip') || mediaType.includes('compressed')) return 'zip'
  return 'generic'
})

const fileIcon = computed(() => {
  if (fileKind.value === 'pdf') return DocumentTextIcon
  if (fileKind.value === 'sheet') return TableCellsIcon
  if (fileKind.value === 'word') return DocumentTextIcon
  if (fileKind.value === 'zip') return ArchiveBoxIcon
  return DocumentIcon
})

const iconBadgeClass = computed(() => {
  if (fileKind.value === 'pdf') return 'bg-rose-50 text-rose-500'
  if (fileKind.value === 'sheet') return 'bg-emerald-50 text-emerald-600'
  if (fileKind.value === 'word') return 'bg-sky-50 text-sky-600'
  if (fileKind.value === 'zip') return 'bg-amber-50 text-amber-600'
  return 'bg-slate-100 text-slate-500'
})

const formatFileSize = (value: number): string => {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${Math.round(value)} B`
}

const formatCreatedAt = (value: string): string => {
  const date = new Date(String(value || ''))
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

const mediaTypeLabel = computed(() => {
  const mediaType = String(props.attachment.mediaType || '').toLowerCase()
  if (mediaType.includes('zip')) return 'ZIP'
  if (mediaType.includes('csv')) return 'CSV'
  if (mediaType.includes('pdf')) return 'PDF'
  return '附件'
})

const subtitle = computed(() => {
  const parts = [
    mediaTypeLabel.value,
    formatFileSize(Number(props.attachment.sizeBytes || 0)),
    formatCreatedAt(props.attachment.createdAt),
  ].filter(Boolean)
  return parts.join(' · ')
})
</script>
