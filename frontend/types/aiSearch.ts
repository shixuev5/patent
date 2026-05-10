export type AiSearchActivityState = 'running' | 'paused' | 'none'

export interface AiSearchSessionSummary {
  sessionId: string
  taskId: string
  title: string
  status: string
  phase: string
  activityState: AiSearchActivityState
  sourceTaskId?: string | null
  sourceType?: string | null
  pinned: boolean
  activePlanVersion?: number | null
  selectedDocumentCount: number
  createdAt?: string | null
  updatedAt?: string | null
}

export interface AiSearchArtifactAttachment {
  attachmentId: string
  kind: string
  name: string
  downloadUrl: string
  mediaType: string
  sizeBytes: number
  createdAt: string
  isPrimary: boolean
}

export interface AiSearchArtifactsPayload {
  attachments: AiSearchArtifactAttachment[]
}

export interface AiSearchRun {
  runId?: string | null
  phase: string
  status: string
  planVersion?: number | null
  activeRetrievalTodoId?: string | null
  activeBatchId?: string | null
  selectedDocumentCount: number
}

export interface AiSearchPendingAction {
  actionId?: string | null
  runId?: string | null
  actionType: string
  status?: string | null
  createdAt?: string | null
  updatedAt?: string | null
  [key: string]: any
}

export interface AiSearchQueuedExecutionMessage {
  queueMessageId: string
  runId: string
  content: string
  ordinal: number
  createdAt: string
}

export interface AiSearchRetrievalState {
  todos: Array<Record<string, any>>
  activeTodo: Record<string, any> | null
  documents: {
    candidates: Array<Record<string, any>>
    selected: Array<Record<string, any>>
  }
}

export interface AiSearchBatchSummary {
  batchId?: string | null
  runId?: string | null
  batchType?: string | null
  status?: string | null
  workspaceDir?: string | null
  inputHash?: string | null
  loadedAt?: string | null
  committedAt?: string | null
  [key: string]: any
}

export interface AiSearchAnalysisState {
  activeBatch: AiSearchBatchSummary | null
  latestCloseReadResult: Record<string, any> | null
  latestFeatureCompareResult: Record<string, any> | null
}

export interface AiSearchSnapshot {
  session: AiSearchSessionSummary
  run: AiSearchRun
  conversation: {
    messages: Array<Record<string, any>>
    pendingAction: AiSearchPendingAction | null
  }
  stream?: {
    lastEventSeq?: number
  }
  executionMessageQueue: {
    items: AiSearchQueuedExecutionMessage[]
  }
  plan: {
    currentPlan: Record<string, any> | null
  }
  retrieval: AiSearchRetrievalState
  analysis: AiSearchAnalysisState
  artifacts: AiSearchArtifactsPayload
  analysisSeed?: Record<string, any> | null
}

export interface AiSearchSessionListResponse {
  items: AiSearchSessionSummary[]
  total: number
}

export interface AiSearchCreateSessionResponse {
  sessionId: string
  taskId: string
  threadId: string
  reused?: boolean
  sourceTaskId?: string | null
}

export interface AiSearchExecutionQueueResponse {
  items: AiSearchQueuedExecutionMessage[]
}

export interface AiSearchStreamEvent {
  type: string
  sessionId: string
  taskId: string
  phase: string
  seq?: number
  runId?: string | null
  entityId?: string | null
  timestamp?: string | null
  payload: any
}

export interface AiSearchActivityTrace {
  traceId: string
  traceType: 'thinking' | 'tool' | 'agent' | string
  status: 'running' | 'completed' | 'failed' | string
  label: string
  actorName?: string | null
  toolName?: string | null
  specialistType?: string | null
  detail?: string | null
  startedAt?: string | null
  endedAt?: string | null
}

export interface AiSearchActiveRun {
  runKey: string
  sessionId: string
  startedAt: string
  phase: string
}

export interface AiSearchPhaseMarker {
  id: string
  runKey: string
  phase: string
  createdAt: string
  endedAt?: string | null
}
