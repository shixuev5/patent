import { computed } from 'vue'

export type ProcessRenderNode = {
  id: string
  title: string
  status: 'in_progress' | 'completed' | 'failed'
  level: number
  isGroup?: boolean
  collapsible?: boolean
  defaultExpanded?: boolean
  autoCollapseOnTerminal?: boolean
  displayKind?: 'group_status' | 'detail'
  dedupeKey?: string
  children?: ProcessRenderNode[]
}

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

const shouldDefaultExpand = (status: ProcessRenderNode['status']): boolean => status === 'in_progress'

const processStatus = (event: ConversationEntryLike): ProcessRenderNode['status'] => {
  const rawStatus = String(event.status || '').trim().toLowerCase()
  if (rawStatus === 'completed') return 'completed'
  if (rawStatus === 'failed') return 'failed'
  return 'in_progress'
}

const baseProcessTitle = (event: ConversationEntryLike): string => {
  const kind = String(event.displayKind || '').trim()
  if (kind === 'group_status') {
    return String(event.subagentLabel || event.label || event.summary || event.statusText || '执行过程').trim()
  }
  return String(event.toolLabel || event.summary || event.statusText || event.label || '执行过程').trim()
}

const processSummary = (event: ConversationEntryLike, titleOverride?: string): string => (
  String(titleOverride || baseProcessTitle(event) || '执行过程').trim()
)

const shouldRenderProcessEvent = (event: ConversationEntryLike): boolean => (
  String(event?.toolName || '').trim() !== 'write_stage_log'
)

const toProcessNode = (event: ConversationEntryLike, titleOverride?: string): ProcessRenderNode => {
  const summary = processSummary(event, titleOverride)
  const status = processStatus(event)
  const dedupeKey = String(event.dedupeKey || event.eventId || event.seq || summary).trim()
  return {
    id: `process-${dedupeKey}`,
    title: summary,
    status,
    level: 0,
    isGroup: String(event.displayKind || '').trim() === 'group_status',
    displayKind: String(event.displayKind || '').trim() === 'group_status' ? 'group_status' : 'detail',
    dedupeKey,
    defaultExpanded: shouldDefaultExpand(status),
    autoCollapseOnTerminal: true,
  }
}

export const useAiSearchConversation = ({
  messages,
  phaseMarkers,
  pendingAssistantMessage,
  currentPendingAction,
  resumeActionCard,
  humanDecisionCard,
  processEvents,
}: {
  messages: { value: Array<Record<string, any>> }
  phaseMarkers: { value: Array<Record<string, any>> }
  pendingAssistantMessage: { value: Record<string, any> | null }
  currentPendingAction: { value: Record<string, any> | null }
  resumeActionCard: { value: ConversationActionCard | null }
  humanDecisionCard: { value: ConversationActionCard | null }
  processEvents: { value: Array<Record<string, any>> }
}) => {
  const processRenderEntries = computed<Array<Record<string, any>>>(() => (
    (() => {
      const filtered = (Array.isArray(processEvents.value) ? processEvents.value : []).filter(item => shouldRenderProcessEvent(item))
      const deduped = new Map<string, Record<string, any>>()
      for (const [index, item] of filtered.entries()) {
        if (!shouldRenderProcessEvent(item)) continue
        const dedupeKey = String(item.dedupeKey || item.eventId || item.seq || index).trim() || String(index)
        const sortKey = toMillis(item.createdAt || item.created_at)
        const existing = deduped.get(dedupeKey)
        if (!existing) {
          deduped.set(dedupeKey, {
            id: `process-entry-${dedupeKey}`,
            entryType: 'process-render',
            sortKey,
            order: 500 + index,
            event: item,
          })
          continue
        }
        existing.event = item
      }

      const titleTotals = new Map<string, number>()
      const dedupedEntries = Array.from(deduped.values())
      for (const entry of dedupedEntries) {
        const event = entry.event || {}
        if (String(event.displayKind || '').trim() === 'group_status') continue
        const title = baseProcessTitle(event)
        if (!title) continue
        titleTotals.set(title, (titleTotals.get(title) || 0) + 1)
      }

      const titleSeen = new Map<string, number>()
      for (const entry of dedupedEntries) {
        const event = entry.event || {}
        const baseTitle = baseProcessTitle(event)
        let title = baseTitle
        if (String(event.displayKind || '').trim() !== 'group_status' && baseTitle) {
          const total = titleTotals.get(baseTitle) || 0
          if (total > 1) {
            const nextIndex = (titleSeen.get(baseTitle) || 0) + 1
            titleSeen.set(baseTitle, nextIndex)
            title = `${baseTitle} ${nextIndex}/${total}`
          }
        }
        entry.node = toProcessNode(event, title)
      }
      return dedupedEntries
    })()
  ))

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
    if (pendingAssistantMessage.value) {
      entries.push({
        id: `pending-${pendingAssistantMessage.value.messageId}`,
        entryType: 'pending-assistant',
        role: 'assistant',
        content: pendingAssistantMessage.value.content,
        createdAt: pendingAssistantMessage.value.createdAt,
        sortKey: toMillis(pendingAssistantMessage.value.createdAt),
        order: 2000,
      })
    }
    return entries.sort((left, right) => {
      if (left.sortKey !== right.sortKey) return left.sortKey - right.sortKey
      return left.order - right.order
    })
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
    [...conversationEntries.value, ...processRenderEntries.value, ...pendingActionEntries.value].sort((left, right) => {
      if (left.sortKey !== right.sortKey) return left.sortKey - right.sortKey
      return left.order - right.order
    })
  ))

  return {
    conversationRenderEntries,
  }
}
