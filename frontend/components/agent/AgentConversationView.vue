<template>
  <div class="relative min-h-0 flex-1">
    <div ref="messageListRef" class="flex h-full min-h-0 flex-col overflow-y-auto px-3 py-4 sm:px-5">
      <div v-if="!entries.length" class="flex min-h-full flex-1 items-center justify-center px-4 py-8 text-center text-sm text-slate-500">
        {{ emptyText }}
      </div>

      <div v-else class="mx-auto flex w-full max-w-[56rem] flex-col gap-3">
        <template v-for="entry in entries" :key="entry.id">
          <article v-if="entry.entryType === 'phase'" class="flex items-center gap-3 py-1">
            <span class="h-px flex-1 bg-slate-200/80" />
            <p class="shrink-0 text-[11px] font-medium text-slate-400">
              {{ resolvedPhaseLabel(entry.phase) }}<span v-if="phaseDurationText(entry)"> · {{ phaseDurationText(entry) }}</span>
            </p>
            <span class="h-px flex-1 bg-slate-200/80" />
          </article>

          <article v-else-if="entry.entryType === 'trace-summary'" class="flex justify-start">
            <details class="group/trace w-full">
              <summary class="agent-summary flex cursor-pointer items-center gap-2 rounded-lg px-1 py-1 text-[12px] leading-5 text-slate-400 transition hover:bg-slate-50 hover:text-slate-600">
                <span class="inline-flex h-4 w-4 shrink-0 items-center justify-center">
                  <CheckCircleIcon class="h-4 w-4 text-slate-300" />
                </span>
                <p class="min-w-0 flex-1 truncate">
                  {{ entry.label }}
                </p>
                <span v-if="traceDurationText(entry)" class="shrink-0 text-[11px]">
                  {{ traceDurationText(entry) }}
                </span>
                <ChevronRightIcon class="h-3.5 w-3.5 shrink-0 transition group-open/trace:rotate-90" />
              </summary>
              <div class="ml-3 mt-1 space-y-1 border-l border-slate-100 pl-4">
                <div
                  v-for="item in traceSummaryItems(entry)"
                  :key="item.traceId || item.id"
                  class="flex items-start gap-2 py-1 text-[12px] leading-5 text-slate-500"
                >
                  <span class="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center">
                    <component :is="traceIcon(item)" class="h-4 w-4" :class="traceIconClass(item)" />
                  </span>
                  <div class="min-w-0 flex-1">
                    <p class="break-words">{{ traceHistoryText(item) }}</p>
                    <p v-if="item.detail" class="mt-0.5 break-words text-[11px] text-slate-400">
                      {{ item.detail }}
                    </p>
                    <div v-if="traceDetailBlocks(item).length" class="mt-2 grid gap-1.5">
                      <div
                        v-for="block in traceDetailBlocks(item)"
                        :key="block.label"
                        class="rounded-lg border border-slate-200 bg-white px-2.5 py-2"
                      >
                        <p class="text-[11px] font-semibold text-slate-500">{{ block.label }}</p>
                        <pre class="mt-1 max-h-36 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-600">{{ block.value }}</pre>
                      </div>
                    </div>
                    <div v-if="traceChildren(item).length" class="mt-2 space-y-1 border-l border-slate-100 pl-3">
                      <div
                        v-for="child in traceChildren(item)"
                        :key="child.traceId || child.id"
                        class="text-[11px] leading-5 text-slate-500"
                      >
                        <span class="font-medium">{{ traceTypeLabel(child) }}</span>
                        <span> · {{ traceInlineText(child) }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </details>
          </article>

          <article v-else-if="entry.entryType === 'trace' && isAgentTrace(entry)" class="flex justify-start">
            <details
              class="group/agent-card w-full rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2"
              :open="traceDefaultOpen(entry)"
            >
              <summary class="agent-summary flex cursor-pointer items-start gap-2">
                <span class="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-white text-cyan-700 ring-1 ring-slate-200">
                  <CpuChipIcon class="h-3.5 w-3.5" />
                </span>
                <div class="min-w-0 flex-1">
                  <div class="flex min-w-0 items-center gap-2">
                    <span class="shrink-0 text-[12px] font-semibold text-slate-700">子 Agent</span>
                    <span v-if="traceActorText(entry)" class="min-w-0 truncate text-[11px] text-slate-400">
                      {{ traceActorText(entry) }}
                    </span>
                    <span v-if="traceDurationText(entry)" class="shrink-0 text-[11px] text-slate-400">
                      {{ traceDurationText(entry) }}
                    </span>
                  </div>
                  <p class="mt-0.5 break-words text-[13px] leading-5 text-slate-600">
                    {{ traceInlineText(entry) }}
                  </p>
                </div>
                <ChevronRightIcon class="mt-1 h-3.5 w-3.5 shrink-0 text-slate-300 transition group-open/agent-card:rotate-90" />
              </summary>

              <div class="mt-2 space-y-2 border-t border-slate-200 pt-2">
                <p v-if="traceDetailText(entry)" class="whitespace-pre-wrap break-words text-[12px] leading-5 text-slate-500">
                  {{ traceDetailText(entry) }}
                </p>
                <div
                  v-for="block in traceDetailBlocks(entry)"
                  :key="block.label"
                  class="rounded-lg border border-slate-200 bg-white px-3 py-2"
                >
                  <p class="text-[11px] font-semibold text-slate-500">{{ block.label }}</p>
                  <pre class="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-600">{{ block.value }}</pre>
                </div>
              </div>
            </details>
          </article>

          <article v-else-if="entry.entryType === 'trace'" class="flex justify-start">
            <details
              v-if="traceCanExpand(entry)"
              class="group/trace w-full"
              :open="traceDefaultOpen(entry)"
            >
              <summary
                class="agent-summary flex cursor-pointer items-start gap-2 rounded-lg px-1 py-1.5 transition"
                :class="traceSummaryClass(entry)"
              >
                <span class="mt-1 inline-flex h-4 w-4 shrink-0 items-center justify-center">
                  <ArrowPathIcon
                    v-if="entry.status === 'running'"
                    class="h-4 w-4 animate-spin text-cyan-600"
                  />
                  <component
                    :is="traceIcon(entry)"
                    v-else
                    class="h-4 w-4"
                    :class="traceIconClass(entry)"
                  />
                </span>
                <div class="min-w-0 flex-1">
                  <div class="flex min-w-0 items-center gap-2">
                    <span class="shrink-0 text-[12px] font-semibold text-slate-700">
                      {{ traceTypeLabel(entry) }}
                    </span>
                    <span v-if="traceActorText(entry)" class="min-w-0 truncate text-[11px] text-slate-400">
                      {{ traceActorText(entry) }}
                    </span>
                    <span v-if="traceDurationText(entry)" class="shrink-0 text-[11px] text-slate-400">
                      {{ traceDurationText(entry) }}
                    </span>
                  </div>
                  <p class="mt-0.5 min-w-0 break-words text-[13px] leading-6">
                    {{ traceInlineText(entry) }}
                  </p>
                </div>
                <ChevronRightIcon class="mt-1 h-3.5 w-3.5 shrink-0 text-slate-300 transition group-open/trace:rotate-90" />
              </summary>

              <div class="ml-3 mt-1 space-y-2 border-l border-slate-100 pl-4">
                <p v-if="traceDetailText(entry)" class="whitespace-pre-wrap break-words text-[12px] leading-5 text-slate-500">
                  {{ traceDetailText(entry) }}
                </p>
                <div
                  v-for="block in traceDetailBlocks(entry)"
                  :key="block.label"
                  class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2"
                >
                  <p class="text-[11px] font-semibold text-slate-500">{{ block.label }}</p>
                  <pre class="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-600">{{ block.value }}</pre>
                </div>
                <div v-if="traceChildren(entry).length" class="space-y-1.5">
                  <p class="text-[11px] font-semibold text-slate-500">子步骤</p>
                  <div
                    v-for="child in traceChildren(entry)"
                    :key="child.traceId || child.id"
                    class="rounded-lg border border-slate-200 bg-white px-3 py-2"
                  >
                    <div class="flex items-start gap-2 text-[12px] leading-5 text-slate-600">
                      <component :is="traceIcon(child)" class="mt-0.5 h-4 w-4 shrink-0" :class="traceIconClass(child)" />
                      <div class="min-w-0 flex-1">
                        <p class="break-words">
                          <span class="font-medium">{{ traceTypeLabel(child) }}</span>
                          <span> · {{ traceInlineText(child) }}</span>
                        </p>
                        <p v-if="traceDetailText(child)" class="mt-0.5 break-words text-[11px] text-slate-400">
                          {{ traceDetailText(child) }}
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </details>

            <div
              v-else
              class="flex w-full items-center gap-2 rounded-lg px-1 py-1 text-[12px] leading-5"
              :class="traceHistoryToneClass(entry)"
            >
              <span class="inline-flex h-4 w-4 shrink-0 items-center justify-center">
                <ArrowPathIcon
                  v-if="entry.status === 'running'"
                  class="h-4 w-4 animate-spin text-cyan-600"
                />
                <component v-else :is="traceIcon(entry)" class="h-4 w-4" :class="traceIconClass(entry)" />
              </span>
              <p class="min-w-0 flex-1 truncate">
                {{ traceHistoryText(entry) }}
              </p>
              <span v-if="traceDurationText(entry)" class="shrink-0 text-[11px]">
                {{ traceDurationText(entry) }}
              </span>
            </div>
          </article>

          <article
            v-else
            class="group/message flex"
            :class="entry.role === 'user' ? 'justify-end' : 'justify-start'"
          >
            <div :class="messageWrapperClass(entry)">
              <div
                class="text-[14px] leading-6"
                :class="messageCardClass(entry)"
              >
                <AgentExpandableContent
                  v-if="entry.role === 'assistant'"
                  :content="entry.content"
                  mode="markdown"
                  theme="slate"
                />

                <AgentExpandableContent
                  v-else
                  :content="entry.content"
                  :mode="userMessageMode(entry)"
                  theme="bubble"
                />
              </div>

              <div
                v-if="canCopyEntry(entry)"
                class="flex justify-end px-0.5 pt-1 opacity-100 transition md:pointer-events-none md:opacity-0 md:group-hover/message:pointer-events-auto md:group-hover/message:opacity-100 md:group-focus-within/message:pointer-events-auto md:group-focus-within/message:opacity-100"
              >
                <button
                  type="button"
                  class="inline-flex h-4 w-4 items-center justify-center text-slate-400 transition hover:text-cyan-700"
                  aria-label="复制消息内容"
                  title="复制"
                  @click="copyEntryContent(entry)"
                >
                  <ClipboardDocumentIcon class="h-4 w-4" />
                </button>
              </div>

              <div
                v-if="downloadAttachmentsForEntry(entry).length"
                class="grid max-w-[33rem] grid-cols-1 gap-2 pt-2 sm:grid-cols-2"
              >
                <AgentDownloadAttachment
                  v-for="attachment in downloadAttachmentsForEntry(entry)"
                  :key="attachment.attachmentId"
                  :attachment="attachment"
                  @download="$emit('download-result', attachment)"
                />
              </div>
            </div>
          </article>
        </template>
      </div>
    </div>

    <button
      v-if="showScrollToBottom"
      type="button"
      class="absolute bottom-4 right-4 inline-flex h-11 w-11 items-center justify-center rounded-full border border-cyan-200/80 bg-cyan-50/95 text-cyan-700 shadow-lg shadow-cyan-200/40 transition hover:bg-cyan-100"
      aria-label="滚动到最新消息"
      title="滚动到最新消息"
      @click="scrollToLatest"
    >
      <ArrowDownIcon class="h-5 w-5" />
    </button>
  </div>
</template>

<script setup lang="ts">
import {
  ArrowDownIcon,
  ArrowPathIcon,
  CheckCircleIcon,
  ChevronRightIcon,
  ClipboardDocumentIcon,
  CpuChipIcon,
  WrenchScrewdriverIcon,
  XCircleIcon,
} from '@heroicons/vue/24/outline'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AgentDownloadAttachment from '~/components/agent/AgentDownloadAttachment.vue'
import AgentExpandableContent from '~/components/agent/AgentExpandableContent.vue'

interface AgentConversationAttachment {
  attachmentId: string
  kind?: string
  name: string
  downloadUrl?: string
  mediaType?: string
  sizeBytes?: number
  createdAt?: string
  isPrimary?: boolean
}

type ConversationEntry = Record<string, any>

const props = withDefaults(defineProps<{
  sessionId?: string
  entries: ConversationEntry[]
  phase?: string
  attachments?: AgentConversationAttachment[]
  streaming?: boolean
  emptyText?: string
  phaseLabel?: (phase: string) => string
}>(), {
  sessionId: '',
  phase: '',
  attachments: () => [],
  streaming: false,
  emptyText: '描述任务目标，或直接与 agent 对话。',
  phaseLabel: undefined,
})

defineEmits<{
  'download-result': [attachment: AgentConversationAttachment]
}>()

const { showMessage } = useGlobalMessage()
const messageListRef = ref<HTMLElement | null>(null)
const isNearBottom = ref(true)
const hasScrollableOverflow = ref(false)
const nowTick = ref(Date.now())
let nowTimer: ReturnType<typeof setInterval> | null = null

const showScrollToBottom = computed(() => hasScrollableOverflow.value && !isNearBottom.value)
const latestEntryFingerprint = computed(() => {
  const last = props.entries.at(-1) || {}
  return [
    props.entries.length,
    last.id,
    last.entryType,
    last.role,
    last.status,
    last.label,
    last.content,
    last.endedAt,
  ].map(value => String(value || '')).join('|')
})

const toMillis = (value?: string | null): number => {
  const ts = Date.parse(String(value || ''))
  return Number.isFinite(ts) ? ts : 0
}

const formatDuration = (durationMs: number): string => {
  const totalSeconds = Math.max(1, Math.floor(durationMs / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) return `${hours}小时${minutes}分`
  if (minutes > 0) return `${minutes}分${seconds}秒`
  return `${seconds}秒`
}

const resolvedPhaseLabel = (phase: string): string => {
  const normalized = String(phase || '').trim()
  if (typeof props.phaseLabel === 'function') return props.phaseLabel(normalized)
  return normalized || '处理'
}

const phaseDurationText = (entry: ConversationEntry): string => {
  const startedAt = toMillis(entry.createdAt)
  if (!startedAt) return ''
  const endedAt = toMillis(entry.endedAt)
  if (endedAt > startedAt) return formatDuration(endedAt - startedAt)
  const isLatestPhaseEntry = props.entries.filter(item => item.entryType === 'phase').at(-1)?.id === entry.id
  if (!isLatestPhaseEntry) return ''
  return `进行中 · ${formatDuration(nowTick.value - startedAt)}`
}

const entryMetadata = (entry: ConversationEntry): Record<string, any> => {
  const value = entry?.metadata
  return value && typeof value === 'object' ? value as Record<string, any> : {}
}

const isUserMarkdownEntry = (entry: ConversationEntry): boolean => {
  if (entry.role !== 'user') return false
  const metadata = entryMetadata(entry)
  return (
    String(metadata.render_mode || '').trim() === 'markdown'
    || String(metadata.message_variant || '').trim() === 'analysis_seed_context'
  )
}

const messageWrapperClass = (entry: ConversationEntry): string => (
  entry.role === 'assistant' ? 'w-full max-w-full' : 'max-w-[82%] sm:max-w-[34rem]'
)

const messageCardClass = (entry: ConversationEntry): string => {
  if (entry.role === 'user') {
    return 'rounded-2xl border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-slate-800 shadow-sm shadow-slate-100'
  }
  return 'px-1 py-1 text-slate-800'
}

const userMessageMode = (entry: ConversationEntry): 'markdown' | 'plaintext' => (
  isUserMarkdownEntry(entry) ? 'markdown' : 'plaintext'
)

const traceDurationText = (entry: ConversationEntry): string => {
  const startedAt = toMillis(entry.startedAt)
  if (!startedAt) return ''
  const endedAt = toMillis(entry.endedAt)
  if (endedAt > startedAt) return formatDuration(endedAt - startedAt)
  if (entry.status === 'running') return `进行中 · ${formatDuration(nowTick.value - startedAt)}`
  return ''
}

const normalizedTraceType = (entry: ConversationEntry): string => String(entry?.traceType || '').trim()

const traceTypeLabel = (entry: ConversationEntry): string => {
  const traceType = normalizedTraceType(entry)
  if (traceType === 'thinking') return '思考'
  if (traceType === 'tool') return '工具'
  if (traceType === 'agent') return '子 Agent'
  if (traceType === 'progress') return '进度'
  if (traceType === 'summary') return '执行过程'
  return '处理'
}

const traceActorText = (entry: ConversationEntry): string => {
  const values = [
    entry.actorName,
    entry.specialistType,
    entry.toolName,
  ].map(value => String(value || '').trim()).filter(Boolean)
  return values[0] || ''
}

const traceInlineText = (entry: ConversationEntry): string => (
  String(entry.label || '').trim() || '处理中'
)

const traceDetailText = (entry: ConversationEntry): string => {
  const detail = String(entry.detail || '').trim()
  const label = String(entry.label || '').trim()
  if (!detail || detail === label) return ''
  return detail
}

const stringifyTraceValue = (value: any): string => {
  if (value === null || value === undefined || value === '') return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch (_error) {
    return String(value)
  }
}

const traceDetailBlocks = (entry: ConversationEntry): Array<{ label: string, value: string }> => {
  const candidates = [
    ['调用参数', entry.arguments ?? entry.input],
    ['执行结果', entry.result ?? entry.output],
    ['元数据', entry.metadata],
  ] as Array<[string, any]>
  return candidates
    .map(([label, value]) => ({ label, value: stringifyTraceValue(value).trim() }))
    .filter(block => !!block.value)
}

const traceCanExpand = (entry: ConversationEntry): boolean => {
  const traceType = normalizedTraceType(entry)
  if (traceType === 'agent') return true
  if (String(entry.status || '').trim() === 'running') return false
  return !!traceDetailText(entry) || traceDetailBlocks(entry).length > 0 || traceChildren(entry).length > 0
}

const traceDefaultOpen = (entry: ConversationEntry): boolean => String(entry.status || '').trim() === 'running'

const isAgentTrace = (entry: ConversationEntry): boolean => normalizedTraceType(entry) === 'agent'

const traceIcon = (entry: ConversationEntry) => {
  if (String(entry.status || '').trim() === 'failed') return XCircleIcon
  const traceType = normalizedTraceType(entry)
  if (traceType === 'agent' || traceType === 'thinking') return CpuChipIcon
  if (traceType === 'tool' || traceType === 'progress') return WrenchScrewdriverIcon
  return CheckCircleIcon
}

const traceIconClass = (entry: ConversationEntry): string => {
  if (String(entry.status || '').trim() === 'failed') return 'text-rose-500'
  if (String(entry.status || '').trim() === 'completed') return 'text-slate-300'
  if (normalizedTraceType(entry) === 'tool') return 'text-slate-400'
  return 'text-cyan-600'
}

const traceSummaryClass = (entry: ConversationEntry): string => {
  if (String(entry.status || '').trim() === 'failed') return 'text-rose-500 hover:bg-rose-50'
  if (String(entry.status || '').trim() === 'running') return 'text-slate-700 hover:bg-cyan-50/60'
  return 'text-slate-500 hover:bg-slate-50'
}

const traceHistoryText = (entry: ConversationEntry): string => {
  const label = traceInlineText(entry)
  if (String(entry.status || '').trim() === 'failed') return `${traceTypeLabel(entry)}失败：${label}`
  if (String(entry.status || '').trim() === 'running') return label
  return `${traceTypeLabel(entry)} · ${label}`
}

const traceHistoryToneClass = (entry: ConversationEntry): string => {
  if (String(entry.status || '').trim() === 'failed') return 'text-rose-500 hover:bg-rose-50'
  return 'text-slate-400 hover:bg-slate-50'
}

const traceSummaryItems = (entry: ConversationEntry): ConversationEntry[] => (
  Array.isArray(entry.items) ? entry.items : []
)

const traceChildren = (entry: ConversationEntry): ConversationEntry[] => (
  Array.isArray(entry.children) ? entry.children : []
)

const entryCopyText = (entry: ConversationEntry): string => String(entry?.content || '').trim()

const canCopyEntry = (entry: ConversationEntry): boolean => !!entryCopyText(entry)

const downloadAttachmentsForEntry = (entry: ConversationEntry): AgentConversationAttachment[] => {
  const attachments = Array.isArray(props.attachments) ? [...props.attachments] : []
  if (!attachments.length) return []
  if (entry.entryType === 'trace' || entry.entryType === 'trace-summary' || entry.entryType === 'phase') return []
  const anchorEntry = [...props.entries].reverse().find((item) => (
    item?.role === 'assistant'
    && item.entryType !== 'trace'
    && item.entryType !== 'trace-summary'
    && item.entryType !== 'phase'
  ))
  if (!anchorEntry || anchorEntry.id !== entry.id) return []
  return attachments.sort((left, right) => {
    if (Boolean(left.isPrimary) !== Boolean(right.isPrimary)) return left.isPrimary ? -1 : 1
    return String(left.attachmentId || '').localeCompare(String(right.attachmentId || ''))
  })
}

const copyEntryContent = async (entry: ConversationEntry) => {
  const text = entryCopyText(entry)
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    showMessage('success', '已复制消息内容')
  } catch (_error) {
    showMessage('error', '复制失败，请稍后重试。')
  }
}

const syncMessageListState = () => {
  const el = messageListRef.value
  if (!el) return
  const remaining = el.scrollHeight - el.scrollTop - el.clientHeight
  isNearBottom.value = remaining <= 160
  hasScrollableOverflow.value = el.scrollHeight > el.clientHeight + 8
}

const scrollMessagesToBottom = async (behavior: ScrollBehavior = 'auto') => {
  await nextTick()
  const el = messageListRef.value
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior })
  syncMessageListState()
}

const scrollToLatest = () => {
  void scrollMessagesToBottom('smooth')
}

watch(messageListRef, (element, previous) => {
  if (previous) previous.removeEventListener('scroll', syncMessageListState)
  if (element) {
    element.addEventListener('scroll', syncMessageListState, { passive: true })
    nextTick(() => {
      syncMessageListState()
    })
  }
})

watch(
  [
    () => props.sessionId,
    () => latestEntryFingerprint.value,
  ],
  ([sessionId], [previousSessionId]) => {
    if (sessionId !== previousSessionId || isNearBottom.value) {
      void scrollMessagesToBottom('auto')
      return
    }
    nextTick(() => {
      syncMessageListState()
    })
  },
)

onMounted(() => {
  nowTimer = setInterval(() => {
    nowTick.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => {
  if (messageListRef.value) {
    messageListRef.value.removeEventListener('scroll', syncMessageListState)
  }
  if (nowTimer) clearInterval(nowTimer)
})
</script>

<style scoped>
.agent-summary {
  list-style: none;
}

.agent-summary::-webkit-details-marker {
  display: none;
}
</style>
