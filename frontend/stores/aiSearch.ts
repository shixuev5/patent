import { defineStore } from 'pinia'
import { requestJson, requestRaw } from '~/utils/apiClient'
import { useTaskStore } from '~/stores/task'
import type {
  AiSearchCreateSessionResponse,
  AiSearchSessionListResponse,
  AiSearchSnapshot,
  AiSearchStreamEvent,
} from '~/types/aiSearch'

const phaseToTaskStatus = (phase: string): string => {
  if (phase === 'awaiting_user_answer' || phase === 'awaiting_plan_confirmation') return 'paused'
  if (phase === 'completed') return 'completed'
  if (phase === 'failed') return 'failed'
  if (phase === 'cancelled') return 'cancelled'
  return 'processing'
}

const formatSubagentName = (name: string): string => {
  if (name === 'search-elements') return '检索要素整理'
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

export const useAiSearchStore = defineStore('aiSearch', {
  state: () => ({
    sessions: [] as Array<Record<string, any>>,
    activeSessionId: '' as string,
    currentSession: null as AiSearchSnapshot | null,
    activityLog: [] as Array<Record<string, any>>,
    loading: false,
    streaming: false,
    error: '' as string,
  }),

  getters: {
    activeSummary: (state) => state.sessions.find((item) => item.sessionId === state.activeSessionId) || null,

    phase: (state) => state.currentSession?.phase || '',

    inputDisabled: (state) => {
      const phase = state.currentSession?.phase || ''
      return state.streaming || phase === 'searching' || !!state.currentSession?.pendingQuestion
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
      this.currentSession = {
        ...snapshot,
        messages: normalizeMessages(snapshot.messages),
      }
      this.activeSessionId = snapshot.session.sessionId
      if (previousSessionId && previousSessionId !== snapshot.session.sessionId) {
        this.activityLog = []
      }
      this._upsertSessionSummary(snapshot.session as unknown as Record<string, any>)
    },

    _pushAssistantMessage(content: string) {
      if (!this.currentSession || !content.trim()) return
      this.currentSession.messages.push({
        role: 'assistant',
        kind: 'chat',
        content,
        created_at: new Date().toISOString(),
      })
    },

    _recordActivity(type: string, text: string, phase: string) {
      if (!this.currentSession || !text.trim()) return
      this.activityLog.push({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        type,
        text,
        phase,
        createdAt: new Date().toISOString(),
      })
      if (this.activityLog.length > 30) {
        this.activityLog = this.activityLog.slice(-30)
      }
    },

    _syncSessionPhase(phase: string) {
      if (!this.currentSession) return
      this.currentSession.phase = phase
      this.currentSession.session.phase = phase
      this.currentSession.session.status = phaseToTaskStatus(phase)
      this._upsertSessionSummary({
        ...this.currentSession.session,
        phase,
        status: this.currentSession.session.status,
      })
    },

    _handleStreamEvent(event: AiSearchStreamEvent) {
      if (!this.currentSession || event.sessionId !== this.currentSession.session.sessionId) return
      this._syncSessionPhase(event.phase)
      if (event.type === 'message.completed') {
        const content = String(event.payload?.content || '').trim()
        if (content) this._pushAssistantMessage(content)
        return
      }
      if (event.type === 'question.required') {
        this.currentSession.pendingQuestion = event.payload || null
        this._recordActivity(event.type, '主 agent 需要用户补充检索信息。', event.phase)
        return
      }
      if (event.type === 'question.resolved') {
        this.currentSession.pendingQuestion = null
        this._recordActivity(event.type, '已恢复追问流程。', event.phase)
        return
      }
      if (event.type === 'search_elements.updated') {
        this.currentSession.searchElements = event.payload || null
        this._recordActivity(event.type, '检索要素已更新。', event.phase)
        return
      }
      if (event.type === 'plan.updated') {
        this.currentSession.currentPlan = event.payload || null
        const planVersion = Number(event.payload?.plan_version || event.payload?.planVersion || 0)
        if (planVersion > 0) {
          this.currentSession.session.activePlanVersion = planVersion
        }
        this._recordActivity(event.type, `检索计划草案已更新${planVersion > 0 ? `，当前为 V${planVersion}。` : '。'}`, event.phase)
        return
      }
      if (event.type === 'plan.awaiting_confirmation') {
        this.currentSession.pendingConfirmation = event.payload || null
        this._recordActivity(event.type, '检索计划已生成，等待用户确认。', event.phase)
        return
      }
      if (event.type === 'plan.confirmed') {
        this.currentSession.pendingConfirmation = null
        this._recordActivity(event.type, '已确认检索计划，开始执行检索。', event.phase)
        return
      }
      if (event.type === 'documents.updated') {
        this.currentSession.candidateDocuments = Array.isArray(event.payload?.items) ? event.payload.items : []
        this._recordActivity(event.type, `候选文献池已更新，共 ${this.currentSession.candidateDocuments.length} 条。`, event.phase)
        return
      }
      if (event.type === 'selection.updated') {
        this.currentSession.selectedDocuments = Array.isArray(event.payload?.items) ? event.payload.items : []
        this.currentSession.session.selectedDocumentCount = this.currentSession.selectedDocuments.length
        this._recordActivity(event.type, `当前对比文件集合已更新，共 ${this.currentSession.selectedDocuments.length} 篇。`, event.phase)
        return
      }
      if (event.type === 'feature_table.updated') {
        this.currentSession.featureTable = event.payload || null
        this._recordActivity(event.type, '特征对比表已刷新。', event.phase)
        return
      }
      if (event.type === 'subagent.started') {
        this._recordActivity(event.type, `${formatSubagentName(String(event.payload?.name || ''))}开始执行。`, event.phase)
        return
      }
      if (event.type === 'subagent.completed') {
        this._recordActivity(event.type, `${formatSubagentName(String(event.payload?.name || ''))}已完成。`, event.phase)
        return
      }
      if (event.type === 'run.completed') {
        this._recordActivity(event.type, '当前轮次已完成。', event.phase)
        return
      }
      if (event.type === 'run.error') {
        this._recordActivity(event.type, String(event.payload?.message || '当前流式轮次执行失败。'), event.phase)
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
        if (this.activeSessionId && this.activeSessionId !== sessionId) {
          this.activityLog = []
        }
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
            // ignore malformed chunks
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
      if (!text || !this.activeSessionId) return
      if (!this.currentSession) return
      this.error = ''
      this.streaming = true
      this.currentSession.messages.push({
        role: 'user',
        kind: 'chat',
        content: text,
        created_at: new Date().toISOString(),
      })
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(this.activeSessionId)}/messages/stream`,
          { content: text },
        )
      } catch (error: any) {
        this.error = error?.message || '发送消息失败'
      } finally {
        this.streaming = false
        await this.loadSession(this.activeSessionId)
      }
    },

    async answerQuestion(questionId: string, answer: string) {
      const text = String(answer || '').trim()
      if (!text || !this.activeSessionId) return
      this.error = ''
      this.streaming = true
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(this.activeSessionId)}/answers/stream`,
          { questionId, answer: text },
        )
      } catch (error: any) {
        this.error = error?.message || '提交回答失败'
      } finally {
        this.streaming = false
        await this.loadSession(this.activeSessionId)
      }
    },

    async confirmPlan(planVersion: number) {
      if (!this.activeSessionId) return
      this.error = ''
      this.streaming = true
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(this.activeSessionId)}/plan/confirm/stream`,
          { planVersion },
        )
      } catch (error: any) {
        this.error = error?.message || '确认计划失败'
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
      if (!this.activeSessionId) return
      this.error = ''
      this.streaming = true
      try {
        await this._postStream(
          `/api/ai-search/sessions/${encodeURIComponent(this.activeSessionId)}/feature-table/stream`,
          { planVersion },
        )
      } catch (error: any) {
        this.error = error?.message || '生成特征对比表失败'
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
        this.activityLog = []

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
