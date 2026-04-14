import { computed } from 'vue'

export type ProcessRenderNode = {
  id: string
  title: string
  status: 'in_progress' | 'completed' | 'failed'
  level: number
  isGroup?: boolean
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
  return 'in_progress'
}

const isTerminalProcessStatus = (status?: string): boolean => ['completed', 'failed'].includes(String(status || '').trim())

const processEntryKey = (entry: ConversationEntryLike): string => {
  const metadata = processMetadata(entry)
  const eventId = String(metadata.eventId || '').trim()
  if (eventId) return eventId.replace(/:(running|completed|failed|started)$/u, '')
  const processType = String(metadata.processType || '').trim() || 'process'
  const toolName = String(metadata.toolName || '').trim()
  const summary = processSummary(entry)
  return [processType, toolName, summary].filter(Boolean).join(':')
}

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
    const activeGroups = new Map<string, Record<string, any>>()
    const activeGroupChildren = new Map<string, Record<string, ProcessRenderNode>>()
    const activeStandaloneProcesses = new Map<string, Record<string, any>>()

    for (const entry of sourceEntries) {
      if (!isProcessMessage(entry)) {
        rendered.push(entry)
        continue
      }

      const metadata = processMetadata(entry)
      const processType = String(metadata.processType || '').trim()
      const processStatus = normalizedProcessStatus(entry)
      const subagentName = String(metadata.subagentName || metadata.name || '').trim()
      const subagentLabel = String(metadata.subagentLabel || metadata.label || subagentName || '').trim()

      if (processType === 'subagent' && subagentName) {
        const existingGroup = activeGroups.get(subagentName)
        if (existingGroup) {
          existingGroup.node = {
            ...existingGroup.node,
            title: subagentLabel || existingGroup.node.title,
            status: processStatus,
          }
        } else {
          const groupEntry = {
            id: `process-group-${subagentName}-${entry.id}`,
            entryType: 'process-render',
            sortKey: entry.sortKey,
            order: entry.order,
            node: {
              id: `process-node-${subagentName}-${entry.id}`,
              title: subagentLabel || processSummary(entry),
              status: processStatus,
              level: 0,
              isGroup: true,
              children: [],
            } as ProcessRenderNode,
          }
          rendered.push(groupEntry)
          activeGroups.set(subagentName, groupEntry)
        }
        if (isTerminalProcessStatus(processStatus)) {
          activeGroups.delete(subagentName)
          activeGroupChildren.delete(subagentName)
        }
        continue
      }

      if (subagentName) {
        let groupEntry = activeGroups.get(subagentName)
        if (!groupEntry) {
          groupEntry = {
            id: `process-group-${subagentName}-${entry.id}`,
            entryType: 'process-render',
            sortKey: entry.sortKey,
            order: entry.order,
            node: {
              id: `process-node-${subagentName}-${entry.id}`,
              title: subagentLabel || subagentName || '执行过程',
              status: processStatus,
              level: 0,
              isGroup: true,
              children: [],
            } as ProcessRenderNode,
          }
          rendered.push(groupEntry)
          activeGroups.set(subagentName, groupEntry)
        }

        const groupChildren = activeGroupChildren.get(subagentName) || {}
        const childKey = processEntryKey(entry)
        const existingChild = groupChildren[childKey]
        if (existingChild) {
          existingChild.title = processSummary(entry)
          existingChild.status = processStatus
        } else {
          const childNode: ProcessRenderNode = {
            id: `process-child-node-${childKey}-${entry.id}`,
            title: processSummary(entry),
            status: processStatus,
            level: 1,
          }
          ;(groupEntry.node.children as ProcessRenderNode[]).push(childNode)
          groupChildren[childKey] = childNode
          activeGroupChildren.set(subagentName, groupChildren)
        }

        if (processStatus === 'failed') {
          groupEntry.node = { ...groupEntry.node, status: 'failed' }
        } else if (!isTerminalProcessStatus(groupEntry.node.status)) {
          groupEntry.node = { ...groupEntry.node, status: 'in_progress' }
        }

        if (isTerminalProcessStatus(processStatus)) {
          delete groupChildren[childKey]
        }
        continue
      }

      const standaloneKey = processEntryKey(entry)
      const existingStandalone = activeStandaloneProcesses.get(standaloneKey)
      if (existingStandalone) {
        existingStandalone.node = {
          ...existingStandalone.node,
          title: processSummary(entry),
          status: processStatus,
        }
      } else {
        const standaloneEntry = {
          id: `process-standalone-${standaloneKey}-${entry.id}`,
          entryType: 'process-render',
          sortKey: entry.sortKey,
          order: entry.order,
          node: {
            id: `process-standalone-node-${standaloneKey}-${entry.id}`,
            title: processSummary(entry),
            status: processStatus,
            level: 0,
          } as ProcessRenderNode,
        }
        rendered.push(standaloneEntry)
        activeStandaloneProcesses.set(standaloneKey, standaloneEntry)
      }
      if (isTerminalProcessStatus(processStatus)) {
        activeStandaloneProcesses.delete(standaloneKey)
      }
    }

    return rendered
  })

  return {
    conversationRenderEntries,
  }
}
