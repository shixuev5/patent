import { defineStore } from 'pinia'
import { requestJson, requestRaw } from '~/utils/apiClient'
import { useTaskStore } from '~/stores/task'
import type {
  AiSearchActiveRun,
  AiSearchCreateSessionResponse,
  AiSearchPendingAssistantMessage,
  AiSearchPhaseMarker,
  AiSearchSessionListResponse,
  AiSearchSnapshot,
  AiSearchStreamEvent,
  AiSearchSubagentStatus,
} from '~/types/aiSearch'

const EXECUTION_PHASES = ['execute_search', 'coarse_screen', 'close_read', 'generate_feature_table']

interface AiSearchSessionRuntime {
  activeRun: AiSearchActiveRun | null
  pendingAssistantMessage: AiSearchPendingAssistantMessage | null
  phaseMarkers: AiSearchPhaseMarker[]
  activeSubagentStatuses: Record<string, AiSearchSubagentStatus>
  streaming: boolean
  error: string
}

const phaseToTaskStatus = (phase: string): string => {
  if (phase === 'awaiting_user_answer' || phase === 'awaiting_plan_confirmation') return 'paused'
  if (phase === 'completed') return 'completed'
  if (phase === 'failed') return 'failed'
  if (phase === 'cancelled') return 'cancelled'
  return 'processing'
}

const isExecutionPhase = (phase: string): boolean => EXECUTION_PHASES.includes(String(phase || '').trim())

const formatSubagentName = (name: string): string => {
  if (name === 'search-elements') return '检索要素整理'
  if (name === 'claim-decomposer') return '权利要求拆解'
  if (name === 'claim-search-strategist') return '检索策略规划'
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
      return data?.detail?.message || data?.detail || data?.message || `请求失败（HTTP ${response.status}）`
    } catch (_error) {
      return `请求失败（HTTP ${response.status}）`
    }
  }
  try {
    const text = await response.text()
    return text || `请求失败（HTTP ${response.status}）`
  } catch (_error) {
    return `请求失败（HTTP ${response.status}）`
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

export const useAiSearchStore = defineStore('aiSearch', {
  state: () => ({
    sessions: [] as Array<Record<string, any>>,
    currentSessionId: '' as string,
    sessionSnapshotsById: {} as Record<string, AiSearchSnapshot>,
    sessionRuntimeById: {} as Record<string, AiSearchSessionRuntime>,
    sessionMutationBusyById: {} as Record<string, boolean>,
    loading: false,
  }),

  getters: {
    currentSession: (state): AiSearchSnapshot | null => {
      const sessionId = String(state.currentSessionId || '').trim()
      return sessionId ? (state.sessionSnapshotsById[sessionId] || null) : null
    },

    activeSummary: (state) => state.sessions.find((item) => item.sessionId === state.currentSessionId) || null,

    phase(): string {
      return this.currentSession?.phase || ''
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
      const phase = this.currentSession?.phase || ''
      return this.streaming || isExecutionPhase(phase) || !!this.currentSession?.pendingQuestion || !!this.currentSession?.resumeAction?.available
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

    _applySnapshot(snapshot: AiSearchSnapshot, options: { activate?: boolean } = {}) {
      const sessionId = String(snapshot.session?.sessionId || '').trim()
      if (!sessionId) return
      this.sessionSnapshotsById = {
        ...this.sessionSnapshotsById,
        [sessionId]: {
          ...snapshot,
          messages: normalizeMessages(snapshot.messages),
        },
      }
      this._ensureRuntime(sessionId)
      this._upsertSessionSummary(snapshot.session as unknown as Record<string, any>)
      if (options.activate !== false) {
        this._setCurrentSessionId(sessionId)
      }
    },

    _getSnapshot(sessionId: string): AiSearchSnapshot | null {
      return this.sessionSnapshotsById[String(sessionId || '').trim()] || null
    },

    _pushMessage(sessionId: string, message: Record<string, any>) {
      const snapshot = this._getSnapshot(sessionId)
      if (!snapshot) return
      const messageId = String(message.message_id || '').trim()
      if (messageId) {
        const index = snapshot.messages.findIndex((item) => String(item.message_id || '').trim() === messageId)
        if (index >= 0) {
          snapshot.messages[index] = { ...snapshot.messages[index], ...message }
          return
        }
      }
      snapshot.messages.push(message)
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
      snapshot.phase = normalizedPhase
      snapshot.session.phase = normalizedPhase
      snapshot.session.status = phaseToTaskStatus(normalizedPhase)
      if (normalizedPhase !== 'awaiting_user_answer') {
        snapshot.pendingQuestion = null
      }
      if (normalizedPhase !== 'awaiting_plan_confirmation') {
        snapshot.pendingConfirmation = null
      }
      if (!isExecutionPhase(normalizedPhase)) {
        snapshot.resumeAction = null
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
      runtime.phaseMarkers = [
        ...runtime.phaseMarkers,
        {
          id: `phase-${runKey}-${normalizedPhase}`,
          runKey,
          phase: normalizedPhase,
          createdAt: nowIso(),
        },
      ]
    },

    _startPendingAssistant(sessionId: string, phaseHint?: string, forceNewRun: boolean = false) {
      const snapshot = this._getSnapshot(sessionId)
      const runtime = this._ensureRuntime(sessionId)
      const createdAt = forceNewRun ? nowIso() : (runtime.pendingAssistantMessage?.createdAt || nowIso())
      const messageId = forceNewRun ? `pending-${Date.now()}` : (runtime.pendingAssistantMessage?.messageId || `pending-${Date.now()}`)
      const nextPhase = String(phaseHint || snapshot?.phase || '').trim()
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
      const phase = String(event.phase || payload?.phase || snapshot?.phase || '').trim()

      if (phase && snapshot) this._syncSessionPhase(sessionId, phase)

      if (event.type === 'run.started') {
        this._setRuntimeError(sessionId, '')
        this._startPendingAssistant(sessionId)
        return
      }

      if (event.type === 'phase.changed') {
        const nextPhase = String(payload?.phase || phase || '').trim()
        if (nextPhase && snapshot) {
          this._syncSessionPhase(sessionId, nextPhase)
          this._ensurePhaseMarker(sessionId, nextPhase)
        }
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

      if (event.type === 'question.required') {
        snapshot.pendingQuestion = payload || null
        return
      }

      if (event.type === 'search_elements.updated') {
        snapshot.searchElements = payload || null
        return
      }

      if (event.type === 'plan.updated') {
        snapshot.currentPlan = payload || null
        const planVersion = Number(payload?.plan_version || payload?.planVersion || 0)
        if (planVersion > 0) {
          snapshot.session.activePlanVersion = planVersion
          this._upsertSessionSummary({ ...snapshot.session })
        }
        return
      }

      if (event.type === 'plan.awaiting_confirmation') {
        snapshot.pendingConfirmation = payload || null
        return
      }

      if (event.type === 'documents.updated') {
        snapshot.candidateDocuments = Array.isArray(payload?.items) ? payload.items : []
        return
      }

      if (event.type === 'selection.updated') {
        snapshot.selectedDocuments = Array.isArray(payload?.items) ? payload.items : []
        snapshot.session.selectedDocumentCount = snapshot.selectedDocuments.length
        this._upsertSessionSummary({ ...snapshot.session })
        return
      }

      if (event.type === 'feature_table.updated') {
        snapshot.featureTable = payload || null
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
        runtime.activeRun = null
        runtime.pendingAssistantMessage = null
        runtime.activeSubagentStatuses = {}
        runtime.streaming = false
        return
      }

      if (event.type === 'run.error') {
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
      } catch (error: any) {
        this._setRuntimeError(this.currentSessionId, error?.message || '获取会话失败')
      } finally {
        this.loading = false
      }
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
        await this.fetchSessions()
        return String(data.sessionId || '').trim()
      } catch (error: any) {
        const message = error?.message || '创建 AI 检索草稿失败'
        this._setRuntimeError(this.currentSessionId, message)
        throw error instanceof Error ? error : new Error(message)
      } finally {
        this.loading = false
      }
    },

    async loadSession(sessionId: string, options: { activate?: boolean } = {}) {
      const targetSessionId = String(sessionId || '').trim()
      if (!targetSessionId) return
      this.loading = true
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
      } catch (error: any) {
        this._setRuntimeError(targetSessionId, error?.message || '加载会话失败')
      } finally {
        this.loading = false
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

    async sendMessage(content: string) {
      const sessionId = String(this.currentSessionId || '').trim()
      const snapshot = this._getSnapshot(sessionId)
      const text = String(content || '').trim()
      if (!text || !sessionId || !snapshot) return
      this._setRuntimeError(sessionId, '')
      this._setStreaming(sessionId, true)
      this._pushUserMessage(sessionId, text, 'chat')
      snapshot.pendingConfirmation = null
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

    async answerQuestion(questionId: string, answer: string) {
      const sessionId = String(this.currentSessionId || '').trim()
      const snapshot = this._getSnapshot(sessionId)
      const text = String(answer || '').trim()
      if (!text || !sessionId || !snapshot) return
      this._setRuntimeError(sessionId, '')
      this._setStreaming(sessionId, true)
      this._pushUserMessage(sessionId, text, 'answer', questionId)
      snapshot.pendingQuestion = null
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
      snapshot.pendingConfirmation = null
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

    async generateFeatureTable(planVersion: number) {
      const sessionId = String(this.currentSessionId || '').trim()
      const snapshot = this._getSnapshot(sessionId)
      if (!sessionId || !snapshot) return
      this._setRuntimeError(sessionId, '')
      this._setStreaming(sessionId, true)
      this._startPendingAssistant(sessionId, 'generate_feature_table', true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(sessionId)}/feature-table/stream`,
          { planVersion },
        )
      } catch (error: any) {
        this._setRuntimeError(sessionId, error?.message || '生成特征对比表失败')
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
      this._startPendingAssistant(sessionId, snapshot.phase, true)
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
