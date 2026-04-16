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
              :disabled="loading || mockMode"
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
            :disabled="loading || mockMode"
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
                      v-if="currentSession && !mockMode"
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
                type="button"
                class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-cyan-700 text-white shadow-sm shadow-cyan-200 transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300 lg:hidden"
                aria-label="新建会话"
                :disabled="loading || mockMode"
                @click="createSession"
              >
                <PlusIcon class="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>

        <div v-if="mockMode" class="border-b border-amber-200 bg-amber-50/70 px-4 py-2 text-[12px] text-amber-800">
          Mock 数据预览模式。可切换会话查看不同阶段，提交类操作已禁用。访问 `/search?mock_ai_search=1` 开启。
        </div>

        <AiSearchConversationView
          :session-id="currentSession?.session.sessionId"
          :entries="conversationRenderEntries"
          :structured-plan-execution-spec="structuredPlanExecutionSpec"
          :active-plan-version="activePlanVersion"
          :confirmation-plan-version="confirmationPlanVersion"
          :plan-confirmation-label="planConfirmationLabel"
          :pending-question="pendingQuestion"
          :human-decision-action="humanDecisionAction"
          :selected-review-documents="selectedReviewDocuments"
          :review-candidate-documents="reviewCandidateDocuments"
          :streaming="streaming || mockMode"
          :resume-last-error="resumeLastError"
          :resume-attempt-count="resumeAttemptCount"
          :pending-assistant-content="pendingAssistantMessage?.content || ''"
          :phase="activePhase"
          :workspace-title="workspaceTitle"
          :attachments="currentSession?.artifacts?.attachments || []"
          @confirm-plan="confirmPlan"
          @resume-execution="resumeExecution"
          @request-document-review="requestDocumentReview"
          @remove-selected-document="removeSelectedDocument"
          @continue-search="continueSearchFromDecision"
          @complete-current-results="completeCurrentResultsFromDecision"
          @download-result="downloadCurrentResult"
        />

        <div v-if="showExecutionPanel" class="border-t border-slate-200">
          <button type="button" class="accordion-toggle" @click="executionPanelOpen = !executionPanelOpen">
            <span class="accordion-title">
              执行进度
              <span class="text-[11px] font-normal text-slate-400">{{ completedExecutionTodoCount }}/{{ executionTodos.length }}</span>
            </span>
            <ChevronDownIcon class="accordion-icon" :class="{ 'rotate-180': executionPanelOpen }" />
          </button>
          <div v-if="executionPanelOpen" class="accordion-body space-y-2.5">
            <div v-if="!executionTodos.length" class="rounded-2xl border border-dashed border-slate-200 px-3 py-6 text-center text-sm text-slate-500">
              计划确认后会在这里显示执行任务拆解和实时状态。
            </div>
            <div v-else class="space-y-1.5">
              <p v-if="currentExecutionHint" class="px-0.5 text-[12px] leading-6 text-slate-500">
                当前进行中：{{ currentExecutionHint }}
              </p>
              <ol class="space-y-0">
                <li
                  v-for="todo in executionTodos"
                  :key="todo.todo_id || todo.title"
                  class="px-0.5 py-0.5"
                >
                  <div class="grid grid-cols-[1.25rem,minmax(0,1fr)] items-center gap-2">
                    <span class="inline-flex h-6 w-6 shrink-0 items-center justify-center">
                      <span
                        v-if="todo.status === 'completed' || todo.status === 'failed'"
                        class="inline-flex h-4.5 w-4.5 items-center justify-center rounded-full border text-[10px]"
                        :class="todoCheckClass(todo.status)"
                      >
                        <CheckIcon v-if="todo.status === 'completed'" class="h-3.5 w-3.5" />
                        <XMarkIcon v-else class="h-3.5 w-3.5" />
                      </span>
                      <span
                        v-else-if="todo.status === 'paused'"
                        class="inline-flex h-2.5 w-2.5 rounded-full bg-amber-400"
                      />
                      <span
                        v-else-if="todo.status === 'in_progress'"
                        class="inline-flex h-2.5 w-2.5 rounded-full bg-cyan-500"
                      />
                      <span
                        v-else
                        class="inline-flex h-2.5 w-2.5 rounded-full bg-slate-300"
                      />
                    </span>
                    <p class="min-w-0 flex-1 truncate text-[13px] leading-6 text-slate-800" :class="{ 'line-through text-slate-400': todo.status === 'completed' }">
                      {{ todo.title || '未命名任务' }}
                    </p>
                  </div>
                </li>
              </ol>
            </div>
          </div>
        </div>

        <div v-if="queuedExecutionMessages.length" class="border-t border-slate-200 px-3 py-2.5">
          <ol class="space-y-2">
            <li
              v-for="(item, index) in queuedExecutionMessages"
              :key="item.queueMessageId"
              class="flex items-start gap-2 text-[13px] leading-5 text-slate-700"
            >
              <span class="shrink-0 text-slate-400">{{ index + 1 }}.</span>
              <p class="min-w-0 flex-1 whitespace-pre-wrap break-words">
                {{ item.content }}
              </p>
              <button
                type="button"
                class="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-rose-600"
                aria-label="删除待执行用户消息"
                @click="deleteQueuedExecutionMessage(item.queueMessageId)"
              >
                <XMarkIcon class="h-3.5 w-3.5" />
              </button>
            </li>
          </ol>
        </div>

        <div class="border-t border-slate-200 px-4 py-4">
          <AiSearchComposerPanel
            v-model="composer"
            :disabled="inputDisabled || !currentSession || mockMode"
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
  </div>
</template>

<script setup lang="ts">
import { Bars3Icon, CheckIcon, ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon, PencilSquareIcon, PlusIcon, XMarkIcon } from '@heroicons/vue/24/outline'
import { storeToRefs } from 'pinia'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AiSearchComposerPanel from '~/components/ai-search/AiSearchComposerPanel.vue'
import AiSearchConversationView from '~/components/ai-search/AiSearchConversationView.vue'
import AiSearchSessionGroups from '~/components/ai-search/AiSearchSessionGroups.vue'
import { useAiSearchComposer } from '~/composables/ai-search/useAiSearchComposer'
import { useAiSearchConversation } from '~/composables/ai-search/useAiSearchConversation'
import { buildAiSearchConversationMockState } from '~/mocks/aiSearchConversationMock'
import { useAdminUsageStore } from '~/stores/adminUsage'
import { useAiSearchStore } from '~/stores/aiSearch'
import { useAuthStore } from '~/stores/auth'
import { useTaskStore } from '~/stores/task'
import type { AiSearchArtifactAttachment, AiSearchSessionSummary } from '~/types/aiSearch'
import { isAiSearchExecutionPhase } from '~/utils/aiSearch'

type SessionGroup = {
  key: string
  label: string
  items: AiSearchSessionSummary[]
}

const SIDEBAR_COLLAPSED_STORAGE_KEY = 'ai-search-sidebar-collapsed'

const config = useRuntimeConfig()
const authStore = useAuthStore()
const adminUsageStore = useAdminUsageStore()
const aiSearchStore = useAiSearchStore()
const taskStore = useTaskStore()
const { showMessage } = useGlobalMessage()
const route = useRoute()
const router = useRouter()
const {
  activeSubagentStatuses,
  currentSession,
  error,
  loading,
  mockMode,
  pendingAssistantMessage,
  phaseMarkers,
  sessions,
  streaming,
} = storeToRefs(aiSearchStore)

const headerTitleDraft = ref('')
const headerTitleInputRef = ref<HTMLInputElement | null>(null)
const headerEditing = ref(false)
const sidebarCollapsed = ref(false)
const mobileDrawerOpen = ref(false)
const executionPanelOpen = ref(true)

const activePhase = computed(() => String(currentSession.value?.run?.phase || currentSession.value?.session?.phase || 'collecting_requirements'))
const messages = computed(() => currentSession.value?.conversation?.messages || [])
const processEvents = computed(() => currentSession.value?.conversation?.processEvents || [])
const currentPendingAction = computed<Record<string, any> | null>(() => {
  const value = currentSession.value?.conversation?.pendingAction
  return value && typeof value === 'object' ? value as Record<string, any> : null
})
const pendingQuestion = computed<Record<string, any> | null>(() => currentPendingAction.value?.actionType === 'question' ? currentPendingAction.value : null)
const pendingConfirmation = computed<Record<string, any> | null>(() => currentPendingAction.value?.actionType === 'plan_confirmation' ? currentPendingAction.value : null)
const humanDecisionAction = computed<Record<string, any> | null>(() => currentPendingAction.value?.actionType === 'human_decision' ? currentPendingAction.value : null)
const resumeAction = computed<Record<string, any> | null>(() => {
  const activeTodo = currentSession.value?.retrieval?.activeTodo
  if (!activeTodo || String(activeTodo.status || '') !== 'failed') return null
  return {
    available: true,
    currentTask: String(activeTodo.todo_id || '').trim() || null,
    taskTitle: String(activeTodo.title || '').trim() || null,
    resumeFrom: String(activeTodo.resume_from || '').trim() || null,
    attemptCount: Number(activeTodo.attempt_count || 0),
    lastError: String(activeTodo.last_error || '').trim() || null,
  }
})
const executionTodos = computed<Array<Record<string, any>>>(() => currentSession.value?.retrieval?.todos || [])
const queuedExecutionMessages = computed<Array<Record<string, any>>>(() => currentSession.value?.executionMessageQueue?.items || [])
const candidateDocuments = computed<Array<Record<string, any>>>(() => currentSession.value?.retrieval?.documents?.candidates || [])
const selectedDocuments = computed<Array<Record<string, any>>>(() => currentSession.value?.retrieval?.documents?.selected || [])
const reviewCandidateDocuments = computed<Array<Record<string, any>>>(() => candidateDocuments.value.filter(item => String(item?.manualAction || '').trim() === 'can_review'))
const selectedReviewDocuments = computed<Array<Record<string, any>>>(() => selectedDocuments.value.filter(item => String(item?.manualAction || '').trim() === 'can_remove'))
const completedExecutionTodoCount = computed(() => executionTodos.value.filter((todo) => todo.status === 'completed').length)
const hasActiveExecutionWork = computed(() => (
  executionTodos.value.some((todo) => todo.status === 'in_progress')
  || activeSubagentList.value.length > 0
))
const activeExecutionTodoTitle = computed(() => {
  const inProgress = executionTodos.value.find((todo) => todo.status === 'in_progress')
  const failed = executionTodos.value.find((todo) => todo.status === 'failed')
  const paused = executionTodos.value.find((todo) => todo.status === 'paused')
  return String(inProgress?.title || failed?.title || paused?.title || '').trim()
})
const currentExecutionHint = computed(() => {
  const segments = [
    activeExecutionTodoTitle.value,
    activeSubagentList.value.map(item => String(item?.label || '').trim()).filter(Boolean).join('、'),
  ].filter(Boolean)
  return segments.join(' · ')
})

const activePlanVersion = computed(() => {
  const candidate = pendingConfirmation.value?.plan_version
    || pendingConfirmation.value?.planVersion
    || currentSession.value?.plan?.currentPlan?.planVersion
    || currentSession.value?.run?.planVersion
    || currentSession.value?.session.activePlanVersion
  const value = Number(candidate || 0)
  return Number.isFinite(value) && value > 0 ? value : 0
})

const confirmationPlanVersion = computed(() => Number(pendingConfirmation.value?.plan_version || pendingConfirmation.value?.planVersion || activePlanVersion.value || 0))
const planConfirmationLabel = computed(() => String(pendingConfirmation.value?.confirmation_label || pendingConfirmation.value?.confirmationLabel || '实施此计划').trim())
const structuredPlanExecutionSpec = computed<Record<string, any> | null>(() => {
  const executionSpec = currentSession.value?.plan?.currentPlan?.executionSpec
  return executionSpec && typeof executionSpec === 'object' ? executionSpec as Record<string, any> : null
})
const resumeTaskTitle = computed(() => String(resumeAction.value?.taskTitle || '').trim())
const resumeLastError = computed(() => String(resumeAction.value?.lastError || '').trim())
const resumeAttemptCount = computed(() => Number(resumeAction.value?.attemptCount || 0))
const workspaceTitle = computed(() => String(currentSession.value?.session.title || 'AI 检索工作台'))
const activeSubagentList = computed(() => Object.values(activeSubagentStatuses.value || {}).filter((item) => item.name !== 'plan-prober'))
const resumeActionCard = computed(() => {
  if (!resumeAction.value?.available) return null
  return {
    actionType: 'resume',
    title: `${resumeTaskTitle.value || '当前执行步骤'}失败，需要显式恢复`,
    body: '系统不会把恢复当成普通聊天消息。点击下方按钮后，会从当前失败步骤继续执行。',
    severity: 'amber',
  }
})
const humanDecisionCard = computed(() => {
  if (!humanDecisionAction.value?.available) return null
  return {
    actionType: 'human_decision',
    title: '自动检索已暂停，等待人工决策',
    body: '请在当前结果基础上决定继续检索，或按当前结果完成。',
    severity: 'slate',
  }
})
const showExecutionPanel = computed(() => (
  executionTodos.value.length > 0
  || activeSubagentList.value.length > 0
  || ['execute_search', 'coarse_screen', 'close_read', 'feature_comparison', 'awaiting_human_decision', 'completed', 'failed'].includes(activePhase.value || '')
))

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
const hasAuthingEnabled = computed(() => String(config.public.authingAppId || '').trim().length > 0)
const canAccessAiSearch = computed(() => hasAuthingEnabled.value && authStore.isLoggedIn && adminUsageStore.isAdmin)
const currentSessionMutating = computed(() => {
  const sessionId = String(currentSession.value?.session.sessionId || '').trim()
  return !!sessionId && aiSearchStore.isSessionMutating(sessionId)
})
const shouldUseMockData = computed(() => String(route.query.mock_ai_search || '').trim() === '1')
const canSubmitHeaderRename = computed(() => {
  const nextTitle = headerTitleDraft.value.trim()
  const currentTitle = String(currentSession.value?.session.title || '').trim()
  return !!currentSession.value && !!nextTitle && nextTitle !== currentTitle
})

const { conversationRenderEntries } = useAiSearchConversation({
  messages,
  phaseMarkers,
  pendingAssistantMessage,
  currentPendingAction,
  resumeActionCard,
  humanDecisionCard,
  processEvents,
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
  pendingQuestion,
  pendingConfirmation,
  resumeAction,
  humanDecisionAction,
  activePhase,
  storeInputDisabled: computed(() => aiSearchStore.inputDisabled),
  sendMessage: (content: string) => aiSearchStore.sendMessage(content),
  answerQuestion: (questionId: string, content: string) => aiSearchStore.answerQuestion(questionId, content),
})

const composerHint = computed(() => {
  if (mockMode.value) return 'Mock 数据预览模式，输入已禁用。'
  if (composerMode.value === 'blocked') return '当前需要先处理上方操作卡，暂不接受普通消息。'
  if (['execute_search', 'coarse_screen', 'close_read', 'feature_comparison'].includes(activePhase.value || '')) {
    return '执行中补充的消息会在下一个执行节点统一生效。'
  }
  return ''
})

const composerHintTone = computed<'default' | 'warning'>(() => (
  composerMode.value === 'answer' ? 'warning' : 'default'
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

const todoCheckClass = (status?: string): string => {
  if (status === 'completed') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (status === 'failed') return 'border-rose-200 bg-rose-50 text-rose-700'
  if (status === 'paused') return 'border-amber-200 bg-amber-50 text-amber-700'
  if (status === 'in_progress') return 'border-cyan-200 bg-cyan-50 text-cyan-700'
  return 'border-slate-200 bg-slate-50 text-slate-400'
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
  mobileDrawerOpen.value = !mobileDrawerOpen.value
}

const createSession = async () => {
  if (mockMode.value) {
    showMessage('info', 'Mock 模式下不创建新会话。')
    return
  }
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
    await aiSearchStore.updateSession(sessionId, { title: nextTitle })
  } catch (_error) {
    // Store-level error watcher already surfaces the message.
  }
}

const toggleSessionPin = async (sessionId: string, pinned: boolean) => {
  try {
    await aiSearchStore.updateSession(sessionId, { pinned })
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

const deleteQueuedExecutionMessage = async (queueMessageId: string) => {
  await aiSearchStore.deleteQueuedExecutionMessage(queueMessageId)
}

const confirmPlan = async () => {
  if (!confirmationPlanVersion.value) return
  await aiSearchStore.confirmPlan(confirmationPlanVersion.value)
}

const resumeExecution = async () => {
  if (!resumeAction.value?.available) return
  await aiSearchStore.resumeExecution()
}

const continueSearchFromDecision = async () => {
  if (!humanDecisionAction.value?.available) return
  await aiSearchStore.continueFromDecision()
}

const completeCurrentResultsFromDecision = async () => {
  if (!humanDecisionAction.value?.available) return
  await aiSearchStore.completeCurrentResultsFromDecision()
}

const requestDocumentReview = async (documentId: string) => {
  if (!humanDecisionAction.value?.available || !activePlanVersion.value || !documentId) return
  await aiSearchStore.submitDocumentReview(activePlanVersion.value, [documentId], [])
}

const removeSelectedDocument = async (documentId: string) => {
  if (!humanDecisionAction.value?.available || !activePlanVersion.value || !documentId) return
  await aiSearchStore.submitDocumentReview(activePlanVersion.value, [], [documentId])
}

watch(
  () => currentSession.value?.session.sessionId || '',
  (sessionId) => {
    composer.value = ''
    headerEditing.value = false
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
  () => activePhase.value,
  (phase) => {
    if (isAiSearchExecutionPhase(phase)) {
      executionPanelOpen.value = true
    }
  },
)

watch(
  [hasActiveExecutionWork, () => executionTodos.value.length],
  ([activeWork, todoCount]) => {
    if (activeWork) {
      executionPanelOpen.value = true
      return
    }
    if (todoCount > 0) {
      executionPanelOpen.value = false
    }
  },
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
  if (shouldUseMockData.value) {
    if (import.meta.client) {
      const storedCollapsed = window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === '1'
      sidebarCollapsed.value = storedCollapsed
      mobileDrawerOpen.value = false
    }
    aiSearchStore.loadMockData(buildAiSearchConversationMockState())
    return
  }
  if (hasAuthingEnabled.value) {
    await authStore.ensureInitialized()
    await adminUsageStore.fetchAccess(true)
  }
  if (!canAccessAiSearch.value) {
    showMessage('error', 'AI 检索仅对已登录管理员开放。')
    await router.replace('/tasks')
    return
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
.accordion-toggle {
  @apply flex w-full items-center justify-between gap-3 px-4 py-2 text-left transition hover:bg-slate-50;
}

.accordion-title {
  @apply flex items-center gap-2 text-[13px] font-semibold text-slate-900;
}

.accordion-icon {
  @apply h-4 w-4 flex-shrink-0 text-slate-400 transition-transform;
}

.accordion-body {
  @apply border-t border-slate-200 px-4 py-3;
}

@media (max-width: 1023px) {
  .accordion-toggle {
    @apply px-3 py-2;
  }

  .accordion-body {
    @apply px-3 py-3;
  }
}

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
</style>
