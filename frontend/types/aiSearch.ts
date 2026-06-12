export type AiSearchActivityState = 'running' | 'paused' | 'none'

export interface AiSearchStopPolicy {
  max_rounds?: number
  max_queries?: number
  max_candidates?: number
  max_selected_documents?: number
  max_no_new_result_rounds?: number
  deadline_seconds?: number
  target_coverage?: string
  stop_when?: string
  databases?: string[]
  [key: string]: any
}

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

export interface AiSearchRetrievalState {
  documents: {
    candidates: Array<Record<string, any>>
    selected: Array<Record<string, any>>
  }
}

export interface AiSearchSnapshot {
  session: AiSearchSessionSummary
  run: AiSearchRun
  conversation: {
    messages: Array<Record<string, any>>
    stopPolicy?: AiSearchStopPolicy
  }
  stream?: {
    lastEventSeq?: number
    activityTraces?: AiSearchActivityTrace[]
  }
  retrieval: AiSearchRetrievalState
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
  parentTraceId?: string | null
  actorName?: string | null
  toolName?: string | null
  specialistType?: string | null
  detail?: string | null
  input?: any
  output?: any
  arguments?: any
  result?: any
  metadata?: Record<string, any> | null
  startedAt?: string | null
  endedAt?: string | null
  _eventOrder?: number | null
  _eventStartedSeq?: number | null
  _eventSeq?: number | null
  _eventAt?: string | null
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
