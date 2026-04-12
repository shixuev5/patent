export interface AiSearchSessionSummary {
  sessionId: string
  taskId: string
  title: string
  status: string
  phase: string
  sourceTaskId?: string | null
  sourceType?: string | null
  pinned: boolean
  activePlanVersion?: number | null
  selectedDocumentCount: number
  createdAt?: string | null
  updatedAt?: string | null
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
  executionMessageQueue: {
    items: AiSearchQueuedExecutionMessage[]
  }
  plan: {
    currentPlan: Record<string, any> | null
  }
  retrieval: AiSearchRetrievalState
  analysis: AiSearchAnalysisState
  artifacts: {
    downloadUrl?: string | null
    [key: string]: any
  }
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
  payload: any
}

export interface AiSearchPendingAssistantMessage {
  messageId: string
  content: string
  contentType: string
  createdAt: string
  hasDelta: boolean
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

export interface AiSearchSubagentStatus {
  name: string
  label: string
  statusText: string
  startedAt: string
}
