import { defineStore } from 'pinia'
import { requestJson, requestRaw } from '~/utils/apiClient'
import { useTaskStore } from '~/stores/task'
import type {
  AiSearchActiveRun,
  AiSearchBatchSummary,
  AiSearchCreateSessionResponse,
  AiSearchExecutionQueueResponse,
  AiSearchPendingAction,
  AiSearchPendingAssistantMessage,
  AiSearchPhaseMarker,
  AiSearchSessionListResponse,
  AiSearchSnapshot,
  AiSearchStreamEvent,
  AiSearchSubagentStatus,
} from '~/types/aiSearch'

const EXECUTION_PHASES = ['execute_search', 'coarse_screen', 'close_read', 'feature_comparison']

interface AiSearchSessionRuntime {
  activeRun: AiSearchActiveRun | null
  pendingAssistantMessage: AiSearchPendingAssistantMessage | null
  phaseMarkers: AiSearchPhaseMarker[]
  activeSubagentStatuses: Record<string, AiSearchSubagentStatus>
  streaming: boolean
  error: string
}

const phaseToTaskStatus = (phase: string): string => {
  if (phase === 'awaiting_user_answer' || phase === 'awaiting_plan_confirmation' || phase === 'awaiting_human_decision') return 'paused'
  if (phase === 'completed') return 'completed'
  if (phase === 'failed') return 'failed'
  if (phase === 'cancelled') return 'cancelled'
  return 'processing'
}

const isExecutionPhase = (phase: string): boolean => EXECUTION_PHASES.includes(String(phase || '').trim())

const formatSubagentName = (name: string): string => {
  if (name === 'search-elements') return '检索要素整理'
  if (name === 'plan-prober') return '计划预检'
  if (name === 'query-executor') return '检索执行'
  if (name === 'coarse-screener') return '候选粗筛'
  if (name === 'close-reader') return '重点精读'
  if (name === 'feature-comparer') return '特征对比'
  return name || '子 agent'
}

const parseErrorMessage = async (response: Response): Promise<string> => {
  const contentType = String(response.headers.get('content-type') || '').toLowerCase()
  if (contentType.includes('application/json')) {
    try {
      const data = await response.json() as any
      return data?.detail?.message || data?.detail || data?.message || `请求失败(HTTP ${response.status})`
    } catch (_error) {
      return `请求失败(HTTP ${response.status})`
    }
  }
  try {
    const text = await response.text()
    return text || `请求失败(HTTP ${response.status})`
  } catch (_error) {
    return `请求失败(HTTP ${response.status})`
  }
}

const normalizeMessages = (messages: Array<Record<string, any>> | undefined) => (
  Array.isArray(messages) ? messages : []
)

const toMillis = (value?: string | null): number => {
  const ts = Date.parse(String(value || ''))
  return Number.isFinite(ts) ? ts : 0
}

const nowIso = (): string => new Date().toISOString()

const pendingAction = (snapshot?: AiSearchSnapshot | null): AiSearchPendingAction | null => {
  const value = snapshot?.conversation?.pendingAction
  return value && typeof value === 'object' ? value as AiSearchPendingAction : null
}

const pendingActionType = (snapshot?: AiSearchSnapshot | null): string => String(pendingAction(snapshot)?.actionType || '').trim()

const activePhase = (snapshot?: AiSearchSnapshot | null): string => String(snapshot?.run?.phase || snapshot?.session?.phase || '').trim()

const resumeAction = (snapshot?: AiSearchSnapshot | null): Record<string, any> | null => {
  const activeTodo = snapshot?.retrieval?.activeTodo
  if (!activeTodo || String(activeTodo.status || '').trim() !== 'failed') return null
  return {
    available: true,
    currentTask: String(activeTodo.todo_id || '').trim() || null,
    taskTitle: String(activeTodo.title || '').trim() || null,
    resumeFrom: String(activeTodo.resume_from || '').trim() || null,
    attemptCount: Number(activeTodo.attempt_count || 0),
    lastError: String(activeTodo.last_error || '').trim() || null,
  }
}

const pendingAssistantFromId = (messageId: string, createdAt?: string): AiSearchPendingAssistantMessage => ({
  messageId,
  content: '',
  contentType: 'markdown',
  createdAt: createdAt || nowIso(),
  hasDelta: false,
})

const createEmptyRuntime = (): AiSearchSessionRuntime => ({
  activeRun: null,
  pendingAssistantMessage: null,
  phaseMarkers: [],
  activeSubagentStatuses: {},
  streaming: false,
  error: '',
})

let fetchSessionsPromise: Promise<void> | null = null

const createPlaceholderSnapshot = (summary: Record<string, any>): AiSearchSnapshot => ({
  session: {
    sessionId: String(summary.sessionId || '').trim(),
    taskId: String(summary.taskId || summary.sessionId || '').trim(),
    title: String(summary.title || 'AI 检索工作台'),
    status: String(summary.status || 'processing'),
    phase: String(summary.phase || 'collecting_requirements'),
    sourceTaskId: String(summary.sourceTaskId || '').trim() || null,
    sourceType: String(summary.sourceType || '').trim() || null,
    pinned: !!summary.pinned,
    activePlanVersion: summary.activePlanVersion ?? null,
    selectedDocumentCount: Number(summary.selectedDocumentCount || 0),
    createdAt: summary.createdAt || null,
    updatedAt: summary.updatedAt || null,
  },
  run: {
    runId: null,
    phase: String(summary.phase || 'collecting_requirements'),
    status: String(summary.status || 'processing'),
    planVersion: summary.activePlanVersion ?? null,
    activeRetrievalTodoId: null,
    activeBatchId: null,
    selectedDocumentCount: Number(summary.selectedDocumentCount || 0),
  },
  conversation: {
    messages: [],
    pendingAction: null,
  },
  executionMessageQueue: {
    items: [],
  },
  plan: {
    currentPlan: null,
  },
  retrieval: {
    todos: [],
    activeTodo: null,
    documents: {
      candidates: [],
      selected: [],
    },
  },
  analysis: {
    activeBatch: null,
    latestCloseReadResult: null,
    latestFeatureCompareResult: null,
  },
  artifacts: {
    downloadUrl: null,
  },
  analysisSeed: summary.analysisSeed || null,
})

export const useAiSearchStore = defineStore('aiSearch', {
  state: () => ({
    sessions: [] as Array<Record<string, any>>,
    currentSessionId: '' as string,
    sessionSnapshotsById: {} as Record<string, AiSearchSnapshot>,
    sessionRuntimeById: {} as Record<string, AiSearchSessionRuntime>,
    sessionMutationBusyById: {} as Record<string, boolean>,
    loading: false,
    sessionsHydrated: false,
  }),

  getters: {
    currentSession: (state): AiSearchSnapshot | null => {
      const sessionId = String(state.currentSessionId || '').trim()
      return sessionId ? (state.sessionSnapshotsById[sessionId] || null) : null
    },

    activeSummary: (state) => state.sessions.find((item) => item.sessionId === state.currentSessionId) || null,

    phase(): string {
      return activePhase(this.currentSession)
    },

    pendingAssistantMessage(): AiSearchPendingAssistantMessage | null {
      const sessionId = String(this.currentSessionId || '').trim()
      return sessionId ? (this.sessionRuntimeById[sessionId]?.pendingAssistantMessage || null) : null
    },

    phaseMarkers(): AiSearchPhaseMarker[] {
      const sessionId = String(this.currentSessionId || '').trim()
      return sessionId ? (this.sessionRuntimeById[sessionId]?.phaseMarkers || []) : []
    },

    activeSubagentStatuses(): Record<string, AiSearchSubagentStatus> {
      const sessionId = String(this.currentSessionId || '').trim()
      return sessionId ? (this.sessionRuntimeById[sessionId]?.activeSubagentStatuses || {}) : {}
    },

    streaming(): boolean {
      const sessionId = String(this.currentSessionId || '').trim()
      return !!(sessionId && this.sessionRuntimeById[sessionId]?.streaming)
    },

    error(): string {
      const sessionId = String(this.currentSessionId || '').trim()
      return sessionId ? String(this.sessionRuntimeById[sessionId]?.error || '') : ''
    },

    inputDisabled(): boolean {
      const phase = activePhase(this.currentSession)
      const action = pendingAction(this.currentSession)
      return (!isExecutionPhase(phase) && this.streaming)
        || pendingActionType(this.currentSession) === 'question'
        || !!resumeAction(this.currentSession)?.available
        || action?.actionType === 'human_decision'
    },
  },

  actions: {
    async _ensureToken(): Promise<string> {
      const taskStore = useTaskStore()
      const authed = await taskStore.ensureAuth()
      if (!authed || !taskStore.authToken) {
        throw new Error('认证失效，请刷新页面后重试。')
      }
      return taskStore.authToken
    },

    _ensureRuntime(sessionId: string): AiSearchSessionRuntime {
      const targetSessionId = String(sessionId || '').trim()
      if (!targetSessionId) return createEmptyRuntime()
      if (!this.sessionRuntimeById[targetSessionId]) {
        this.sessionRuntimeById[targetSessionId] = createEmptyRuntime()
      }
      return this.sessionRuntimeById[targetSessionId]
    },

    _setRuntime(sessionId: string, patch: Partial<AiSearchSessionRuntime>) {
      const runtime = this._ensureRuntime(sessionId)
      this.sessionRuntimeById[sessionId] = {
        ...runtime,
        ...patch,
      }
    },

    _resetTransientRunState(sessionId: string) {
      this._setRuntime(sessionId, {
        activeRun: null,
        pendingAssistantMessage: null,
        activeSubagentStatuses: {},
      })
    },

    _setRuntimeError(sessionId: string, message: string) {
      this._setRuntime(sessionId, { error: String(message || '') })
    },

    _setSessionMutationBusy(sessionId: string, busy: boolean) {
      const targetSessionId = String(sessionId || '').trim()
      if (!targetSessionId) return
      this.sessionMutationBusyById = {
        ...this.sessionMutationBusyById,
        [targetSessionId]: busy,
      }
    },

    isSessionMutating(sessionId: string): boolean {
      return !!this.sessionMutationBusyById[String(sessionId || '').trim()]
    },

    findSessionBySourceTaskId(sourceTaskId: string): Record<string, any> | null {
      const targetSourceTaskId = String(sourceTaskId || '').trim()
      if (!targetSourceTaskId) return null
      return this.sessions.find((item) => String(item.sourceTaskId || '').trim() === targetSourceTaskId) || null
    },

    _upsertSessionSummary(summary: Record<string, any>) {
      const sessionId = String(summary.sessionId || '').trim()
      if (!sessionId) return
      const index = this.sessions.findIndex((item) => item.sessionId === sessionId)
      if (index >= 0) {
        this.sessions[index] = { ...this.sessions[index], ...summary }
      } else {
        this.sessions.unshift(summary)
      }
    },

    _sortedSessionIds(excludeSessionId?: string) {
      return [...this.sessions]
        .filter((item) => item.sessionId !== excludeSessionId)
        .sort((left, right) => {
          if (Boolean(left.pinned) !== Boolean(right.pinned)) return left.pinned ? -1 : 1
          const diff = toMillis(right.updatedAt || right.createdAt) - toMillis(left.updatedAt || left.createdAt)
          if (diff !== 0) return diff
          return String(right.sessionId || '').localeCompare(String(left.sessionId || ''))
        })
        .map((item) => String(item.sessionId || '').trim())
        .filter(Boolean)
    },

    _setCurrentSessionId(sessionId: string) {
      this.currentSessionId = String(sessionId || '').trim()
    },

    activateSession(sessionId: string) {
      const targetSessionId = String(sessionId || '').trim()
      if (!targetSessionId) return
      if (!this.sessionSnapshotsById[targetSessionId]) {
        const summary = this.sessions.find((item) => String(item.sessionId || '').trim() === targetSessionId)
        if (summary) {
          this._applySnapshot(createPlaceholderSnapshot(summary), { activate: false })
        }
      }
      this._ensureRuntime(targetSessionId)
      this._setCurrentSessionId(targetSessionId)
    },

    _applySnapshot(snapshot: AiSearchSnapshot, options: { activate?: boolean } = {}) {
      const sessionId = String(snapshot.session?.sessionId || '').trim()
      if (!sessionId) return
      this.sessionSnapshotsById = {
        ...this.sessionSnapshotsById,
        [sessionId]: {
          ...snapshot,
          conversation: {
            ...(snapshot.conversation || { messages: [], pendingAction: null }),
            messages: normalizeMessages(snapshot.conversation?.messages),
          },
          executionMessageQueue: {
            items: Array.isArray(snapshot.executionMessageQueue?.items) ? snapshot.executionMessageQueue.items : [],
          },
        },
      }
      this._ensureRuntime(sessionId)
      this._upsertSessionSummary(snapshot.session as unknown as Record<string, any>)
      if (options.activate !== false) {
        this._setCurrentSessionId(sessionId)
      }
    },

    _shouldAutoStartAnalysisSeed(sessionId: string): boolean {
      const snapshot = this._getSnapshot(sessionId)
      const status = String(snapshot?.analysisSeed?.status || '').trim()
      const runtime = this._ensureRuntime(sessionId)
      if (!snapshot || status !== 'pending') return false
      return !runtime.streaming
    },

    _triggerAnalysisSeedIfNeeded(sessionId: string) {
      const targetSessionId = String(sessionId || '').trim()
      if (!targetSessionId || !this._shouldAutoStartAnalysisSeed(targetSessionId)) return
      void this.streamAnalysisSeed(targetSessionId)
    },

    _getSnapshot(sessionId: string): AiSearchSnapshot | null {
      return this.sessionSnapshotsById[String(sessionId || '').trim()] || null
    },

    _pushMessage(sessionId: string, message: Record<string, any>) {
      const snapshot = this._getSnapshot(sessionId)
      if (!snapshot) return
      const messages = normalizeMessages(snapshot.conversation?.messages)
      const messageId = String(message.message_id || '').trim()
      if (messageId) {
        const index = messages.findIndex((item) => String(item.message_id || '').trim() === messageId)
        if (index >= 0) {
          messages[index] = { ...messages[index], ...message }
          snapshot.conversation.messages = [...messages]
          return
        }
      }
      snapshot.conversation.messages = [...messages, message]
    },

    _pushAssistantMessage(sessionId: string, content: string, messageId?: string) {
      const text = String(content || '')
      if (!text.trim()) return
      this._pushMessage(sessionId, {
        message_id: messageId || `assistant-${Date.now()}`,
        role: 'assistant',
        kind: 'chat',
        content: text,
        created_at: nowIso(),
      })
    },

    _pushUserMessage(sessionId: string, content: string, kind: 'chat' | 'answer' = 'chat', questionId?: string) {
      const text = String(content || '')
      if (!text.trim()) return
      this._pushMessage(sessionId, {
        message_id: `user-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        role: 'user',
        kind,
        question_id: questionId,
        content: text,
        created_at: nowIso(),
      })
    },

    _syncSessionPhase(sessionId: string, phase: string) {
      const snapshot = this._getSnapshot(sessionId)
      if (!snapshot) return
      const runtime = this._ensureRuntime(sessionId)
      const normalizedPhase = String(phase || '').trim()
      if (!normalizedPhase) return
      snapshot.session.phase = normalizedPhase
      snapshot.session.status = phaseToTaskStatus(normalizedPhase)
      snapshot.run = {
        ...(snapshot.run || {}),
        phase: normalizedPhase,
        status: phaseToTaskStatus(normalizedPhase),
      }
      if (!['awaiting_user_answer', 'awaiting_plan_confirmation', 'awaiting_human_decision'].includes(normalizedPhase) && snapshot.conversation) {
        snapshot.conversation.pendingAction = null
      }
      if (runtime.activeRun) {
        runtime.activeRun = { ...runtime.activeRun, phase: normalizedPhase }
      }
      this._upsertSessionSummary({
        ...snapshot.session,
        phase: normalizedPhase,
        status: snapshot.session.status,
      })
    },

    _ensurePhaseMarker(sessionId: string, phase: string) {
        const normalizedPhase = String(phase || '').trim()
        const runtime = this._ensureRuntime(sessionId)
        const runKey = String(runtime.activeRun?.runKey || '').trim()
        if (!normalizedPhase || !runKey) return
        if (runtime.phaseMarkers.some((item) => item.runKey === runKey && item.phase === normalizedPhase)) return
        const createdAt = nowIso()
        const previous = runtime.phaseMarkers[runtime.phaseMarkers.length - 1]
        if (previous && !previous.endedAt) {
          runtime.phaseMarkers = [
            ...runtime.phaseMarkers.slice(0, -1),
            { ...previous, endedAt: createdAt },
          ]
        }
        runtime.phaseMarkers = [
          ...runtime.phaseMarkers,
          {
            id: `phase-${runKey}-${normalizedPhase}`,
            runKey,
            phase: normalizedPhase,
            createdAt,
            endedAt: null,
          },
        ]
    },

    _closeLatestPhaseMarker(sessionId: string) {
      const runtime = this._ensureRuntime(sessionId)
      const previous = runtime.phaseMarkers[runtime.phaseMarkers.length - 1]
      if (!previous || previous.endedAt) return
      runtime.phaseMarkers = [
        ...runtime.phaseMarkers.slice(0, -1),
        { ...previous, endedAt: nowIso() },
      ]
    },

    _startPendingAssistant(sessionId: string, phaseHint?: string, forceNewRun: boolean = false) {
      const snapshot = this._getSnapshot(sessionId)
      const runtime = this._ensureRuntime(sessionId)
      const createdAt = forceNewRun ? nowIso() : (runtime.pendingAssistantMessage?.createdAt || nowIso())
      const messageId = forceNewRun ? `pending-${Date.now()}` : (runtime.pendingAssistantMessage?.messageId || `pending-${Date.now()}`)
      const nextPhase = String(phaseHint || activePhase(snapshot) || '').trim()
      if (forceNewRun || !runtime.activeRun) {
        runtime.activeRun = {
          runKey: `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          sessionId,
          startedAt: createdAt,
          phase: nextPhase,
        }
        runtime.activeSubagentStatuses = {}
        runtime.pendingAssistantMessage = pendingAssistantFromId(messageId, createdAt)
        return
      }
      runtime.activeRun = {
        ...runtime.activeRun,
        phase: nextPhase || runtime.activeRun.phase,
      }
      if (!runtime.pendingAssistantMessage) {
        runtime.pendingAssistantMessage = pendingAssistantFromId(messageId, createdAt)
      }
    },

    _setPendingAssistantMessage(sessionId: string, messageId?: string, patch?: Partial<AiSearchPendingAssistantMessage>) {
      this._startPendingAssistant(sessionId)
      const runtime = this._ensureRuntime(sessionId)
      const current = runtime.pendingAssistantMessage || pendingAssistantFromId(messageId || `pending-${Date.now()}`)
      runtime.pendingAssistantMessage = {
        ...current,
        ...(messageId ? { messageId } : {}),
        ...patch,
      }
    },

    _completePendingAssistantMessage(sessionId: string, messageId: string, content: string) {
      this._pushAssistantMessage(sessionId, content, messageId)
      this._ensureRuntime(sessionId).pendingAssistantMessage = null
    },

    _setStreaming(sessionId: string, streaming: boolean) {
      this._setRuntime(sessionId, { streaming })
    },

    _handleStreamEvent(event: AiSearchStreamEvent) {
      const sessionId = String(event.sessionId || '').trim()
      if (!sessionId) return
      const snapshot = this._getSnapshot(sessionId)
      const runtime = this._ensureRuntime(sessionId)
      const payload = event.payload || {}
      const phase = String(event.phase || payload?.run?.phase || payload?.phase || activePhase(snapshot) || '').trim()

      if (phase && snapshot) this._syncSessionPhase(sessionId, phase)

      if (event.type === 'run.started') {
        this._setRuntimeError(sessionId, '')
        this._startPendingAssistant(sessionId)
        this._ensurePhaseMarker(sessionId, phase)
        return
      }

      if (event.type === 'assistant.message.started') {
        this._setPendingAssistantMessage(sessionId, String(payload?.messageId || '').trim() || undefined, {
          contentType: String(payload?.contentType || 'markdown'),
        })
        return
      }

      if (event.type === 'assistant.message.delta') {
        const messageId = String(payload?.messageId || runtime.pendingAssistantMessage?.messageId || '').trim() || `pending-${Date.now()}`
        const delta = String(payload?.delta || '')
        this._setPendingAssistantMessage(sessionId, messageId, {
          content: `${runtime.pendingAssistantMessage?.content || ''}${delta}`,
          contentType: String(payload?.contentType || runtime.pendingAssistantMessage?.contentType || 'markdown'),
          hasDelta: !!(`${runtime.pendingAssistantMessage?.content || ''}${delta}`).trim(),
        })
        return
      }

      if (event.type === 'assistant.message.completed') {
        const messageId = String(payload?.messageId || runtime.pendingAssistantMessage?.messageId || '').trim() || `assistant-${Date.now()}`
        const content = String(payload?.content || runtime.pendingAssistantMessage?.content || '')
        if (content.trim()) {
          this._completePendingAssistantMessage(sessionId, messageId, content)
        } else {
          runtime.pendingAssistantMessage = null
        }
        return
      }

      if (!snapshot) return

      if (event.type === 'message.created') {
        this._pushMessage(sessionId, payload)
        return
      }

      if (event.type === 'run.updated') {
        if (payload?.session) {
          snapshot.session = { ...(snapshot.session || {}), ...payload.session }
          this._upsertSessionSummary({ ...snapshot.session })
        }
        if (payload?.run) {
          snapshot.run = { ...(snapshot.run || {}), ...payload.run }
          const nextPhase = String(payload.run.phase || phase || '').trim()
          if (nextPhase) {
            this._syncSessionPhase(sessionId, nextPhase)
            this._ensurePhaseMarker(sessionId, nextPhase)
          }
        }
        if (Object.prototype.hasOwnProperty.call(payload || {}, 'plan')) {
          snapshot.plan = { ...(snapshot.plan || {}), currentPlan: payload.plan || null }
        }
        if (payload?.artifacts) {
          snapshot.artifacts = { ...(snapshot.artifacts || {}), ...payload.artifacts }
        }
        return
      }

      if (event.type === 'todo.updated') {
        snapshot.retrieval = {
          ...(snapshot.retrieval || {}),
          todos: Array.isArray(payload?.items) ? payload.items : [],
          activeTodo: payload?.activeTodo || null,
        }
        return
      }

      if (event.type === 'pending_action.updated') {
        snapshot.conversation = {
          ...(snapshot.conversation || {}),
          pendingAction: payload || null,
        }
        return
      }

      if (event.type === 'documents.updated') {
        const candidates = Array.isArray(payload?.candidates) ? payload.candidates : []
        const selected = Array.isArray(payload?.selected) ? payload.selected : []
        snapshot.retrieval = {
          ...(snapshot.retrieval || {}),
          documents: {
            ...((snapshot.retrieval?.documents as Record<string, any>) || {}),
            candidates,
            selected,
          },
        }
        snapshot.session.selectedDocumentCount = selected.length
        snapshot.run = { ...(snapshot.run || {}), selectedDocumentCount: selected.length }
        this._upsertSessionSummary({ ...snapshot.session })
        return
      }

      if (event.type === 'batch.created' || event.type === 'batch.updated') {
        const activeBatch = (event.type === 'batch.created'
          ? (payload as AiSearchBatchSummary)
          : payload?.activeBatch) || null
        snapshot.analysis = {
          ...(snapshot.analysis || {}),
          activeBatch,
          latestCloseReadResult: payload?.latestCloseReadResult || snapshot.analysis?.latestCloseReadResult || null,
          latestFeatureCompareResult: payload?.latestFeatureCompareResult || snapshot.analysis?.latestFeatureCompareResult || null,
        }
        return
      }

      if (event.type === 'subagent.started') {
        const name = String(payload?.name || '').trim()
        if (name) {
          runtime.activeSubagentStatuses = {
            ...runtime.activeSubagentStatuses,
            [name]: {
              name,
              label: String(payload?.label || formatSubagentName(name)),
              statusText: String(payload?.statusText || `${formatSubagentName(name)}执行中。`),
              startedAt: nowIso(),
            },
          }
        }
        return
      }

      if (event.type === 'subagent.completed') {
        const name = String(payload?.name || '').trim()
        if (name && runtime.activeSubagentStatuses[name]) {
          const nextStatuses = { ...runtime.activeSubagentStatuses }
          delete nextStatuses[name]
          runtime.activeSubagentStatuses = nextStatuses
        }
        return
      }

      if (event.type === 'run.completed') {
        this._closeLatestPhaseMarker(sessionId)
        runtime.activeRun = null
        runtime.pendingAssistantMessage = null
        runtime.activeSubagentStatuses = {}
        runtime.streaming = false
        return
      }

      if (event.type === 'run.error') {
        this._closeLatestPhaseMarker(sessionId)
        runtime.error = String(payload?.message || '当前流式轮次执行失败。')
        runtime.streaming = false
        this._resetTransientRunState(sessionId)
      }
    },

    async fetchSessions() {
      this.loading = true
      try {
        const token = await this._ensureToken()
        const config = useRuntimeConfig()
        const data = await requestJson<AiSearchSessionListResponse>({
          baseUrl: config.public.apiBaseUrl,
          path: '/api/ai-search/sessions',
          method: 'GET',
          token,
        })
        this.sessions = Array.isArray(data.items) ? data.items : []
        this.sessionsHydrated = true
      } catch (error: any) {
        this._setRuntimeError(this.currentSessionId, error?.message || '获取会话失败')
      } finally {
        this.loading = false
      }
    },

    async ensureSessionsLoaded() {
      if (this.sessionsHydrated) return
      if (!fetchSessionsPromise) {
        fetchSessionsPromise = this.fetchSessions().finally(() => {
          fetchSessionsPromise = null
        })
      }
      await fetchSessionsPromise
    },

    async createSession() {
      this.loading = true
      try {
        const token = await this._ensureToken()
        const config = useRuntimeConfig()
        const data = await requestJson<AiSearchCreateSessionResponse>({
          baseUrl: config.public.apiBaseUrl,
          path: '/api/ai-search/sessions',
          method: 'POST',
          token,
        })
        await this.fetchSessions()
        await this.loadSession(data.sessionId, { activate: true })
      } catch (error: any) {
        this._setRuntimeError(this.currentSessionId, error?.message || '创建会话失败')
      } finally {
        this.loading = false
      }
    },

    async createSessionFromAnalysis(analysisTaskId: string) {
      const taskId = String(analysisTaskId || '').trim()
      if (!taskId) throw new Error('缺少 AI 分析任务ID。')
      this.loading = true
      try {
        const token = await this._ensureToken()
        const config = useRuntimeConfig()
        const data = await requestJson<AiSearchCreateSessionResponse>({
          baseUrl: config.public.apiBaseUrl,
          path: '/api/ai-search/sessions/from-analysis',
          method: 'POST',
          token,
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ analysisTaskId: taskId }),
        })
        const sessionId = String(data.sessionId || '').trim()
        if (sessionId) {
          if (data.reused) {
            await this.fetchSessions()
            await this.loadSession(sessionId, { activate: true })
          } else {
            const createdAt = nowIso()
            this._applySnapshot(
              createPlaceholderSnapshot({
                sessionId,
                taskId: String(data.taskId || sessionId).trim(),
                title: 'AI 检索计划',
                status: 'processing',
                phase: 'drafting_plan',
                sourceTaskId: String(data.sourceTaskId || taskId).trim() || taskId,
                sourceType: 'analysis',
                createdAt,
                updatedAt: createdAt,
                analysisSeed: {
                  status: 'pending',
                  sourceTaskId: taskId,
                },
              }),
              { activate: true },
            )
            void this.fetchSessions()
          }
        }
        return sessionId
      } catch (error: any) {
        const message = error?.message || '创建 AI 检索计划失败'
        this._setRuntimeError(this.currentSessionId, message)
        throw error instanceof Error ? error : new Error(message)
      } finally {
        this.loading = false
      }
    },

    async loadSession(sessionId: string, options: { activate?: boolean, silent?: boolean, autoStartSeed?: boolean } = {}) {
      const targetSessionId = String(sessionId || '').trim()
      if (!targetSessionId) return
      if (options.activate) {
        this.activateSession(targetSessionId)
      }
      if (!options.silent) {
        this.loading = true
      }
      this._ensureRuntime(targetSessionId).error = ''
      try {
        const token = await this._ensureToken()
        const config = useRuntimeConfig()
        const data = await requestJson<AiSearchSnapshot>({
          baseUrl: config.public.apiBaseUrl,
          path: `/api/ai-search/sessions/${encodeURIComponent(targetSessionId)}`,
          method: 'GET',
          token,
        })
        this._applySnapshot(data, options)
        if (options.autoStartSeed !== false) {
          this._triggerAnalysisSeedIfNeeded(targetSessionId)
        }
      } catch (error: any) {
        this._setRuntimeError(targetSessionId, error?.message || '加载会话失败')
      } finally {
        if (!options.silent) {
          this.loading = false
        }
      }
    },

    async init(preferredSessionId: string = '') {
      await this.fetchSessions()
      const targetId = String(preferredSessionId || this.currentSessionId || this._sortedSessionIds()[0] || '').trim()
      if (targetId) {
        await this.loadSession(targetId, { activate: true })
        return
      }
      if (!this.sessions.length) {
        await this.createSession()
      }
    },

    async _consumeStream(response: Response) {
      const reader = response.body?.getReader()
      if (!reader) return
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() || ''
        for (const part of parts) {
          const dataLine = part
            .split('\n')
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.replace(/^data:\s*/, ''))
            .join('')
            .trim()
          if (!dataLine) continue
          try {
            const event = JSON.parse(dataLine) as AiSearchStreamEvent
            this._handleStreamEvent(event)
          } catch (_error) {
            // ignore malformed chunks and keepalive comments
          }
        }
      }
    },

    async _postStream(path: string, payload: Record<string, any>) {
      const token = await this._ensureToken()
      const config = useRuntimeConfig()
      const response = await requestRaw({
        baseUrl: config.public.apiBaseUrl,
        path,
        method: 'POST',
        token,
        headers: {
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
        },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        throw new Error(await parseErrorMessage(response))
      }
      await this._consumeStream(response)
    },

    async _postJson<T>(path: string, payload?: Record<string, any>, method: 'POST' | 'DELETE' = 'POST') {
      const token = await this._ensureToken()
      const config = useRuntimeConfig()
      return requestJson<T>({
        baseUrl: config.public.apiBaseUrl,
        path,
        method,
        token,
        headers: payload ? { 'Content-Type': 'application/json' } : undefined,
        body: payload ? JSON.stringify(payload) : undefined,
      })
    },

    async sendMessage(content: string) {
      const sessionId = String(this.currentSessionId || '').trim()
      const snapshot = this._getSnapshot(sessionId)
      const text = String(content || '').trim()
      if (!text || !sessionId || !snapshot) return
      if (isExecutionPhase(activePhase(snapshot))) {
        await this.queueExecutionMessage(text)
        return
      }
      this._setRuntimeError(sessionId, '')
      this._setStreaming(sessionId, true)
      this._pushUserMessage(sessionId, text, 'chat')
      snapshot.conversation = { ...(snapshot.conversation || {}), pendingAction: null }
      this._startPendingAssistant(sessionId, 'drafting_plan', true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(sessionId)}/messages/stream`,
          { content: text },
        )
      } catch (error: any) {
        this._setRuntimeError(sessionId, error?.message || '发送消息失败')
        this._resetTransientRunState(sessionId)
      } finally {
        this._setStreaming(sessionId, false)
        await this.loadSession(sessionId, { activate: sessionId === this.currentSessionId })
      }
    },

    async queueExecutionMessage(content: string) {
      const sessionId = String(this.currentSessionId || '').trim()
      const snapshot = this._getSnapshot(sessionId)
      const text = String(content || '').trim()
      if (!text || !sessionId || !snapshot) return
      this._setRuntimeError(sessionId, '')
      try {
        await this._postJson<AiSearchExecutionQueueResponse>(
          `/api/ai-search/sessions/${encodeURIComponent(sessionId)}/execution-message-queue`,
          { content: text },
        )
      } catch (error: any) {
        this._setRuntimeError(sessionId, error?.message || '添加待执行用户消息失败')
      } finally {
        await this.loadSession(sessionId, { activate: sessionId === this.currentSessionId, silent: true })
      }
    },

    async deleteQueuedExecutionMessage(queueMessageId: string) {
      const sessionId = String(this.currentSessionId || '').trim()
      const snapshot = this._getSnapshot(sessionId)
      const targetId = String(queueMessageId || '').trim()
      if (!sessionId || !snapshot || !targetId) return
      this._setRuntimeError(sessionId, '')
      try {
        await this._postJson<AiSearchExecutionQueueResponse>(
          `/api/ai-search/sessions/${encodeURIComponent(sessionId)}/execution-message-queue/${encodeURIComponent(targetId)}`,
          undefined,
          'DELETE',
        )
      } catch (error: any) {
        this._setRuntimeError(sessionId, error?.message || '删除待执行用户消息失败')
      } finally {
        await this.loadSession(sessionId, { activate: sessionId === this.currentSessionId, silent: true })
      }
    },

    async answerQuestion(questionId: string, answer: string) {
      const sessionId = String(this.currentSessionId || '').trim()
      const snapshot = this._getSnapshot(sessionId)
      const text = String(answer || '').trim()
      if (!text || !sessionId || !snapshot) return
      this._setRuntimeError(sessionId, '')
      this._setStreaming(sessionId, true)
      this._pushUserMessage(sessionId, text, 'answer', questionId)
      snapshot.conversation = { ...(snapshot.conversation || {}), pendingAction: null }
      this._startPendingAssistant(sessionId, 'drafting_plan', true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(sessionId)}/answers/stream`,
          { questionId, answer: text },
        )
      } catch (error: any) {
        this._setRuntimeError(sessionId, error?.message || '提交回答失败')
        this._resetTransientRunState(sessionId)
      } finally {
        this._setStreaming(sessionId, false)
        await this.loadSession(sessionId, { activate: sessionId === this.currentSessionId })
      }
    },

    async confirmPlan(planVersion: number) {
      const sessionId = String(this.currentSessionId || '').trim()
      const snapshot = this._getSnapshot(sessionId)
      if (!sessionId || !snapshot) return
      this._setRuntimeError(sessionId, '')
      this._setStreaming(sessionId, true)
      snapshot.conversation = { ...(snapshot.conversation || {}), pendingAction: null }
      this._startPendingAssistant(sessionId, 'execute_search', true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(sessionId)}/plan/confirm/stream`,
          { planVersion },
        )
      } catch (error: any) {
        this._setRuntimeError(sessionId, error?.message || '确认计划失败')
        this._resetTransientRunState(sessionId)
      } finally {
        this._setStreaming(sessionId, false)
        await this.loadSession(sessionId, { activate: sessionId === this.currentSessionId })
      }
    },

    async patchSelectedDocuments(planVersion: number, addDocumentIds?: string[], removeDocumentIds?: string[]) {
      const sessionId = String(this.currentSessionId || '').trim()
      if (!sessionId) return
      this.loading = true
      this._setRuntimeError(sessionId, '')
      try {
        const token = await this._ensureToken()
        const config = useRuntimeConfig()
        const data = await requestJson<AiSearchSnapshot>({
          baseUrl: config.public.apiBaseUrl,
          path: `/api/ai-search/sessions/${encodeURIComponent(sessionId)}/selected-documents`,
          method: 'PATCH',
          token,
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            planVersion,
            addDocumentIds,
            removeDocumentIds,
          }),
        })
        this._applySnapshot(data, { activate: sessionId === this.currentSessionId })
      } catch (error: any) {
        this._setRuntimeError(sessionId, error?.message || '更新对比文件失败')
      } finally {
        this.loading = false
      }
    },

    async generateFeatureComparison(planVersion: number) {
      const sessionId = String(this.currentSessionId || '').trim()
      const snapshot = this._getSnapshot(sessionId)
      if (!sessionId || !snapshot) return
      this._setRuntimeError(sessionId, '')
      this._setStreaming(sessionId, true)
      this._startPendingAssistant(sessionId, 'feature_comparison', true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(sessionId)}/feature-comparison/stream`,
          { planVersion },
        )
      } catch (error: any) {
        this._setRuntimeError(sessionId, error?.message || '生成特征对比分析失败')
        this._resetTransientRunState(sessionId)
      } finally {
        this._setStreaming(sessionId, false)
        await this.loadSession(sessionId, { activate: sessionId === this.currentSessionId })
      }
    },

    async resumeExecution() {
      const sessionId = String(this.currentSessionId || '').trim()
      const snapshot = this._getSnapshot(sessionId)
      if (!sessionId || !snapshot) return
      this._setRuntimeError(sessionId, '')
      this._setStreaming(sessionId, true)
      this._startPendingAssistant(sessionId, activePhase(snapshot), true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(sessionId)}/resume/stream`,
          {},
        )
      } catch (error: any) {
        this._setRuntimeError(sessionId, error?.message || '恢复执行失败')
        this._resetTransientRunState(sessionId)
      } finally {
        this._setStreaming(sessionId, false)
        await this.loadSession(sessionId, { activate: sessionId === this.currentSessionId })
      }
    },

    async continueFromDecision() {
      const sessionId = String(this.currentSessionId || '').trim()
      const snapshot = this._getSnapshot(sessionId)
      if (!sessionId || !snapshot) return
      this._setRuntimeError(sessionId, '')
      this._setStreaming(sessionId, true)
      this._startPendingAssistant(sessionId, 'drafting_plan', true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(sessionId)}/decision/continue`,
          {},
        )
      } catch (error: any) {
        this._setRuntimeError(sessionId, error?.message || '继续检索失败')
        this._resetTransientRunState(sessionId)
      } finally {
        this._setStreaming(sessionId, false)
        await this.loadSession(sessionId, { activate: sessionId === this.currentSessionId })
      }
    },

    async streamAnalysisSeed(sessionId: string) {
      const targetSessionId = String(sessionId || '').trim()
      const snapshot = this._getSnapshot(targetSessionId)
      if (!targetSessionId || !snapshot) return
      if (String(snapshot.analysisSeed?.status || '').trim() !== 'pending') return
      this._setRuntimeError(targetSessionId, '')
      this._setStreaming(targetSessionId, true)
      snapshot.analysisSeed = {
        ...(snapshot.analysisSeed || {}),
        status: 'running',
      }
      this._startPendingAssistant(targetSessionId, 'drafting_plan', true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(targetSessionId)}/analysis-seed/stream`,
          {},
        )
      } catch (error: any) {
        this._setRuntimeError(targetSessionId, error?.message || '生成 AI 检索计划失败')
        this._resetTransientRunState(targetSessionId)
      } finally {
        this._setStreaming(targetSessionId, false)
        await this.loadSession(targetSessionId, {
          activate: targetSessionId === this.currentSessionId,
          silent: true,
          autoStartSeed: false,
        })
      }
    },

    async completeCurrentResultsFromDecision() {
      const sessionId = String(this.currentSessionId || '').trim()
      const snapshot = this._getSnapshot(sessionId)
      if (!sessionId || !snapshot) return
      this._setRuntimeError(sessionId, '')
      this._setStreaming(sessionId, true)
      this._startPendingAssistant(sessionId, activePhase(snapshot), true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(sessionId)}/decision/complete`,
          {},
        )
      } catch (error: any) {
        this._setRuntimeError(sessionId, error?.message || '按当前结果完成失败')
        this._resetTransientRunState(sessionId)
      } finally {
        this._setStreaming(sessionId, false)
        await this.loadSession(sessionId, { activate: sessionId === this.currentSessionId })
      }
    },

    async updateSession(sessionId: string, payload: { title?: string, pinned?: boolean }) {
      const targetSessionId = String(sessionId || '').trim()
      if (!targetSessionId) return
      this._setSessionMutationBusy(targetSessionId, true)
      this._setRuntimeError(targetSessionId, '')
      try {
        const token = await this._ensureToken()
        const config = useRuntimeConfig()
        const data = await requestJson<Record<string, any>>({
          baseUrl: config.public.apiBaseUrl,
          path: `/api/ai-search/sessions/${encodeURIComponent(targetSessionId)}`,
          method: 'PATCH',
          token,
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(payload),
        })
        this._upsertSessionSummary(data)
        const snapshot = this._getSnapshot(targetSessionId)
        if (snapshot) {
          snapshot.session = {
            ...snapshot.session,
            ...data,
          }
        }
      } catch (error: any) {
        const message = error?.message || '更新会话失败'
        this._setRuntimeError(targetSessionId, message)
        throw error instanceof Error ? error : new Error(message)
      } finally {
        this._setSessionMutationBusy(targetSessionId, false)
      }
    },

    async deleteSession(sessionId: string) {
      const targetSessionId = String(sessionId || '').trim()
      if (!targetSessionId) return
      const wasActive = this.currentSessionId === targetSessionId
      this._setSessionMutationBusy(targetSessionId, true)
      this._setRuntimeError(targetSessionId, '')
      try {
        const token = await this._ensureToken()
        const config = useRuntimeConfig()
        await requestJson<{ deleted: boolean }>({
          baseUrl: config.public.apiBaseUrl,
          path: `/api/ai-search/sessions/${encodeURIComponent(targetSessionId)}`,
          method: 'DELETE',
          token,
        })
        this.sessions = this.sessions.filter((item) => item.sessionId !== targetSessionId)
        delete this.sessionSnapshotsById[targetSessionId]
        delete this.sessionRuntimeById[targetSessionId]
        delete this.sessionMutationBusyById[targetSessionId]
        if (!wasActive) return
        this._setCurrentSessionId('')
        await this.fetchSessions()
        const nextSessionId = this._sortedSessionIds()[0]
        if (nextSessionId) {
          await this.loadSession(nextSessionId, { activate: true })
          return
        }
        await this.createSession()
      } catch (error: any) {
        const message = error?.message || '删除会话失败'
        this._setRuntimeError(targetSessionId, message)
        throw error instanceof Error ? error : new Error(message)
      } finally {
        if (this.sessionMutationBusyById[targetSessionId] !== undefined) {
          this._setSessionMutationBusy(targetSessionId, false)
        }
      }
    },
  },
})
