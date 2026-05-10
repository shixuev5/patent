<template>
  <div class="relative min-h-0 flex-1">
    <div ref="messageListRef" class="flex min-h-0 h-full flex-col overflow-y-auto px-4 py-4">
      <div v-if="!entries.length" class="flex min-h-full flex-1 items-center justify-center px-4 py-8 text-center text-sm text-slate-500">
        描述检索目标、技术方案、核心效果或约束条件。
      </div>

      <div v-else class="space-y-2.5">
        <template v-for="entry in entries" :key="entry.id">
          <article v-if="entry.entryType === 'phase'" class="flex items-center gap-3 py-1">
            <span class="h-px flex-1 bg-slate-200/80" />
            <p class="shrink-0 text-[11px] font-medium tracking-[0.14em] text-slate-400">
              {{ phaseLabel(entry.phase) }}<span v-if="phaseDurationText(entry)"> · {{ phaseDurationText(entry) }}</span>
            </p>
            <span class="h-px flex-1 bg-slate-200/80" />
          </article>

          <article v-if="isPendingActionEntry(entry)" class="flex justify-start">
            <div class="w-full max-w-full">
              <section
                v-if="entry.actionType === 'resume'"
                class="rounded-2xl border px-4 py-4"
                :class="entry.card?.severity === 'amber' ? 'border-amber-200 bg-amber-50' : 'border-slate-200 bg-slate-50'"
              >
                <div class="space-y-3">
                  <div class="min-w-0">
                    <p class="text-sm font-semibold" :class="entry.card?.severity === 'amber' ? 'text-amber-900' : 'text-slate-900'">
                      {{ entry.card?.title }}
                    </p>
                    <p class="mt-1 text-xs leading-6" :class="entry.card?.severity === 'amber' ? 'text-amber-800' : 'text-slate-700'">
                      {{ entry.card?.body }}
                    </p>
                    <p v-if="resumeLastError" class="mt-2 rounded-xl border border-amber-200 bg-white/70 px-3 py-2 text-xs leading-6 text-amber-900">
                      上次错误：{{ resumeLastError }}
                    </p>
                    <p v-if="resumeAttemptCount > 0" class="mt-2 text-[11px] text-amber-700">
                      已尝试 {{ resumeAttemptCount }} 次
                    </p>
                  </div>
                  <div class="flex justify-end">
                    <button
                      type="button"
                      class="inline-flex items-center justify-center rounded-lg bg-amber-600 px-3 py-1.5 text-[12px] font-semibold text-white transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:bg-slate-300"
                      :disabled="streaming"
                      @click="$emit('resume-execution')"
                    >
                      恢复执行
                    </button>
                  </div>
                </div>
              </section>

              <AiSearchHumanDecisionCard
                v-else-if="entry.actionType === 'human_decision'"
                :action="humanDecisionAction"
                :selected-docs="selectedReviewDocuments"
                :candidate-docs="reviewCandidateDocuments"
                :disabled="streaming"
                @request-review="$emit('request-document-review', $event)"
                @remove-selected="$emit('remove-selected-document', $event)"
                @continue-search="$emit('continue-search')"
                @complete-current-results="$emit('complete-current-results')"
              />
            </div>
          </article>

          <article
            v-else
            class="group/message flex"
            :class="entry.role === 'user' ? 'justify-end' : 'justify-start'"
          >
            <div :class="messageWrapperClass(entry)">
              <div
                class="text-[13px] leading-5"
                :class="messageCardClass(entry)"
              >
                <template v-if="entry.role === 'assistant' && isPlanMessage(entry)">
                  <div v-if="isLatestPlanMessage(entry)" class="space-y-3">
                    <p class="text-[13px] font-semibold text-slate-900">
                      {{ planTitle(entry) }}
                    </p>
                    <AiSearchExpandableContent :content="planBody(entry)" mode="markdown" theme="slate" />
                    <AiSearchPlanConfirmationCard
                      v-if="isPendingPlanEntry(entry)"
                      :confirm-disabled="streaming || !hasPendingPlanConfirmation"
                      :label="planConfirmationLabel"
                      @confirm="$emit('confirm-plan')"
                    />
                  </div>
                  <div v-else class="space-y-3">
                    <p class="text-[13px] font-semibold text-slate-900">
                      {{ historicalPlanTitle(entry) }}
                    </p>
                    <AiSearchExpandableContent :content="planBody(entry)" mode="markdown" theme="slate" />
                  </div>
                </template>

                <template v-else-if="entry.role === 'assistant' && isQuestionMessage(entry)">
                  <AiSearchQuestionCard
                    :prompt="entry.content"
                    :pending="isPendingQuestionEntry(entry)"
                  />
                </template>

                <template v-else-if="entry.role === 'assistant'">
                  <AiSearchExpandableContent :content="entry.content" mode="markdown" theme="slate" />
                </template>

                <AiSearchExpandableContent
                  v-else
                  :content="entry.content"
                  :mode="userMessageMode(entry)"
                  :theme="userMessageTheme(entry)"
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
                <AiSearchDownloadAttachment
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
import { ArrowDownIcon, ClipboardDocumentIcon } from '@heroicons/vue/24/outline'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AiSearchDownloadAttachment from '~/components/ai-search/AiSearchDownloadAttachment.vue'
import AiSearchExpandableContent from '~/components/ai-search/AiSearchExpandableContent.vue'
import AiSearchHumanDecisionCard from '~/components/ai-search/AiSearchHumanDecisionCard.vue'
import AiSearchPlanConfirmationCard from '~/components/ai-search/AiSearchPlanConfirmationCard.vue'
import AiSearchQuestionCard from '~/components/ai-search/AiSearchQuestionCard.vue'
import type { AiSearchArtifactAttachment } from '~/types/aiSearch'
import { aiSearchPhaseLabel } from '~/utils/aiSearch'

const props = withDefaults(defineProps<{
  sessionId?: string
  entries: Array<Record<string, any>>
  structuredPlanExecutionSpec?: Record<string, any> | null
  activePlanVersion?: number
  hasPendingPlanConfirmation?: boolean
  planConfirmationLabel?: string
  pendingQuestion?: Record<string, any> | null
  humanDecisionAction?: Record<string, any> | null
  selectedReviewDocuments?: Array<Record<string, any>>
  reviewCandidateDocuments?: Array<Record<string, any>>
  phase?: string
  workspaceTitle?: string
  attachments?: AiSearchArtifactAttachment[]
  streaming?: boolean
  resumeLastError?: string
  resumeAttemptCount?: number
}>(), {
  sessionId: '',
  structuredPlanExecutionSpec: null,
  activePlanVersion: 0,
  hasPendingPlanConfirmation: false,
  planConfirmationLabel: '实施此计划',
  pendingQuestion: null,
  humanDecisionAction: null,
  selectedReviewDocuments: () => [],
  reviewCandidateDocuments: () => [],
  phase: '',
  workspaceTitle: '',
  attachments: () => [],
  streaming: false,
  resumeLastError: '',
  resumeAttemptCount: 0,
})

defineEmits<{
  'confirm-plan': []
  'resume-execution': []
  'request-document-review': [documentId: string]
  'remove-selected-document': [documentId: string]
  'continue-search': []
  'complete-current-results': []
  'download-result': [attachment: AiSearchArtifactAttachment]
}>()

const { showMessage } = useGlobalMessage()
const messageListRef = ref<HTMLElement | null>(null)
const isNearBottom = ref(true)
const hasScrollableOverflow = ref(false)
const nowTick = ref(Date.now())
let nowTimer: ReturnType<typeof setInterval> | null = null

const showScrollToBottom = computed(() => hasScrollableOverflow.value && !isNearBottom.value)

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

const phaseLabel = (phase: string): string => aiSearchPhaseLabel(phase)

const phaseDurationText = (entry: Record<string, any>): string => {
  const startedAt = toMillis(entry.createdAt)
  if (!startedAt) return ''
  const endedAt = toMillis(entry.endedAt)
  if (endedAt > startedAt) return formatDuration(endedAt - startedAt)
  const isLatestPhaseEntry = props.entries.filter(item => item.entryType === 'phase').at(-1)?.id === entry.id
  if (!isLatestPhaseEntry) return ''
  return `进行中 · ${formatDuration(nowTick.value - startedAt)}`
}

const isPendingActionEntry = (entry: Record<string, any>): boolean => String(entry.entryType || '').trim() === 'pending-action'
const isPlanMessage = (entry: Record<string, any>): boolean => String(entry.kind || '').trim() === 'plan_confirmation'
const isQuestionMessage = (entry: Record<string, any>): boolean => String(entry.kind || '').trim() === 'question'
const isNarrationLikeEntry = (entry: Record<string, any>): boolean => (
  entry.role === 'assistant' && !isPlanMessage(entry) && !isQuestionMessage(entry)
)
const planVersionOf = (entry: Record<string, any>): number => {
  const value = Number(entry.plan_version || entry.planVersion || entry.metadata?.plan_version || 0)
  return Number.isFinite(value) ? value : 0
}

const latestPlanMessageId = computed(() => {
  const latest = props.entries.filter(isPlanMessage).at(-1)
  return String(latest?.id || latest?.message_id || latest?.messageId || '').trim()
})

const stripLeadingHeading = (content: string): string => {
  return content.replace(/^\s{0,3}#{1,6}\s+.+?\n+/, '').trim()
}

const planTitle = (entry: Record<string, any>): string => {
  const version = planVersionOf(entry) || props.activePlanVersion
  return version > 0 ? `检索计划 v${version}` : '检索计划'
}

const historicalPlanTitle = (entry: Record<string, any>): string => {
  const version = planVersionOf(entry)
  return version > 0 ? `历史计划 v${version}` : '历史计划'
}

const planBody = (entry: Record<string, any>): string => {
  const content = String(entry?.content || '').trim()
  const normalized = stripLeadingHeading(content)
  return normalized || content
}

const isLatestPlanMessage = (entry: Record<string, any>): boolean => {
  if (!isPlanMessage(entry)) return false
  const version = planVersionOf(entry)
  if (version > 0 && props.activePlanVersion > 0) {
    return version === props.activePlanVersion
  }
  const entryId = String(entry?.id || entry?.message_id || entry?.messageId || '').trim()
  return !!entryId && entryId === latestPlanMessageId.value
}

const isPendingPlanEntry = (entry: Record<string, any>): boolean => {
  if (!props.hasPendingPlanConfirmation || !isPlanMessage(entry)) return false
  const entryId = String(entry?.id || entry?.message_id || entry?.messageId || '').trim()
  return !!entryId && entryId === latestPlanMessageId.value
}

const isPendingQuestionEntry = (entry: Record<string, any>): boolean => {
  if (!isQuestionMessage(entry) || !props.pendingQuestion) return false
  const entryQuestionId = String(entry.question_id || entry.questionId || '').trim()
  const pendingQuestionId = String(props.pendingQuestion.question_id || props.pendingQuestion.questionId || '').trim()
  return !!entryQuestionId && entryQuestionId === pendingQuestionId
}

const entryMetadata = (entry: Record<string, any>): Record<string, any> => {
  const value = entry?.metadata
  return value && typeof value === 'object' ? value as Record<string, any> : {}
}

const isUserMarkdownEntry = (entry: Record<string, any>): boolean => {
  if (entry.role !== 'user') return false
  const metadata = entryMetadata(entry)
  return (
    String(metadata.render_mode || '').trim() === 'markdown'
    || String(metadata.message_variant || '').trim() === 'analysis_seed_context'
  )
}

const messageWrapperClass = (entry: Record<string, any>): string => (
  isNarrationLikeEntry(entry) ? 'w-full max-w-full' : 'max-w-[90%]'
)

const messageCardClass = (entry: Record<string, any>): string => {
  if (entry.role === 'user') {
    return 'rounded-2xl bg-cyan-700 px-3.5 py-2.5 text-white shadow-sm shadow-cyan-100'
  }
  if (isNarrationLikeEntry(entry)) {
    return 'px-1 py-1 text-slate-700'
  }
  if (isQuestionMessage(entry)) return ''
  return 'rounded-2xl border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-slate-700 shadow-sm'
}

const userMessageMode = (entry: Record<string, any>): 'markdown' | 'plaintext' => (
  isUserMarkdownEntry(entry) ? 'markdown' : 'plaintext'
)

const userMessageTheme = (_entry: Record<string, any>): 'cyan' => 'cyan'

const entryCopyText = (entry: Record<string, any>): string => String(entry?.content || '').trim()

const canCopyEntry = (entry: Record<string, any>): boolean => !!entryCopyText(entry)

const downloadAttachmentsForEntry = (entry: Record<string, any>): AiSearchArtifactAttachment[] => {
  if (String(props.phase || '').trim() !== 'completed') return []
  const attachments = Array.isArray(props.attachments) ? [...props.attachments] : []
  if (!attachments.length) return []
  if (entry.role !== 'assistant' || isPlanMessage(entry) || isQuestionMessage(entry)) return []
  const assistantEntries = props.entries.filter(item => item.role === 'assistant')
  if (assistantEntries.at(-1)?.id !== entry.id) return []
  return attachments.sort((left, right) => {
    if (Boolean(left.isPrimary) !== Boolean(right.isPrimary)) return left.isPrimary ? -1 : 1
    return String(left.attachmentId || '').localeCompare(String(right.attachmentId || ''))
  })
}

const copyEntryContent = async (entry: Record<string, any>) => {
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
    () => props.entries.length,
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
