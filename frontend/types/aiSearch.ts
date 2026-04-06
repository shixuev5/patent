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

export interface AiSearchSourceSummary {
  sourceType: string
  sourceTaskId?: string
  sourcePn?: string
  sourceTitle?: string
  seedMode?: string
  summaryText?: string
}

export interface AiSearchSnapshot {
  session: AiSearchSessionSummary
  phase: string
  messages: Array<Record<string, any>>
  sourceSummary?: AiSearchSourceSummary | null
  searchElements?: Record<string, any> | null
  currentPlan?: Record<string, any> | null
  candidateDocuments: Array<Record<string, any>>
  selectedDocuments: Array<Record<string, any>>
  featureTable?: Record<string, any> | null
  pendingQuestion?: Record<string, any> | null
  pendingConfirmation?: Record<string, any> | null
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
