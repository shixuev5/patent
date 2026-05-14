import { computed } from 'vue'
import type { AiSearchActivityTrace } from '~/types/aiSearch'

export type ConversationActionCard = {
  actionType: 'resume' | 'human_decision'
  title: string
  body: string
  severity: 'amber' | 'slate'
}

type ConversationEntryLike = Record<string, any>

const toMillis = (value?: string | null): number => {
  const ts = Date.parse(String(value || ''))
  return Number.isFinite(ts) ? ts : 0
}

const isTraceEntry = (entry: ConversationEntryLike): boolean => (
  String(entry?.entryType || '').trim() === 'trace'
)

const isCompletedTraceEntry = (entry: ConversationEntryLike): boolean => (
  isTraceEntry(entry) && String(entry?.status || '').trim() === 'completed'
)

const isLowSignalCompletedTrace = (entry: ConversationEntryLike): boolean => (
  isCompletedTraceEntry(entry) && String(entry?.traceType || '').trim() === 'thinking'
)

const buildTraceSummaryLabel = (items: ConversationEntryLike[]): string => {
  const labels = items
    .map(item => String(item?.label || '').trim())
    .filter(Boolean)
  const preview = labels.slice(0, 2).join('、')
  const remaining = labels.length - Math.min(labels.length, 2)
  if (!preview) return `已完成 ${items.length} 个步骤`
  if (remaining > 0) return `已完成 ${items.length} 个步骤：${preview} 等`
  return `已完成 ${items.length} 个步骤：${preview}`
}

const mergeConversationEntries = (entries: ConversationEntryLike[]): ConversationEntryLike[] => {
  const merged: ConversationEntryLike[] = []
  let completedTraceBuffer: ConversationEntryLike[] = []

  const flushCompletedTraceBuffer = () => {
    if (!completedTraceBuffer.length) return
    if (completedTraceBuffer.length === 1) {
      merged.push(completedTraceBuffer[0])
      completedTraceBuffer = []
      return
    }
    const first = completedTraceBuffer[0]
    const last = completedTraceBuffer[completedTraceBuffer.length - 1]
    merged.push({
      id: `trace-summary-${String(first.id || '').trim()}-${String(last.id || '').trim()}`,
      entryType: 'trace-summary',
      traceType: 'summary',
      status: 'completed',
      label: buildTraceSummaryLabel(completedTraceBuffer),
      aggregateCount: completedTraceBuffer.length,
      items: completedTraceBuffer,
      sortKey: first.sortKey,
      order: first.order,
      startedAt: first.startedAt || null,
      endedAt: last.endedAt || last.startedAt || null,
    })
    completedTraceBuffer = []
  }

  entries.forEach((entry) => {
    if (isLowSignalCompletedTrace(entry)) return
    if (isCompletedTraceEntry(entry)) {
      completedTraceBuffer.push(entry)
      return
    }
    flushCompletedTraceBuffer()
    merged.push(entry)
  })

  flushCompletedTraceBuffer()
  return merged
}

export const useAiSearchConversation = ({
  messages,
  activityTraces,
  phaseMarkers,
  currentPendingAction,
  resumeActionCard,
  humanDecisionCard,
}: {
  messages: { value: Array<Record<string, any>> }
  activityTraces: { value: AiSearchActivityTrace[] }
  phaseMarkers: { value: Array<Record<string, any>> }
  currentPendingAction: { value: Record<string, any> | null }
  resumeActionCard: { value: ConversationActionCard | null }
  humanDecisionCard: { value: ConversationActionCard | null }
}) => {
  const conversationEntries = computed<Array<Record<string, any>>>(() => {
    const entries: Array<Record<string, any>> = []
    messages.value.forEach((message, index) => {
      entries.push({
        id: message.message_id || `message-${index}`,
        entryType: 'message',
        sortKey: toMillis(message.created_at),
        order: index,
        ...message,
      })
    })
    activityTraces.value.forEach((trace, index) => {
      entries.push({
        id: trace.traceId || `trace-${index}`,
        entryType: 'trace',
        sortKey: toMillis(trace.startedAt || trace.endedAt),
        order: 500 + index,
        ...trace,
      })
    })
    phaseMarkers.value.forEach((marker, index) => {
      entries.push({
        id: marker.id,
        entryType: 'phase',
        phase: marker.phase,
        createdAt: marker.createdAt,
        endedAt: marker.endedAt || null,
        sortKey: toMillis(marker.createdAt),
        order: 1000 + index,
      })
    })
    const sortedEntries = entries.sort((left, right) => {
      if (left.sortKey !== right.sortKey) return left.sortKey - right.sortKey
      return left.order - right.order
    })
    return mergeConversationEntries(sortedEntries)
  })

  const pendingActionEntries = computed<Array<Record<string, any>>>(() => {
    const entries: Array<Record<string, any>> = []
    const baseSortKey = conversationEntries.value.reduce((max, entry) => Math.max(max, Number(entry.sortKey || 0)), 0) + 1
    if (resumeActionCard.value) {
      entries.push({
        id: 'pending-action-resume',
        entryType: 'pending-action',
        sortKey: Math.max(baseSortKey, toMillis(currentPendingAction.value?.createdAt)),
        order: 3000,
        actionType: 'resume',
        card: resumeActionCard.value,
      })
    }
    if (humanDecisionCard.value) {
      entries.push({
        id: 'pending-action-human-decision',
        entryType: 'pending-action',
        sortKey: Math.max(baseSortKey + entries.length, toMillis(currentPendingAction.value?.createdAt)),
        order: 3001,
        actionType: 'human_decision',
        card: humanDecisionCard.value,
      })
    }
    return entries
  })

  const conversationRenderEntries = computed<Array<Record<string, any>>>(() => (
    [...conversationEntries.value, ...pendingActionEntries.value].sort((left, right) => {
      if (left.sortKey !== right.sortKey) return left.sortKey - right.sortKey
      return left.order - right.order
    })
  ))

  return {
    conversationRenderEntries,
  }
}
