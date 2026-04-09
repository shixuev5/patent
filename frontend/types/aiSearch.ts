export interface AiSearchSessionSummary {
  sessionId: string
  taskId: string
  title: string
  status: string
  phase: string
  pinned: boolean
  activePlanVersion?: number | null
  selectedDocumentCount: number
  createdAt?: string | null
  updatedAt?: string | null
}

export interface AiSearchSnapshot {
  session: AiSearchSessionSummary
  phase: string
  messages: Array<Record<string, any>>
  downloadUrl?: string | null
  analysisSeed?: Record<string, any> | null
  humanDecisionAction?: Record<string, any> | null
  currentPlan?: Record<string, any> | null
  executionTodos: Array<Record<string, any>>
  candidateDocuments: Array<Record<string, any>>
  selectedDocuments: Array<Record<string, any>>
  featureComparison?: Record<string, any> | null
  pendingQuestion?: Record<string, any> | null
  pendingConfirmation?: Record<string, any> | null
  resumeAction?: Record<string, any> | null
}

export interface AiSearchSessionListResponse {
  items: AiSearchSessionSummary[]
  total: number
}

export interface AiSearchCreateSessionResponse {
  sessionId: string
  taskId: string
  threadId: string
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
}

export interface AiSearchSubagentStatus {
  name: string
  label: string
  statusText: string
  startedAt: string
}
