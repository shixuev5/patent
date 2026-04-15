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

const isProcessMessage = (entry: ConversationEntryLike): boolean => String(entry.kind || '').trim() === 'process'

const processMetadata = (entry: ConversationEntryLike): Record<string, any> => {
  const value = entry?.metadata
  return value && typeof value === 'object' ? value as Record<string, any> : {}
}

const processSummary = (entry: ConversationEntryLike): string => {
  const metadata = processMetadata(entry)
  return String(metadata.summary || entry.content || metadata.label || metadata.toolLabel || '执行过程').trim()
}

const normalizedProcessStatus = (entry: ConversationEntryLike): 'in_progress' | 'completed' | 'failed' => {
  const rawStatus = String(processMetadata(entry).status || '').trim().toLowerCase()
  if (rawStatus === 'completed') return 'completed'
  if (rawStatus === 'failed') return 'failed'
  if (rawStatus === 'running') return 'in_progress'
  return 'in_progress'
}

const processDisplayKind = (entry: ConversationEntryLike): 'group_status' | 'detail' | '' => {
  const kind = String(processMetadata(entry).displayKind || '').trim()
  if (kind === 'group_status' || kind === 'detail') return kind
  return ''
}

const processDedupeKey = (entry: ConversationEntryLike): string => {
  const metadata = processMetadata(entry)
  return String(metadata.dedupeKey || '').trim()
}

const processGroupKey = (entry: ConversationEntryLike): string => String(processMetadata(entry).displayGroupKey || '').trim()

const isRenderableProcessMessage = (entry: ConversationEntryLike): boolean => (
  isProcessMessage(entry)
  && !!processDisplayKind(entry)
  && !!processDedupeKey(entry)
)

const shouldDefaultExpand = (status: ProcessRenderNode['status']): boolean => status === 'in_progress'

export const useAiSearchConversation = ({
  messages,
  phaseMarkers,
  pendingAssistantMessage,
  currentPendingAction,
  resumeActionCard,
  humanDecisionCard,
}: {
  messages: { value: Array<Record<string, any>> }
  phaseMarkers: { value: Array<Record<string, any>> }
  pendingAssistantMessage: { value: Record<string, any> | null }
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

  const conversationRenderEntries = computed<Array<Record<string, any>>>(() => {
    const sourceEntries = [...conversationEntries.value, ...pendingActionEntries.value].sort((left, right) => {
      if (left.sortKey !== right.sortKey) return left.sortKey - right.sortKey
      return left.order - right.order
    })

    const rendered: Array<Record<string, any>> = []
    const groupEntries = new Map<string, Record<string, any>>()
    const groupChildren = new Map<string, Record<string, ProcessRenderNode>>()
    const standaloneProcesses = new Map<string, Record<string, any>>()

    const ensureGroupEntry = (
      groupKey: string,
      entry: Record<string, any>,
      title: string,
      status: ProcessRenderNode['status'],
    ): Record<string, any> => {
      const existingGroup = groupEntries.get(groupKey)
      if (existingGroup) {
        existingGroup.node = {
          ...existingGroup.node,
          title: title || existingGroup.node.title,
          status,
          defaultExpanded: shouldDefaultExpand(status),
        }
        return existingGroup
      }
      const groupEntry = {
        id: `process-group-${groupKey}-${entry.id}`,
        entryType: 'process-render',
        sortKey: entry.sortKey,
        order: entry.order,
        node: {
          id: `process-node-${groupKey}-${entry.id}`,
          title: title || '执行过程',
          status,
          level: 0,
          isGroup: true,
          collapsible: true,
          defaultExpanded: shouldDefaultExpand(status),
          autoCollapseOnTerminal: true,
          displayKind: 'group_status',
          dedupeKey: groupKey,
          children: [],
        } as ProcessRenderNode,
      }
      rendered.push(groupEntry)
      groupEntries.set(groupKey, groupEntry)
      return groupEntry
    }

    for (const entry of sourceEntries) {
      if (isProcessMessage(entry) && !isRenderableProcessMessage(entry)) {
        continue
      }

      if (!isProcessMessage(entry)) {
        rendered.push(entry)
        continue
      }

      const metadata = processMetadata(entry)
      const processStatus = normalizedProcessStatus(entry)
      const displayKind = processDisplayKind(entry)
      const dedupeKey = processDedupeKey(entry)
      const groupKey = processGroupKey(entry)

      if (displayKind === 'group_status') {
        ensureGroupEntry(
          groupKey || dedupeKey,
          entry,
          String(metadata.label || metadata.subagentLabel || entry.content || '执行过程').trim(),
          processStatus,
        )
        continue
      }

      if (groupKey) {
        const groupEntry = ensureGroupEntry(
          groupKey,
          entry,
          String(metadata.subagentLabel || metadata.label || '执行过程').trim(),
          processStatus,
        )
        const existingChildren = groupChildren.get(groupKey) || {}
        const existingChild = existingChildren[dedupeKey]
        if (existingChild) {
          existingChild.title = processSummary(entry)
          existingChild.status = processStatus
          existingChild.defaultExpanded = shouldDefaultExpand(processStatus)
        } else {
          const childNode: ProcessRenderNode = {
            id: `process-child-node-${dedupeKey}-${entry.id}`,
            title: processSummary(entry),
            status: processStatus,
            level: 1,
            displayKind: 'detail',
            dedupeKey,
            defaultExpanded: shouldDefaultExpand(processStatus),
            autoCollapseOnTerminal: true,
          }
          ;(groupEntry.node.children as ProcessRenderNode[]).push(childNode)
          existingChildren[dedupeKey] = childNode
          groupChildren.set(groupKey, existingChildren)
        }
        const childStatuses = (groupEntry.node.children || []).map(child => child.status)
        const nextGroupStatus: ProcessRenderNode['status'] = childStatuses.includes('failed')
          ? 'failed'
          : childStatuses.includes('in_progress')
            ? 'in_progress'
            : groupEntry.node.status
        groupEntry.node = {
          ...groupEntry.node,
          status: nextGroupStatus,
          defaultExpanded: shouldDefaultExpand(nextGroupStatus),
        }
        continue
      }

      const existingStandalone = standaloneProcesses.get(dedupeKey)
      if (existingStandalone) {
        existingStandalone.node = {
          ...existingStandalone.node,
          title: processSummary(entry),
          status: processStatus,
          defaultExpanded: shouldDefaultExpand(processStatus),
        }
      } else {
        const standaloneEntry = {
          id: `process-standalone-${dedupeKey}-${entry.id}`,
          entryType: 'process-render',
          sortKey: entry.sortKey,
          order: entry.order,
          node: {
            id: `process-standalone-node-${dedupeKey}-${entry.id}`,
            title: processSummary(entry),
            status: processStatus,
            level: 0,
            displayKind: 'detail',
            dedupeKey,
            defaultExpanded: shouldDefaultExpand(processStatus),
            autoCollapseOnTerminal: true,
          } as ProcessRenderNode,
        }
        rendered.push(standaloneEntry)
        standaloneProcesses.set(dedupeKey, standaloneEntry)
      }
    }

    return rendered
  })

  return {
    conversationRenderEntries,
  }
}
