import { computed } from 'vue'
import type { AiSearchActivityTrace } from '~/types/aiSearch'
import { aiSearchPhaseLabel } from '~/utils/aiSearch'

type ConversationEntryLike = Record<string, any>

const toMillis = (value?: string | null): number => {
  const ts = Date.parse(String(value || ''))
  return Number.isFinite(ts) ? ts : 0
}

const entryMetadata = (entry: ConversationEntryLike): Record<string, any> => {
  const value = entry?.metadata
  return value && typeof value === 'object' ? value as Record<string, any> : {}
}

const isAnalysisSeedContextMessage = (entry: ConversationEntryLike): boolean => (
  String(entry?.role || '').trim() === 'user'
  && String(entryMetadata(entry).message_variant || '').trim() === 'analysis_seed_context'
)

const isTraceEntry = (entry: ConversationEntryLike): boolean => (
  String(entry?.entryType || '').trim() === 'trace'
)

const buildWaitingTraceLabel = (phase: string): string => {
  const normalized = String(phase || '').trim()
  const phaseLabel = aiSearchPhaseLabel(normalized)
  if (!normalized) return '正在继续处理，请稍候。'
  return `正在${phaseLabel}，请稍候。`
}

const isRunningPhase = (phase: string): boolean => String(phase || '').trim() === 'running'

export const useAiSearchConversation = ({
  messages,
  activityTraces,
  phaseMarkers,
  streaming,
  phase,
}: {
  messages: { value: Array<Record<string, any>> }
  activityTraces: { value: AiSearchActivityTrace[] }
  phaseMarkers: { value: Array<Record<string, any>> }
  streaming: { value: boolean }
  phase: { value: string }
}) => {
  const conversationEntries = computed<Array<Record<string, any>>>(() => {
    const entries: Array<Record<string, any>> = []
    messages.value.forEach((message, index) => {
      if (isAnalysisSeedContextMessage(message)) return
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
    const hasRunningTrace = sortedEntries.some((entry) => (
      isTraceEntry(entry)
      && String(entry.status || '').trim() === 'running'
    ))
    const shouldShowSyntheticWaitingTrace = (streaming.value || isRunningPhase(phase.value)) && !hasRunningTrace
    if (shouldShowSyntheticWaitingTrace && !hasRunningTrace) {
      const maxSortKey = sortedEntries.reduce((max, entry) => Math.max(max, Number(entry.sortKey || 0)), 0)
      sortedEntries.push({
        id: `trace-waiting-${String(phase.value || '').trim() || 'default'}`,
        entryType: 'trace',
        traceType: 'progress',
        status: 'running',
        label: buildWaitingTraceLabel(phase.value),
        actorName: 'ai-search-agent',
        startedAt: new Date().toISOString(),
        sortKey: maxSortKey + 1,
        order: 2999,
        synthetic: true,
      })
    }
    return sortedEntries
  })

  const conversationRenderEntries = computed<Array<Record<string, any>>>(() => (
    [...conversationEntries.value].sort((left, right) => {
      if (left.sortKey !== right.sortKey) return left.sortKey - right.sortKey
      return left.order - right.order
    })
  ))

  return {
    conversationRenderEntries,
  }
}
