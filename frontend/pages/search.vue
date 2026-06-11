<template>
  <div class="flex min-h-0 flex-1 flex-col overflow-hidden">
    <section class="mx-auto grid min-h-0 w-full max-w-6xl flex-1 items-start gap-3 sm:gap-4 lg:items-stretch" :class="layoutClass">
      <aside
        class="order-2 hidden h-full min-h-0 flex-col lg:order-1 lg:flex"
        :class="sidebarClass"
      >
        <div v-if="showCollapsedSidebarRail" class="rounded-[2rem] border border-slate-200 bg-white/95 px-2 py-3 shadow-sm shadow-slate-200">
          <div class="flex flex-col items-center gap-3">
            <button
              type="button"
              class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:text-slate-700"
              aria-label="展开会话侧栏"
              title="展开侧栏"
              @click="toggleSidebar"
            >
              <ChevronRightIcon class="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-cyan-700 text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
              :disabled="loading"
              @click="createSession"
            >
              <PlusIcon class="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        <div v-else class="mb-3 space-y-3">
          <div class="flex items-center justify-between gap-3">
            <h1 class="text-[15px] font-semibold text-slate-900">会话列表</h1>
            <button
              type="button"
              class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:text-slate-700"
              aria-label="收起会话侧栏"
              title="收起侧栏"
              @click="toggleSidebar"
            >
              <ChevronLeftIcon class="h-3.5 w-3.5" />
            </button>
          </div>
          <button
            type="button"
            class="w-full rounded-lg bg-cyan-700 px-3 py-2 text-[12px] font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
            :disabled="loading"
            @click="createSession"
          >
            新建会话
          </button>
        </div>

        <div v-if="!sidebarCollapsed && loading && !sessions.length" class="flex-1 rounded-2xl border border-dashed border-slate-200 px-3 py-6 text-center text-[13px] text-slate-500">
          正在加载会话...
        </div>
        <div v-else-if="!sidebarCollapsed && !sessions.length" class="flex-1 rounded-2xl bg-slate-50/90 px-3 py-6 text-center text-[13px] text-slate-500">
          暂无会话
        </div>
        <AiSearchSessionGroups
          v-else-if="!sidebarCollapsed"
          class="min-h-0 flex-1 overflow-y-auto pr-1"
          :groups="groupedSessions"
          :active-session-id="currentSession?.session.sessionId"
          :is-busy="isSessionMutating"
          @select="selectSession"
          @rename="renameSession"
          @toggle-pin="toggleSessionPin"
          @delete="deleteSession"
        />
      </aside>

      <section
        class="order-1 flex h-full min-h-0 min-w-0 w-full self-stretch justify-self-stretch flex-col overflow-hidden rounded-3xl border border-slate-200 bg-white/95 shadow-sm shadow-slate-200 lg:order-2"
      >
        <div class="border-b border-slate-200 px-4 py-2">
          <div class="flex items-center justify-between gap-2 overflow-hidden">
            <div class="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
              <div class="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
                <button
                  type="button"
                  class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-600 shadow-sm shadow-slate-200/70 transition hover:bg-slate-100 lg:hidden"
                  :aria-label="showMobileSessionDrawer ? '收起历史会话' : '展开历史会话'"
                  @click="toggleSidebar"
                >
                  <Bars3Icon class="h-4 w-4" />
                </button>
                <div class="flex min-w-0 items-center gap-2 overflow-hidden">
                  <template v-if="headerEditing && currentSession">
                    <input
                      ref="headerTitleInputRef"
                      v-model.trim="headerTitleDraft"
                      type="text"
                      class="w-full max-w-[26rem] rounded-xl border border-cyan-200 bg-white px-3 py-1.5 text-sm font-semibold text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100"
                      maxlength="120"
                      placeholder="输入会话标题"
                      @keydown.enter.prevent="submitHeaderRename"
                      @keydown.esc.prevent="cancelHeaderRename"
                    />
                    <button
                      type="button"
                      class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-cyan-200 bg-cyan-50 text-cyan-700 transition hover:bg-cyan-100 disabled:cursor-not-allowed disabled:opacity-60"
                      :disabled="currentSessionMutating || !canSubmitHeaderRename"
                      aria-label="保存会话标题"
                      title="保存"
                      @click="submitHeaderRename"
                    >
                      <CheckIcon class="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:bg-slate-100"
                      aria-label="取消重命名"
                      title="取消"
                      @click="cancelHeaderRename"
                    >
                      <XMarkIcon class="h-4 w-4" />
                    </button>
                  </template>
                  <template v-else>
                    <p class="truncate whitespace-nowrap text-sm font-semibold text-slate-900" :title="workspaceTitle">{{ workspaceTitle }}</p>
                    <button
                      v-if="currentSession"
                      type="button"
                      class="hidden h-8 w-8 shrink-0 items-center justify-center rounded-full border border-transparent text-slate-400 transition hover:border-slate-200 hover:bg-slate-50 hover:text-slate-700 lg:inline-flex"
                      :disabled="currentSessionMutating"
                      aria-label="重命名当前会话"
                      title="重命名当前会话"
                      @click="startHeaderRename"
                    >
                      <PencilSquareIcon class="h-4 w-4" />
                    </button>
                  </template>
                </div>
              </div>
            </div>
            <div class="flex shrink-0 items-center gap-2 self-center">
              <button
                v-if="currentSession && !showInvalidSessionState"
                type="button"
                class="inline-flex h-8 items-center justify-center gap-1.5 rounded-full border px-2.5 text-[12px] font-semibold transition"
                :class="contextPanelOpen ? 'border-cyan-200 bg-cyan-50 text-cyan-700' : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'"
                aria-label="打开审查上下文"
                title="审查上下文"
                @click="toggleContextPanel"
              >
                <AdjustmentsHorizontalIcon class="h-4 w-4" />
                <span class="hidden sm:inline">审查上下文</span>
                <span class="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">
                  {{ selectedDocuments.length }}/{{ candidateDocuments.length }}
                </span>
              </button>
              <button
                type="button"
                class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-cyan-700 text-white shadow-sm shadow-cyan-200 transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300 lg:hidden"
                aria-label="新建会话"
                :disabled="loading"
                @click="createSession"
              >
                <PlusIcon class="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>

        <AiSearchConversationView
          v-if="!showInvalidSessionState"
          :session-id="currentSession?.session.sessionId"
          :entries="conversationRenderEntries"
          :streaming="streaming"
          :phase="activePhase"
          :attachments="currentSession?.artifacts?.attachments || []"
          @download-result="downloadCurrentResult"
        />

        <div
          v-if="showInvalidSessionState"
          class="border-t border-slate-200 px-4 py-6"
        >
          <div class="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-900">
            <p class="font-semibold">当前会话不可用</p>
            <p class="mt-1 leading-6">
              {{ invalidSessionMessage }}
            </p>
            <div class="mt-3 flex justify-end">
              <button
                type="button"
                class="inline-flex items-center justify-center rounded-lg bg-amber-600 px-3 py-1.5 text-[12px] font-semibold text-white transition hover:bg-amber-700"
                @click="createSession"
              >
                新建会话
              </button>
            </div>
          </div>
        </div>

        <div class="border-t border-slate-200 px-4 py-4">
          <AiSearchComposerPanel
            v-if="!showInvalidSessionState"
            v-model="composer"
            :disabled="inputDisabled || !currentSession || showInvalidSessionState"
            :placeholder="inputPlaceholder"
            :can-submit="canSubmitMessage"
            :mode="composerMode"
            :hint="composerHint"
            :hint-tone="composerHintTone"
            @submit="submitMessage"
          />
        </div>
      </section>
    </section>

    <transition name="drawer-fade">
      <div
        v-if="showMobileSessionDrawer"
        class="fixed inset-x-0 bottom-0 top-[56px] z-40 bg-slate-950/14 lg:hidden"
        @click="toggleSidebar"
      />
    </transition>

    <transition name="drawer-slide">
      <aside
        v-if="showMobileSessionDrawer"
        class="fixed bottom-0 left-0 top-[56px] z-50 flex w-[17.5rem] max-w-[86vw] flex-col border-r border-slate-200 bg-white shadow-2xl shadow-slate-300/30 lg:hidden"
      >
        <div class="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <p class="text-sm font-semibold text-slate-900">历史会话</p>
          <button
            type="button"
            class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition hover:border-slate-300 hover:text-slate-700"
            aria-label="关闭历史会话抽屉"
            @click="toggleSidebar"
          >
            <ChevronLeftIcon class="h-4 w-4" />
          </button>
        </div>

        <div class="flex-1 overflow-y-auto px-3 py-3">
          <div v-if="loading && !sessions.length" class="rounded-2xl border border-dashed border-slate-200 px-3 py-5 text-center text-[13px] text-slate-500">
            正在加载会话...
          </div>
          <div v-else-if="!sessions.length" class="rounded-2xl bg-slate-50/90 px-3 py-5 text-center text-[13px] text-slate-500">
            暂无会话
          </div>
          <AiSearchSessionGroups
            v-else
            :groups="groupedSessions"
            :active-session-id="currentSession?.session.sessionId"
            :is-busy="isSessionMutating"
            @select="selectSession"
            @rename="renameSession"
            @toggle-pin="toggleSessionPin"
            @delete="deleteSession"
          />
        </div>
      </aside>
    </transition>

    <transition name="drawer-fade">
      <div
        v-if="showContextPanelDrawer"
        class="fixed inset-x-0 bottom-0 top-[56px] z-40 bg-slate-950/14"
        @click="closeContextPanel"
      />
    </transition>

    <transition name="context-drawer">
      <aside
        v-if="showContextPanelDrawer"
        class="fixed bottom-0 left-0 right-0 z-50 flex max-h-[84vh] min-h-0 flex-col overflow-hidden rounded-t-xl bg-white shadow-2xl shadow-slate-300/40 lg:bottom-0 lg:left-auto lg:right-0 lg:top-[56px] lg:h-auto lg:max-h-none lg:w-[25rem] lg:max-w-[calc(100vw-4rem)] lg:rounded-l-xl lg:rounded-tr-none"
      >
        <AiSearchContextPanel
          class="h-full w-full border-0"
          :stop-policy-draft="stopPolicyDraft"
          :database-options="databaseOptions"
          :candidate-documents="candidateDocuments"
          :selected-documents="selectedDocuments"
          :streaming="streaming"
          :has-session="!!currentSession"
          :phase="activePhase"
          closable
          @apply-stop-policy="applyStopPolicy"
          @select-document="requestDocumentReview"
          @remove-document="removeSelectedDocument"
          @quick-prompt="sendQuickPrompt"
          @cancel-run="cancelCurrentRun"
          @export-report="exportReport"
          @close="closeContextPanel"
        />
      </aside>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { AdjustmentsHorizontalIcon, Bars3Icon, CheckIcon, ChevronLeftIcon, ChevronRightIcon, PencilSquareIcon, PlusIcon, XMarkIcon } from '@heroicons/vue/24/outline'
import { storeToRefs } from 'pinia'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AiSearchComposerPanel from '~/components/ai-search/AiSearchComposerPanel.vue'
import AiSearchConversationView from '~/components/ai-search/AiSearchConversationView.vue'
import AiSearchContextPanel from '~/components/ai-search/AiSearchContextPanel.vue'
import AiSearchSessionGroups from '~/components/ai-search/AiSearchSessionGroups.vue'
import { useAiSearchComposer } from '~/composables/ai-search/useAiSearchComposer'
import { useAiSearchConversation } from '~/composables/ai-search/useAiSearchConversation'
import { useAiSearchStore } from '~/stores/aiSearch'
import { useAuthStore } from '~/stores/auth'
import { useTaskStore } from '~/stores/task'
import type { AiSearchArtifactAttachment, AiSearchSessionSummary } from '~/types/aiSearch'

type SessionGroup = {
  key: string
  label: string
  items: AiSearchSessionSummary[]
}

const SIDEBAR_COLLAPSED_STORAGE_KEY = 'ai-search-sidebar-collapsed'

const config = useRuntimeConfig()
const authStore = useAuthStore()
const aiSearchStore = useAiSearchStore()
const taskStore = useTaskStore()
const { showMessage } = useGlobalMessage()
const route = useRoute()
const router = useRouter()
const {
  activityTraces,
  currentSession,
  error,
  loading,
  phaseMarkers,
  sessions,
  streaming,
} = storeToRefs(aiSearchStore)

const headerTitleDraft = ref('')
const headerTitleInputRef = ref<HTMLInputElement | null>(null)
const headerEditing = ref(false)
const sidebarCollapsed = ref(false)
const mobileDrawerOpen = ref(false)
const contextPanelOpen = ref(false)
const stopPolicyDraft = ref({
  maxRounds: 5,
  maxQueries: 30,
  maxCandidates: 200,
  maxSelectedDocuments: 8,
  maxNoNewResultRounds: 2,
  deadlineSeconds: 600,
  targetCoverage: '',
  stopWhen: '',
  databases: ['zhihuiya', 'openalex', 'semanticscholar', 'crossref'] as string[],
})

const activePhase = computed(() => String(currentSession.value?.run?.phase || currentSession.value?.session?.phase || 'idle'))
const messages = computed(() => currentSession.value?.conversation?.messages || [])
const candidateDocuments = computed<Array<Record<string, any>>>(() => currentSession.value?.retrieval?.documents?.candidates || [])
const selectedDocuments = computed<Array<Record<string, any>>>(() => currentSession.value?.retrieval?.documents?.selected || [])
const currentStopPolicy = computed<Record<string, any>>(() => currentSession.value?.conversation?.stopPolicy || {})
const databaseOptions = [
  { value: 'zhihuiya', label: '智慧芽' },
  { value: 'openalex', label: 'OpenAlex' },
  { value: 'semanticscholar', label: 'Semantic Scholar' },
  { value: 'crossref', label: 'Crossref' },
]

const activePlanVersion = computed(() => {
  const candidate = currentSession.value?.run?.planVersion
    || currentSession.value?.session.activePlanVersion
  const value = Number(candidate || 0)
  return Number.isFinite(value) && value > 0 ? value : 0
})

const workspaceTitle = computed(() => String(currentSession.value?.session.title || 'AI 检索工作台'))
const requestedSessionId = computed(() => String(route.query.session || '').trim())
const requestedSessionError = computed(() => {
  const sessionId = requestedSessionId.value
  if (!sessionId) return ''
  const runtime = aiSearchStore.sessionRuntimeById[sessionId]
  return String(runtime?.error || '').trim()
})
const hasRequestedSessionSummary = computed(() => (
  !!requestedSessionId.value
  && sessions.value.some(item => String(item.sessionId || '').trim() === requestedSessionId.value)
))
const isShowingRequestedSession = computed(() => {
  const currentId = String(currentSession.value?.session.sessionId || '').trim()
  if (!requestedSessionId.value) return true
  return !!currentId && currentId === requestedSessionId.value
})
const showInvalidSessionState = computed(() => (
  !isShowingRequestedSession.value
  && !!requestedSessionId.value
  && (
    /加载会话失败|404|不存在|not found/i.test(requestedSessionError.value)
    || (!hasRequestedSessionSummary.value && sessions.value.length > 0)
  )
))
const invalidSessionMessage = computed(() => {
  const detail = requestedSessionError.value
  if (detail) return `会话 ${requestedSessionId.value} 无法加载，可能已被删除、失效，或不属于当前身份。`
  return '当前链接里的会话无法加载，可能已被删除、失效，或不属于当前身份。'
})
const layoutClass = computed(() => (sidebarCollapsed.value
  ? 'lg:grid-cols-[auto,minmax(0,1fr)]'
  : 'lg:grid-cols-[15rem,minmax(0,1fr)] xl:grid-cols-[15.5rem,minmax(0,1fr)]'
))
const sidebarClass = computed(() => (
  sidebarCollapsed.value
    ? 'items-center'
    : 'rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200'
))
const showCollapsedSidebarRail = computed(() => sidebarCollapsed.value)
const showMobileSessionDrawer = computed(() => mobileDrawerOpen.value)
const showContextPanelDrawer = computed(() => (
  contextPanelOpen.value && !!currentSession.value && !showInvalidSessionState.value
))
const hasAuthingEnabled = computed(() => String(config.public.authingAppId || '').trim().length > 0)
const currentSessionMutating = computed(() => {
  const sessionId = String(currentSession.value?.session.sessionId || '').trim()
  return !!sessionId && aiSearchStore.isSessionMutating(sessionId)
})
const canSubmitHeaderRename = computed(() => {
  const nextTitle = headerTitleDraft.value.trim()
  const currentTitle = String(currentSession.value?.session.title || '').trim()
  return !!currentSession.value && !!nextTitle && nextTitle !== currentTitle
})

const { conversationRenderEntries } = useAiSearchConversation({
  messages,
  activityTraces,
  phaseMarkers,
  streaming,
  phase: activePhase,
})

const {
  composer,
  composerMode,
  inputDisabled,
  canSubmitMessage,
  inputPlaceholder,
  submitMessage,
} = useAiSearchComposer({
  currentSession,
  activePhase,
  storeInputDisabled: computed(() => aiSearchStore.inputDisabled),
  sendMessage: (content: string) => aiSearchStore.sendMessage(content),
})

const composerHint = computed(() => {
  if (activePhase.value === 'running') return '可直接发送新指令调整方向；当前轮会尽快停止并保留已保存候选。'
  return ''
})

const composerHintTone = computed<'default' | 'warning'>(() => (
  'default'
))

const withTokenQuery = (url: string, token: string): string => {
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}token=${encodeURIComponent(token)}`
}

const downloadCurrentResult = async (attachment: AiSearchArtifactAttachment) => {
  const rawPath = String(attachment?.downloadUrl || '').trim()
  if (!rawPath) return
  const rawDownloadUrl = rawPath.startsWith('http') || rawPath.startsWith('data:') || rawPath.startsWith('blob:')
    ? rawPath
    : `${config.public.apiBaseUrl}${rawPath}`

  try {
    const authed = await taskStore.ensureAuth()
    const downloadUrl = authed && taskStore.authToken
      ? withTokenQuery(rawDownloadUrl, taskStore.authToken)
      : rawDownloadUrl
    const link = document.createElement('a')
    link.href = downloadUrl
    link.target = '_blank'
    link.rel = 'noopener'
    link.download = String(attachment?.name || 'ai-search-attachment').trim() || 'ai-search-attachment'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  } catch (error) {
    console.error('下载 AI 检索结果失败：', error)
    showMessage('error', '下载失败，请稍后重试。')
    window.open(rawDownloadUrl, '_blank')
  }
}

const sortedSessions = computed<AiSearchSessionSummary[]>(() => {
  return [...sessions.value].sort((left, right) => {
    if (Boolean(left.pinned) !== Boolean(right.pinned)) return left.pinned ? -1 : 1
    const diff = toMillis(right.updatedAt || right.createdAt) - toMillis(left.updatedAt || left.createdAt)
    if (diff !== 0) return diff
    return String(right.sessionId || '').localeCompare(String(left.sessionId || ''))
  })
})

const groupedSessions = computed<SessionGroup[]>(() => {
  const groups = new Map<string, SessionGroup>()
  const pinnedItems = sortedSessions.value.filter((session) => session.pinned)
  const regularItems = sortedSessions.value.filter((session) => !session.pinned)

  if (pinnedItems.length) {
    groups.set('pinned', {
      key: 'pinned',
      label: '置顶',
      items: pinnedItems,
    })
  }

  for (const session of regularItems) {
    const bucket = resolveSessionGroup(session.updatedAt || session.createdAt)
    const existing = groups.get(bucket.key)
    if (existing) {
      existing.items.push(session)
      continue
    }
    groups.set(bucket.key, {
      key: bucket.key,
      label: bucket.label,
      items: [session],
    })
  }

  return Array.from(groups.values())
})

const toMillis = (value?: string | null): number => {
  const ts = Date.parse(String(value || ''))
  return Number.isFinite(ts) ? ts : 0
}

const getDayStart = (date: Date): number => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()

const formatMonthGroup = (date: Date): string => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`

const resolveSessionGroup = (value?: string | null): { key: string, label: string } => {
  const text = String(value || '').trim()
  const date = new Date(text)
  if (Number.isNaN(date.getTime())) {
    return { key: 'older-unknown', label: '更早' }
  }

  const now = new Date()
  const dayDiff = Math.floor((getDayStart(now) - getDayStart(date)) / 86400000)

  if (dayDiff <= 0) return { key: 'today', label: '今天' }
  if (dayDiff === 1) return { key: 'yesterday', label: '昨天' }
  if (dayDiff <= 7) return { key: 'last-7-days', label: '最近 7 天' }
  if (dayDiff <= 30) return { key: 'last-30-days', label: '最近 30 天' }

  const monthLabel = formatMonthGroup(date)
  return { key: `month-${monthLabel}`, label: monthLabel }
}

const isDesktopViewport = (): boolean => {
  if (!import.meta.client) return false
  return window.matchMedia('(min-width: 1024px)').matches
}

const toggleSidebar = () => {
  if (isDesktopViewport()) {
    sidebarCollapsed.value = !sidebarCollapsed.value
    return
  }
  const nextOpen = !mobileDrawerOpen.value
  mobileDrawerOpen.value = nextOpen
  if (nextOpen) contextPanelOpen.value = false
}

const toggleContextPanel = () => {
  if (!currentSession.value || showInvalidSessionState.value) return
  contextPanelOpen.value = !contextPanelOpen.value
  if (contextPanelOpen.value) mobileDrawerOpen.value = false
}

const closeContextPanel = () => {
  contextPanelOpen.value = false
}

const createSession = async () => {
  await aiSearchStore.createSession()
}

const isSessionMutating = (sessionId: string): boolean => aiSearchStore.isSessionMutating(sessionId)

const selectSession = async (sessionId: string) => {
  if (!sessionId || sessionId === currentSession.value?.session.sessionId) return
  aiSearchStore.activateSession(sessionId)
  await aiSearchStore.loadSession(sessionId, { activate: false })
  if (!isDesktopViewport()) mobileDrawerOpen.value = false
}

const syncHeaderRenameDraft = () => {
  headerTitleDraft.value = String(currentSession.value?.session.title || '').trim()
}

const startHeaderRename = async () => {
  if (!currentSession.value || currentSessionMutating.value) return
  headerEditing.value = true
  syncHeaderRenameDraft()
  await nextTick()
  headerTitleInputRef.value?.focus()
  headerTitleInputRef.value?.select()
}

const cancelHeaderRename = () => {
  headerEditing.value = false
  syncHeaderRenameDraft()
}

const submitHeaderRename = async () => {
  const sessionId = String(currentSession.value?.session.sessionId || '').trim()
  const nextTitle = headerTitleDraft.value.trim()
  if (!sessionId) return
  if (!nextTitle || nextTitle === String(currentSession.value?.session.title || '').trim()) {
    cancelHeaderRename()
    return
  }
  headerEditing.value = false
  await renameSession(sessionId, nextTitle)
}

const renameSession = async (sessionId: string, title: string) => {
  const nextTitle = String(title || '').trim()
  if (!nextTitle) return
  try {
    await aiSearchStore.updateSessionMeta(sessionId, { title: nextTitle })
  } catch (_error) {
    // Store-level error watcher already surfaces the message.
  }
}

const toggleSessionPin = async (sessionId: string, pinned: boolean) => {
  try {
    await aiSearchStore.updateSessionMeta(sessionId, { pinned })
  } catch (_error) {
    // Store-level error watcher already surfaces the message.
  }
}

const deleteSession = async (sessionId: string) => {
  const target = sessions.value.find((item) => item.sessionId === sessionId)
  const title = String(target?.title || '该会话').trim()
  if (!window.confirm(`确定删除“${title}”吗？此操作不可撤销。`)) return
  try {
    await aiSearchStore.deleteSession(sessionId)
  } catch (_error) {
    return
  }
  if (!isDesktopViewport() && currentSession.value?.session.sessionId !== sessionId) {
    mobileDrawerOpen.value = false
  }
}

const clampInt = (value: unknown, fallback: number, min: number, max: number): number => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.max(min, Math.min(Math.round(parsed), max))
}

const syncStopPolicyDraft = () => {
  const policy = currentStopPolicy.value
  stopPolicyDraft.value = {
    maxRounds: clampInt(policy.max_rounds, 5, 1, 30),
    maxQueries: clampInt(policy.max_queries, 30, 1, 200),
    maxCandidates: clampInt(policy.max_candidates, 200, 1, 1000),
    maxSelectedDocuments: clampInt(policy.max_selected_documents, 8, 1, 50),
    maxNoNewResultRounds: clampInt(policy.max_no_new_result_rounds, 2, 1, 20),
    deadlineSeconds: clampInt(policy.deadline_seconds, 600, 30, 7200),
    targetCoverage: String(policy.target_coverage || '').trim(),
    stopWhen: String(policy.stop_when || '').trim(),
    databases: Array.isArray(policy.databases) && policy.databases.length
      ? policy.databases.map((item: unknown) => String(item || '').trim()).filter(Boolean)
      : ['zhihuiya', 'openalex', 'semanticscholar', 'crossref'],
  }
}

const applyStopPolicy = async () => {
  await aiSearchStore.updateStopPolicy({
    maxRounds: stopPolicyDraft.value.maxRounds,
    maxQueries: stopPolicyDraft.value.maxQueries,
    maxCandidates: stopPolicyDraft.value.maxCandidates,
    maxSelectedDocuments: stopPolicyDraft.value.maxSelectedDocuments,
    maxNoNewResultRounds: stopPolicyDraft.value.maxNoNewResultRounds,
    deadlineSeconds: stopPolicyDraft.value.deadlineSeconds,
    targetCoverage: stopPolicyDraft.value.targetCoverage,
    stopWhen: stopPolicyDraft.value.stopWhen,
    databases: stopPolicyDraft.value.databases,
  })
  showMessage('success', '停止条件已更新。')
}

const quickPromptText = (key: string): string => {
  const prompts: Record<string, string> = {
    'direct-search': '请基于当前会话信息直接开始检索；如果检索目标不完整，只追问最关键缺口。',
    'continue-search': '请在当前候选结果基础上继续扩展检索，并说明新增检索方向。',
    'summarize-selected': '请基于当前已选文献总结关键命中点、证据差距和下一步建议。',
    'tighten-stop': '请根据当前候选质量和停止条件，建议如何收紧检索范围；如果合适，请更新停止条件。',
  }
  return prompts[key] || ''
}

const sendQuickPrompt = async (key: string) => {
  const text = quickPromptText(key)
  if (!text || !currentSession.value) return
  await aiSearchStore.sendMessage(text)
}

const cancelCurrentRun = async () => {
  if (!currentSession.value || activePhase.value !== 'running') return
  await aiSearchStore.cancelRun()
}

const exportReport = async () => {
  if (!currentSession.value || streaming.value) return
  await aiSearchStore.exportReport()
  showMessage('success', '检索报告已导出。')
}


const requestDocumentReview = async (documentId: string) => {
  const version = activePlanVersion.value || 1
  if (!documentId) return
  await aiSearchStore.submitDocumentSelection(version, [documentId], [])
}

const removeSelectedDocument = async (documentId: string) => {
  const version = activePlanVersion.value || 1
  if (!documentId) return
  await aiSearchStore.submitDocumentSelection(version, [], [documentId])
}

watch(
  () => currentSession.value?.session.sessionId || '',
  (sessionId) => {
    composer.value = ''
    headerEditing.value = false
    contextPanelOpen.value = false
    syncHeaderRenameDraft()
    const currentQuerySession = String(route.query.session || '').trim()
    if (sessionId && sessionId !== currentQuerySession) {
      router.replace({
        query: {
          ...route.query,
          session: sessionId,
        },
      }).catch(() => {})
    }
  },
)

watch(
  () => currentSession.value?.session.title || '',
  () => {
    if (!headerEditing.value) syncHeaderRenameDraft()
  },
)

watch(
  currentStopPolicy,
  syncStopPolicyDraft,
  { immediate: true, deep: true },
)

watch(
  error,
  (value, previousValue) => {
    const text = String(value || '').trim()
    if (!text || text === previousValue) return
    showMessage('error', text)
  },
)

watch(
  sidebarCollapsed,
  (value) => {
    if (!import.meta.client) return
    window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, value ? '1' : '0')
  },
)

onMounted(async () => {
  if (hasAuthingEnabled.value) {
    await authStore.ensureInitialized()
  } else {
    await taskStore.ensureAuth()
  }
  if (import.meta.client) {
    const storedCollapsed = window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === '1'
    sidebarCollapsed.value = storedCollapsed
    mobileDrawerOpen.value = false
  }
  const preferredSessionId = String(route.query.session || '').trim()
  await aiSearchStore.init(preferredSessionId)
})
</script>

<style scoped>
.drawer-fade-enter-active,
.drawer-fade-leave-active,
.drawer-slide-enter-active,
.drawer-slide-leave-active {
  transition: all 0.22s ease;
}

.drawer-fade-enter-from,
.drawer-fade-leave-to {
  opacity: 0;
}

.drawer-slide-enter-from,
.drawer-slide-leave-to {
  opacity: 0;
  transform: translateX(-100%);
}

.context-drawer-enter-active,
.context-drawer-leave-active {
  transition: all 0.22s ease;
}

.context-drawer-enter-from,
.context-drawer-leave-to {
  opacity: 0;
  transform: translateY(100%);
}

@media (min-width: 1024px) {
  .context-drawer-enter-from,
  .context-drawer-leave-to {
    transform: translateX(100%);
  }
}
</style>
