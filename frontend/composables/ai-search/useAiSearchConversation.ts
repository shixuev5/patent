import { computed } from 'vue'
import type { AiSearchActivityTrace } from '~/types/aiSearch'
import { aiSearchPhaseLabel } from '~/utils/aiSearch'

type ConversationEntryLike = Record<string, any>

const toMillis = (value?: string | null): number => {
  const ts = Date.parse(String(value || ''))
  return Number.isFinite(ts) ? ts : 0
}

const toFiniteNumber = (value: any): number => {
  const numeric = Number(value || 0)
  return Number.isFinite(numeric) ? numeric : 0
}

const entrySortSeq = (entry: ConversationEntryLike): number => (
  toFiniteNumber(entry.sortSeq || entry._eventStartedSeq || entry._eventOrder || entry._eventSeq || 0)
)

const compareEntries = (left: ConversationEntryLike, right: ConversationEntryLike): number => {
  const leftTime = toFiniteNumber(left.sortKey)
  const rightTime = toFiniteNumber(right.sortKey)
  if (leftTime !== rightTime) return leftTime - rightTime
  const leftSeq = entrySortSeq(left)
  const rightSeq = entrySortSeq(right)
  if (leftSeq !== rightSeq) return leftSeq - rightSeq
  return toFiniteNumber(left.order) - toFiniteNumber(right.order)
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

const buildWaitingTraceLabel = (phase: string, stopSatisfied: boolean = false): string => {
  if (stopSatisfied) return '停止条件已满足，正在整理结论。'
  const normalized = String(phase || '').trim()
  const phaseLabel = aiSearchPhaseLabel(normalized)
  if (!normalized) return '正在同步会话进度，请稍候。'
  if (normalized === 'running') return '正在同步检索进度，请稍候。'
  return `正在${phaseLabel}，请稍候。`
}

const isRunningPhase = (phase: string): boolean => String(phase || '').trim() === 'running'

const truthyValue = (value: unknown): boolean => (
  value === true || String(value || '').trim().toLowerCase() === 'true'
)

const traceIndicatesStopSatisfied = (trace: ConversationEntryLike): boolean => {
  const output = trace?.output && typeof trace.output === 'object' ? trace.output as Record<string, any> : null
  const result = trace?.result && typeof trace.result === 'object' ? trace.result as Record<string, any> : null
  const payload = output || result
  if (!payload) return false
  if (truthyValue(payload.blocked)) return true
  const stop = payload.stop && typeof payload.stop === 'object' ? payload.stop as Record<string, any> : null
  return !!stop && truthyValue(stop.should_stop)
}

export const useAiSearchConversation = ({
  messages,
  activityTraces,
  streaming,
  phase,
}: {
  messages: { value: Array<Record<string, any>> }
  activityTraces: { value: AiSearchActivityTrace[] }
  streaming: { value: boolean }
  phase: { value: string }
}) => {
  const conversationEntries = computed<Array<Record<string, any>>>(() => {
    const entries: Array<Record<string, any>> = []
    messages.value.forEach((message, index) => {
      if (isAnalysisSeedContextMessage(message)) return
      const eventAt = String(message._eventAt || '').trim()
      entries.push({
        id: message.message_id || `message-${index}`,
        entryType: 'message',
        sortKey: toMillis(eventAt || message.created_at),
        sortSeq: entrySortSeq(message),
        order: index,
        ...message,
      })
    })
    const traceEntries = activityTraces.value.map((trace, index) => ({
      id: trace.traceId || `trace-${index}`,
      entryType: 'trace',
      sortKey: toMillis(trace.startedAt || trace.endedAt),
      sortSeq: toFiniteNumber(trace._eventOrder || trace._eventStartedSeq || trace._eventSeq),
      order: 500 + index,
      ...trace,
    }))
    const traceById = new Map<string, Record<string, any>>()
    traceEntries.forEach((trace) => {
      const traceId = String(trace.traceId || trace.id || '').trim()
      if (traceId) traceById.set(traceId, trace)
    })
    const childrenByParent = new Map<string, Array<Record<string, any>>>()
    traceEntries.forEach((trace) => {
      const parentTraceId = String(trace.parentTraceId || '').trim()
      if (!parentTraceId || !traceById.has(parentTraceId)) return
      const children = childrenByParent.get(parentTraceId) || []
      children.push(trace)
      childrenByParent.set(parentTraceId, children)
    })
    const attachChildren = (trace: Record<string, any>): Record<string, any> => {
      const traceId = String(trace.traceId || trace.id || '').trim()
      const children = (childrenByParent.get(traceId) || [])
        .sort(compareEntries)
        .map(child => attachChildren(child))
      return children.length ? { ...trace, children } : trace
    }
    traceEntries
      .filter((trace) => {
        const parentTraceId = String(trace.parentTraceId || '').trim()
        return !parentTraceId || !traceById.has(parentTraceId)
      })
      .map(trace => attachChildren(trace))
      .forEach(trace => entries.push(trace))
    const sortedEntries = entries.sort(compareEntries)
    const hasRunningTrace = sortedEntries.some((entry) => (
      isTraceEntry(entry)
      && String(entry.status || '').trim() === 'running'
    ))
    const shouldShowSyntheticWaitingTrace = (streaming.value || isRunningPhase(phase.value)) && !hasRunningTrace
    if (shouldShowSyntheticWaitingTrace && !hasRunningTrace) {
      const maxSortKey = sortedEntries.reduce((max, entry) => Math.max(max, Number(entry.sortKey || 0)), 0)
      const stopSatisfied = sortedEntries.some((entry) => (
        isTraceEntry(entry)
        && String(entry.status || '').trim() !== 'running'
        && traceIndicatesStopSatisfied(entry)
      ))
      sortedEntries.push({
        id: `trace-waiting-${String(phase.value || '').trim() || 'default'}`,
        entryType: 'trace',
        traceType: 'progress',
        status: 'running',
        label: buildWaitingTraceLabel(phase.value, stopSatisfied),
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
    [...conversationEntries.value].sort(compareEntries)
  ))

  return {
    conversationRenderEntries,
  }
}
