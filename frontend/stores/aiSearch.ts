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

const normalizeMessages = (messages: Array<Record<string, any>> | undefined) => {
  return Array.isArray(messages) ? messages : []
}

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

export const useAiSearchStore = defineStore('aiSearch', {
  state: () => ({
    sessions: [] as Array<Record<string, any>>,
    activeSessionId: '' as string,
    currentSession: null as AiSearchSnapshot | null,
    activeRun: null as AiSearchActiveRun | null,
    pendingAssistantMessage: null as AiSearchPendingAssistantMessage | null,
    phaseMarkers: [] as AiSearchPhaseMarker[],
    activeSubagentStatuses: {} as Record<string, AiSearchSubagentStatus>,
    loading: false,
    streaming: false,
    error: '' as string,
  }),

  getters: {
    activeSummary: (state) => state.sessions.find((item) => item.sessionId === state.activeSessionId) || null,

    phase: (state) => state.currentSession?.phase || '',

    inputDisabled: (state) => {
      const phase = state.currentSession?.phase || ''
      return state.streaming || isExecutionPhase(phase) || !!state.currentSession?.pendingQuestion || !!state.currentSession?.resumeAction?.available
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

    _resetTransientRunState() {
      this.activeRun = null
      this.pendingAssistantMessage = null
      this.activeSubagentStatuses = {}
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

    _syncCurrentSessionSummary(summary: Record<string, any>) {
      if (!this.currentSession || this.currentSession.session.sessionId !== summary.sessionId) return
      this.currentSession.session = {
        ...this.currentSession.session,
        ...summary,
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

    _applySnapshot(snapshot: AiSearchSnapshot) {
      const previousSessionId = this.currentSession?.session.sessionId || ''
      const switchingSession = previousSessionId && previousSessionId !== snapshot.session.sessionId
      this.currentSession = {
        ...snapshot,
        messages: normalizeMessages(snapshot.messages),
      }
      this.activeSessionId = snapshot.session.sessionId
      if (switchingSession) {
        this.phaseMarkers = []
        this._resetTransientRunState()
      }
      this._upsertSessionSummary(snapshot.session as unknown as Record<string, any>)
    },

    _pushMessage(message: Record<string, any>) {
      if (!this.currentSession) return
      const messageId = String(message.message_id || '').trim()
      if (messageId) {
        const index = this.currentSession.messages.findIndex((item) => String(item.message_id || '').trim() === messageId)
        if (index >= 0) {
          this.currentSession.messages[index] = { ...this.currentSession.messages[index], ...message }
          return
        }
      }
      this.currentSession.messages.push(message)
    },

    _pushAssistantMessage(content: string, messageId?: string) {
      const text = String(content || '')
      if (!this.currentSession || !text.trim()) return
      this._pushMessage({
        message_id: messageId || `assistant-${Date.now()}`,
        role: 'assistant',
        kind: 'chat',
        content: text,
        created_at: nowIso(),
      })
    },

    _pushUserMessage(content: string, kind: 'chat' | 'answer' = 'chat', questionId?: string) {
      const text = String(content || '')
      if (!this.currentSession || !text.trim()) return
      this._pushMessage({
        message_id: `user-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        role: 'user',
        kind,
        question_id: questionId,
        content: text,
        created_at: nowIso(),
      })
    },

    _syncSessionPhase(phase: string) {
      if (!this.currentSession) return
      const normalizedPhase = String(phase || '').trim()
      if (!normalizedPhase) return
      this.currentSession.phase = normalizedPhase
      this.currentSession.session.phase = normalizedPhase
      this.currentSession.session.status = phaseToTaskStatus(normalizedPhase)
      if (normalizedPhase !== 'awaiting_user_answer') {
        this.currentSession.pendingQuestion = null
      }
      if (normalizedPhase !== 'awaiting_plan_confirmation') {
        this.currentSession.pendingConfirmation = null
      }
      if (!isExecutionPhase(normalizedPhase)) {
        this.currentSession.resumeAction = null
      }
      if (this.activeRun) {
        this.activeRun = { ...this.activeRun, phase: normalizedPhase }
      }
      this._upsertSessionSummary({
        ...this.currentSession.session,
        phase: normalizedPhase,
        status: this.currentSession.session.status,
      })
    },

    _ensurePhaseMarker(phase: string) {
      const normalizedPhase = String(phase || '').trim()
      const runKey = String(this.activeRun?.runKey || '').trim()
      if (!normalizedPhase || !runKey) return
      if (this.phaseMarkers.some((item) => item.runKey === runKey && item.phase === normalizedPhase)) return
      this.phaseMarkers.push({
        id: `phase-${runKey}-${normalizedPhase}`,
        runKey,
        phase: normalizedPhase,
        createdAt: nowIso(),
      })
    },

    _startPendingAssistant(phaseHint?: string, forceNewRun: boolean = false) {
      if (!this.currentSession) return
      const createdAt = forceNewRun ? nowIso() : (this.pendingAssistantMessage?.createdAt || nowIso())
      const messageId = forceNewRun ? `pending-${Date.now()}` : (this.pendingAssistantMessage?.messageId || `pending-${Date.now()}`)
      const nextPhase = String(phaseHint || this.currentSession.phase || '').trim()
      if (forceNewRun || !this.activeRun) {
        this.activeRun = {
          runKey: `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          sessionId: this.currentSession.session.sessionId,
          startedAt: createdAt,
          phase: nextPhase,
        }
        this.activeSubagentStatuses = {}
        this.pendingAssistantMessage = pendingAssistantFromId(messageId, createdAt)
        return
      }
      this.activeRun = {
        ...this.activeRun,
        phase: nextPhase || this.activeRun.phase,
      }
      if (!this.pendingAssistantMessage) {
        this.pendingAssistantMessage = pendingAssistantFromId(messageId, createdAt)
      }
    },

    _setPendingAssistantMessage(messageId?: string, patch?: Partial<AiSearchPendingAssistantMessage>) {
      this._startPendingAssistant()
      const current = this.pendingAssistantMessage || pendingAssistantFromId(messageId || `pending-${Date.now()}`)
      this.pendingAssistantMessage = {
        ...current,
        ...(messageId ? { messageId } : {}),
        ...patch,
      }
    },

    _completePendingAssistantMessage(messageId: string, content: string) {
      this._pushAssistantMessage(content, messageId)
      this.pendingAssistantMessage = null
    },

    _handleStreamEvent(event: AiSearchStreamEvent) {
      if (!this.currentSession || event.sessionId !== this.currentSession.session.sessionId) return

      const payload = event.payload || {}
      const phase = String(event.phase || payload?.phase || this.currentSession.phase || '').trim()
      if (phase) this._syncSessionPhase(phase)

      if (event.type === 'run.started') {
        this._startPendingAssistant()
        return
      }

      if (event.type === 'phase.changed') {
        const nextPhase = String(payload?.phase || phase || '').trim()
        if (nextPhase) {
          this._syncSessionPhase(nextPhase)
          this._ensurePhaseMarker(nextPhase)
        }
        return
      }

      if (event.type === 'assistant.message.started') {
        this._setPendingAssistantMessage(String(payload?.messageId || '').trim() || undefined, {
          contentType: String(payload?.contentType || 'markdown'),
        })
        return
      }

      if (event.type === 'assistant.message.delta') {
        const messageId = String(payload?.messageId || this.pendingAssistantMessage?.messageId || '').trim() || `pending-${Date.now()}`
        const delta = String(payload?.delta || '')
        this._setPendingAssistantMessage(messageId, {
          content: `${this.pendingAssistantMessage?.content || ''}${delta}`,
          contentType: String(payload?.contentType || this.pendingAssistantMessage?.contentType || 'markdown'),
          hasDelta: !!(`${this.pendingAssistantMessage?.content || ''}${delta}`).trim(),
        })
        return
      }

      if (event.type === 'assistant.message.completed') {
        const messageId = String(payload?.messageId || this.pendingAssistantMessage?.messageId || '').trim() || `assistant-${Date.now()}`
        const content = String(payload?.content || this.pendingAssistantMessage?.content || '')
        if (content.trim()) {
          this._completePendingAssistantMessage(messageId, content)
        } else {
          this.pendingAssistantMessage = null
        }
        return
      }

      if (event.type === 'question.required') {
        this.currentSession.pendingQuestion = payload || null
        return
      }

      if (event.type === 'search_elements.updated') {
        this.currentSession.searchElements = payload || null
        return
      }

      if (event.type === 'plan.updated') {
        this.currentSession.currentPlan = payload || null
        const planVersion = Number(payload?.plan_version || payload?.planVersion || 0)
        if (planVersion > 0) {
          this.currentSession.session.activePlanVersion = planVersion
        }
        return
      }

      if (event.type === 'plan.awaiting_confirmation') {
        this.currentSession.pendingConfirmation = payload || null
        return
      }

      if (event.type === 'documents.updated') {
        this.currentSession.candidateDocuments = Array.isArray(payload?.items) ? payload.items : []
        return
      }

      if (event.type === 'selection.updated') {
        this.currentSession.selectedDocuments = Array.isArray(payload?.items) ? payload.items : []
        this.currentSession.session.selectedDocumentCount = this.currentSession.selectedDocuments.length
        return
      }

      if (event.type === 'feature_table.updated') {
        this.currentSession.featureTable = payload || null
        return
      }

      if (event.type === 'subagent.started') {
        const name = String(payload?.name || '').trim()
        if (name) {
          this.activeSubagentStatuses = {
            ...this.activeSubagentStatuses,
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
        if (name && this.activeSubagentStatuses[name]) {
          const nextStatuses = { ...this.activeSubagentStatuses }
          delete nextStatuses[name]
          this.activeSubagentStatuses = nextStatuses
        }
        return
      }

      if (event.type === 'run.completed') {
        this.activeRun = null
        this.pendingAssistantMessage = null
        this.activeSubagentStatuses = {}
        return
      }

      if (event.type === 'run.error') {
        this.error = String(payload?.message || '当前流式轮次执行失败。')
        this._resetTransientRunState()
      }
    },

    async fetchSessions() {
      this.loading = true
      this.error = ''
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
        this.error = error?.message || '获取会话失败'
      } finally {
        this.loading = false
      }
    },

    async createSession() {
      this.loading = true
      this.error = ''
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
        await this.loadSession(data.sessionId)
      } catch (error: any) {
        this.error = error?.message || '创建会话失败'
      } finally {
        this.loading = false
      }
    },

    async createSessionFromAnalysis(analysisTaskId: string) {
      const taskId = String(analysisTaskId || '').trim()
      if (!taskId) throw new Error('缺少 AI 分析任务ID。')
      this.loading = true
      this.error = ''
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
        this.error = error?.message || '创建 AI 检索草稿失败'
        throw error instanceof Error ? error : new Error(this.error)
      } finally {
        this.loading = false
      }
    },

    async loadSession(sessionId: string) {
      this.loading = true
      this.error = ''
      try {
        const token = await this._ensureToken()
        const config = useRuntimeConfig()
        const data = await requestJson<AiSearchSnapshot>({
          baseUrl: config.public.apiBaseUrl,
          path: `/api/ai-search/sessions/${encodeURIComponent(sessionId)}`,
          method: 'GET',
          token,
        })
        this._applySnapshot(data)
      } catch (error: any) {
        this.error = error?.message || '加载会话失败'
      } finally {
        this.loading = false
      }
    },

    async init(preferredSessionId: string = '') {
      await this.fetchSessions()
      const targetId = String(preferredSessionId || this.activeSessionId || this._sortedSessionIds()[0] || '').trim()
      if (targetId) {
        await this.loadSession(targetId)
        return
      }
      if (!this.sessions.length) {
        await this.createSession()
        return
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
      const text = String(content || '').trim()
      if (!text || !this.activeSessionId || !this.currentSession) return
      this.error = ''
      this.streaming = true
      this._pushUserMessage(text, 'chat')
      this.currentSession.pendingConfirmation = null
      this._startPendingAssistant('drafting_plan', true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(this.activeSessionId)}/messages/stream`,
          { content: text },
        )
      } catch (error: any) {
        this.error = error?.message || '发送消息失败'
        this._resetTransientRunState()
      } finally {
        this.streaming = false
        await this.loadSession(this.activeSessionId)
      }
    },

    async answerQuestion(questionId: string, answer: string) {
      const text = String(answer || '').trim()
      if (!text || !this.activeSessionId || !this.currentSession) return
      this.error = ''
      this.streaming = true
      this._pushUserMessage(text, 'answer', questionId)
      this.currentSession.pendingQuestion = null
      this._startPendingAssistant('drafting_plan', true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(this.activeSessionId)}/answers/stream`,
          { questionId, answer: text },
        )
      } catch (error: any) {
        this.error = error?.message || '提交回答失败'
        this._resetTransientRunState()
      } finally {
        this.streaming = false
        await this.loadSession(this.activeSessionId)
      }
    },

    async confirmPlan(planVersion: number) {
      if (!this.activeSessionId || !this.currentSession) return
      this.error = ''
      this.streaming = true
      this.currentSession.pendingConfirmation = null
      this._startPendingAssistant('execute_search', true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(this.activeSessionId)}/plan/confirm/stream`,
          { planVersion },
        )
      } catch (error: any) {
        this.error = error?.message || '确认计划失败'
        this._resetTransientRunState()
      } finally {
        this.streaming = false
        await this.loadSession(this.activeSessionId)
      }
    },

    async patchSelectedDocuments(planVersion: number, addDocumentIds?: string[], removeDocumentIds?: string[]) {
      if (!this.activeSessionId) return
      this.loading = true
      this.error = ''
      try {
        const token = await this._ensureToken()
        const config = useRuntimeConfig()
        const data = await requestJson<AiSearchSnapshot>({
          baseUrl: config.public.apiBaseUrl,
          path: `/api/ai-search/sessions/${encodeURIComponent(this.activeSessionId)}/selected-documents`,
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
        this._applySnapshot(data)
      } catch (error: any) {
        this.error = error?.message || '更新对比文件失败'
      } finally {
        this.loading = false
      }
    },

    async generateFeatureTable(planVersion: number) {
      if (!this.activeSessionId || !this.currentSession) return
      this.error = ''
      this.streaming = true
      this._startPendingAssistant('generate_feature_table', true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(this.activeSessionId)}/feature-table/stream`,
          { planVersion },
        )
      } catch (error: any) {
        this.error = error?.message || '生成特征对比表失败'
        this._resetTransientRunState()
      } finally {
        this.streaming = false
        await this.loadSession(this.activeSessionId)
      }
    },

    async resumeExecution() {
      if (!this.activeSessionId || !this.currentSession) return
      this.error = ''
      this.streaming = true
      this._startPendingAssistant(this.currentSession.phase, true)
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(this.activeSessionId)}/resume/stream`,
          {},
        )
      } catch (error: any) {
        this.error = error?.message || '恢复执行失败'
        this._resetTransientRunState()
      } finally {
        this.streaming = false
        await this.loadSession(this.activeSessionId)
      }
    },

    async updateSession(sessionId: string, payload: { title?: string, pinned?: boolean }) {
      const targetSessionId = String(sessionId || '').trim()
      if (!targetSessionId) return
      this.loading = true
      this.error = ''
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
        this._syncCurrentSessionSummary(data)
      } catch (error: any) {
        this.error = error?.message || '更新会话失败'
        throw error instanceof Error ? error : new Error(this.error)
      } finally {
        this.loading = false
      }
    },

    async deleteSession(sessionId: string) {
      const targetSessionId = String(sessionId || '').trim()
      if (!targetSessionId) return
      const wasActive = this.activeSessionId === targetSessionId
      this.loading = true
      this.error = ''
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
        if (!wasActive) return

        this.currentSession = null
        this.activeSessionId = ''
        this.phaseMarkers = []
        this._resetTransientRunState()

        await this.fetchSessions()
        const nextSessionId = this._sortedSessionIds()[0]
        if (nextSessionId) {
          await this.loadSession(nextSessionId)
          return
        }
        await this.createSession()
      } catch (error: any) {
        this.error = error?.message || '删除会话失败'
        throw error instanceof Error ? error : new Error(this.error)
      } finally {
        this.loading = false
      }
    },
  },
})
