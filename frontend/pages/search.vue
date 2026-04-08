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
        <div v-else-if="!sidebarCollapsed" class="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
          <section
            v-for="group in groupedSessions"
            :key="group.key"
            class="space-y-2"
          >
            <p class="px-1 text-[11px] font-semibold tracking-[0.18em] text-slate-400">{{ group.label }}</p>
            <AiSearchSessionListItem
              v-for="session in group.items"
              :key="session.sessionId"
              :session="session"
              :active="session.sessionId === currentSession?.session.sessionId"
              :busy="isSessionMutating(session.sessionId)"
              @select="selectSession"
              @rename="renameSession"
              @toggle-pin="toggleSessionPin"
              @delete="deleteSession"
            />
          </section>
        </div>
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
                <div class="flex shrink-0 items-center gap-2 lg:hidden">
                  <span class="shrink-0 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-600">
                    {{ activePhaseLabel }}
                  </span>
                </div>
              </div>
            </div>
            <div class="flex shrink-0 items-center gap-2 self-center">
              <div class="hidden shrink-0 items-center gap-2 lg:flex">
                <span class="shrink-0 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-600">
                  {{ activePhaseLabel }}
                </span>
              </div>
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

        <div ref="messageListRef" class="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-4">
          <div v-if="!conversationEntries.length" class="flex min-h-full flex-1 items-center justify-center px-4 py-8 text-center text-sm text-slate-500">
            描述检索目标、技术方案、核心效果或约束条件。
          </div>

          <div v-else class="space-y-4">
            <template v-for="entry in conversationEntries" :key="entry.id">
              <article v-if="entry.entryType === 'phase'" class="flex items-center gap-3 py-1">
                <span class="h-px flex-1 bg-slate-200/80" />
                <p class="shrink-0 text-[11px] font-medium tracking-[0.14em] text-slate-400">
                  ------ {{ phaseLabel(entry.phase) }} ------
                </p>
                <span class="h-px flex-1 bg-slate-200/80" />
              </article>

              <article
                v-else
                class="flex"
                :class="entry.role === 'user' ? 'justify-end' : 'justify-start'"
              >
                <div
                  class="max-w-[92%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm"
                  :class="entry.role === 'user'
                    ? 'bg-cyan-700 text-white shadow-cyan-100'
                    : 'border border-slate-200 bg-slate-50 text-slate-700'"
                >
                  <template v-if="entry.entryType === 'pending-assistant'">
                    <div v-if="entry.content" class="text-slate-700">
                      <AiSearchMarkdown :content="entry.content" />
                    </div>
                    <div v-else class="space-y-3">
                      <div class="flex items-center gap-2 text-[13px] font-medium text-slate-500">
                        <span class="inline-flex h-2 w-2 rounded-full bg-cyan-500 animate-pulse" />
                        <span>思考中</span>
                      </div>
                      <div class="space-y-2">
                        <div class="h-2.5 w-36 rounded-full bg-slate-200/80" />
                        <div class="h-2.5 w-48 rounded-full bg-slate-200/70" />
                      </div>
                    </div>
                  </template>

                  <template v-else-if="entry.role === 'assistant' && isPlanMessage(entry)">
                    <div v-if="isLatestPlanMessage(entry)" class="space-y-4">
                      <div class="flex items-center justify-between gap-3">
                        <p class="text-[12px] font-semibold tracking-[0.18em] text-slate-400">PLAN</p>
                        <span class="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-semibold text-slate-500">
                          v{{ planVersionOf(entry) || activePlanVersion || '?' }}
                        </span>
                      </div>
                      <AiSearchMarkdown :content="entry.content" />
                      <AiSearchPlanConfirmationCard
                        v-if="isPendingPlanEntry(entry)"
                        :confirm-disabled="streaming || !confirmationPlanVersion"
                        :label="planConfirmationLabel"
                        @confirm="confirmPlan"
                      />
                    </div>
                    <details v-else class="group">
                      <summary class="flex cursor-pointer list-none items-center justify-between gap-3 text-sm font-semibold text-slate-700">
                        <span>历史计划 v{{ planVersionOf(entry) || '?' }}</span>
                        <span class="text-xs font-medium text-slate-400 group-open:hidden">展开</span>
                        <span class="hidden text-xs font-medium text-slate-400 group-open:inline">收起</span>
                      </summary>
                      <div class="mt-3 border-t border-slate-200 pt-3 text-slate-600">
                        <AiSearchMarkdown :content="entry.content" />
                      </div>
                    </details>
                  </template>

                  <template v-else-if="entry.role === 'assistant'">
                    <AiSearchMarkdown :content="entry.content" />
                  </template>

                  <p v-else class="whitespace-pre-wrap break-words">{{ entry.content }}</p>
                </div>
              </article>
            </template>
          </div>
        </div>

        <div v-if="showExecutionPanel" class="border-t border-slate-200">
          <button type="button" class="accordion-toggle" @click="executionPanelOpen = !executionPanelOpen">
            <span class="accordion-title">
              执行进度
              <span class="accordion-meta">{{ completedExecutionTodoCount }}/{{ executionTodos.length }} 完成</span>
            </span>
            <ChevronDownIcon class="accordion-icon" :class="{ 'rotate-180': executionPanelOpen }" />
          </button>
          <div v-if="executionPanelOpen" class="accordion-body space-y-3">
            <div v-if="!executionTodos.length" class="rounded-2xl border border-dashed border-slate-200 px-3 py-6 text-center text-sm text-slate-500">
              计划确认后会在这里显示执行任务拆解和实时状态。
            </div>
            <div v-else class="rounded-2xl border border-slate-200 bg-white">
              <div class="flex items-center justify-between border-b border-slate-100 px-3 py-2 text-[11px] text-slate-500">
                <span>{{ completedExecutionTodoCount }} / {{ executionTodos.length }} 项已完成</span>
                <span v-if="activeExecutionTodoTitle" class="truncate text-right text-cyan-700">当前：{{ activeExecutionTodoTitle }}</span>
              </div>
              <ol class="divide-y divide-slate-100">
                <li
                v-for="todo in executionTodos"
                :key="todo.todo_id || todo.title"
                class="px-3 py-2"
              >
                <div class="flex items-center gap-2.5">
                  <span class="inline-flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded-full border text-[10px]" :class="todoCheckClass(todo.status)">
                    <CheckIcon v-if="todo.status === 'completed'" class="h-3.5 w-3.5" />
                    <XMarkIcon v-else-if="todo.status === 'failed'" class="h-3.5 w-3.5" />
                    <span v-else-if="todo.status === 'paused'" class="h-1.5 w-1.5 rounded-full bg-amber-600" />
                    <span v-else-if="todo.status === 'in_progress'" class="h-2 w-2 rounded-full bg-cyan-600" />
                    <span v-else class="h-2 w-2 rounded-full bg-slate-300" />
                  </span>
                  <p class="min-w-0 flex-1 truncate text-[13px] text-slate-800" :class="{ 'line-through text-slate-400': todo.status === 'completed' }">
                    {{ todo.title || '未命名任务' }}
                  </p>
                </div>
              </li>
              </ol>
            </div>

            <div v-if="activeSubagentList.length" class="rounded-2xl border border-cyan-200 bg-cyan-50/70 px-3 py-3">
              <p class="text-xs font-semibold text-cyan-900">当前执行中</p>
              <div class="mt-2 space-y-2">
                <div
                  v-for="item in activeSubagentList"
                  :key="item.name"
                  class="flex items-center justify-between gap-3 rounded-xl border border-white/80 bg-white/80 px-3 py-2 text-[12px] text-slate-700"
                >
                  <span class="font-medium text-slate-900">{{ item.label }}</span>
                  <span class="text-slate-500">{{ item.statusText }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="border-t border-slate-200 px-4 py-4">
          <AiSearchQuestionCard
            v-if="pendingQuestion"
            v-model="answerDraft"
            :prompt="questionPrompt"
            :reason="questionReason"
            :answer-shape="questionAnswerShape"
            :disabled="streaming || !answerDraft.trim()"
            @submit="submitAnswer"
          />

          <section v-else-if="resumeAction?.available" class="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-4">
            <div class="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div class="min-w-0">
                <p class="text-sm font-semibold text-amber-900">
                  {{ resumeTaskTitle || '当前执行步骤' }}失败，需要显式恢复
                </p>
                <p class="mt-1 text-xs leading-5 text-amber-800">
                  系统不会把恢复当成普通聊天消息。点击下方按钮后，会从当前失败步骤继续执行。
                </p>
                <p v-if="resumeLastError" class="mt-2 rounded-xl border border-amber-200 bg-white/70 px-3 py-2 text-xs leading-5 text-amber-900">
                  上次错误：{{ resumeLastError }}
                </p>
                <p v-if="resumeAttemptCount > 0" class="mt-2 text-[11px] text-amber-700">
                  已尝试 {{ resumeAttemptCount }} 次
                </p>
              </div>
              <button
                type="button"
                class="shrink-0 rounded-xl bg-amber-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:bg-slate-300"
                :disabled="streaming"
                @click="resumeExecution"
              >
                恢复执行
              </button>
            </div>
          </section>

          <section v-else>
            <div class="relative">
              <textarea
                v-model="composer"
                rows="2"
                class="min-h-[5.25rem] max-h-[9.5rem] w-full resize-none overflow-y-auto rounded-2xl border border-slate-200 bg-white px-4 py-3 pb-8 pr-16 text-sm text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100 disabled:cursor-not-allowed disabled:bg-slate-50"
                :disabled="inputDisabled || !currentSession"
                :placeholder="inputPlaceholder"
                @keydown.enter.exact.prevent="onComposerEnter"
                @keydown.meta.enter.prevent="submitMessage"
                @keydown.ctrl.enter.prevent="submitMessage"
              />
              <span class="pointer-events-none absolute bottom-3 left-4 text-[11px] text-slate-400">
                Enter 发送，Shift+Enter 换行
              </span>
              <button
                type="button"
                class="absolute bottom-3 right-3 inline-flex h-10 w-10 items-center justify-center rounded-full bg-slate-900 text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                aria-label="发送消息"
                :disabled="!canSubmitMessage"
                @click="submitMessage"
              >
                <ArrowUpIcon class="h-4 w-4" />
              </button>
            </div>
            <p
              v-if="['execute_search', 'coarse_screen', 'close_read', 'generate_feature_table'].includes(currentSession?.phase || '')"
              class="mt-2 text-xs text-slate-500"
            >
              当前轮检索执行中，执行完成后可继续调整计划。
            </p>
          </section>
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
          <div v-else class="space-y-3">
            <section
              v-for="group in groupedSessions"
              :key="group.key"
              class="space-y-2"
            >
              <p class="px-1 text-[11px] font-semibold tracking-[0.18em] text-slate-400">{{ group.label }}</p>
              <AiSearchSessionListItem
                v-for="session in group.items"
                :key="session.sessionId"
                :session="session"
                :active="session.sessionId === currentSession?.session.sessionId"
                :busy="isSessionMutating(session.sessionId)"
                @select="selectSession"
                @rename="renameSession"
                @toggle-pin="toggleSessionPin"
                @delete="deleteSession"
              />
            </section>
          </div>
        </div>
      </aside>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ArrowUpIcon, Bars3Icon, CheckIcon, ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon, PencilSquareIcon, PlusIcon, XMarkIcon } from '@heroicons/vue/24/outline'
import { storeToRefs } from 'pinia'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AiSearchMarkdown from '~/components/ai-search/AiSearchMarkdown.vue'
import AiSearchPlanConfirmationCard from '~/components/ai-search/AiSearchPlanConfirmationCard.vue'
import AiSearchQuestionCard from '~/components/ai-search/AiSearchQuestionCard.vue'
import AiSearchSessionListItem from '~/components/ai-search/AiSearchSessionListItem.vue'
import { useAdminUsageStore } from '~/stores/adminUsage'
import { useAiSearchStore } from '~/stores/aiSearch'
import { useAuthStore } from '~/stores/auth'
import type { AiSearchSessionSummary } from '~/types/aiSearch'

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
const { showMessage } = useGlobalMessage()
const route = useRoute()
const router = useRouter()
const {
  activeSubagentStatuses,
  currentSession,
  error,
  loading,
  pendingAssistantMessage,
  phaseMarkers,
  sessions,
  streaming,
} = storeToRefs(aiSearchStore)

const composer = ref('')
const answerDraft = ref('')
const headerTitleDraft = ref('')
const headerTitleInputRef = ref<HTMLInputElement | null>(null)
const headerEditing = ref(false)
const messageListRef = ref<HTMLElement | null>(null)
const sidebarCollapsed = ref(false)
const mobileDrawerOpen = ref(false)
const executionPanelOpen = ref(true)

const messages = computed(() => currentSession.value?.messages || [])
const pendingQuestion = computed<Record<string, any> | null>(() => currentSession.value?.pendingQuestion || null)
const pendingConfirmation = computed<Record<string, any> | null>(() => currentSession.value?.pendingConfirmation || null)
const resumeAction = computed<Record<string, any> | null>(() => currentSession.value?.resumeAction || null)
const executionTodos = computed<Array<Record<string, any>>>(() => currentSession.value?.executionTodos || [])
const completedExecutionTodoCount = computed(() => executionTodos.value.filter((todo) => todo.status === 'completed').length)
const activeExecutionTodoTitle = computed(() => {
  const inProgress = executionTodos.value.find((todo) => todo.status === 'in_progress')
  const failed = executionTodos.value.find((todo) => todo.status === 'failed')
  const paused = executionTodos.value.find((todo) => todo.status === 'paused')
  return String(inProgress?.title || failed?.title || paused?.title || '').trim()
})

const activePlanVersion = computed(() => {
  const candidate = pendingConfirmation.value?.planVersion
    || currentSession.value?.currentPlan?.planVersion
    || currentSession.value?.session.activePlanVersion
  const value = Number(candidate || 0)
  return Number.isFinite(value) && value > 0 ? value : 0
})

const confirmationPlanVersion = computed(() => Number(pendingConfirmation.value?.planVersion || activePlanVersion.value || 0))
const planConfirmationLabel = computed(() => String(pendingConfirmation.value?.confirmationLabel || '实施此计划').trim())
const activePhaseLabel = computed(() => phaseLabel(currentSession.value?.phase || 'collecting_requirements'))
const inputDisabled = computed(() => aiSearchStore.inputDisabled || !currentSession.value)
const canSubmitMessage = computed(() => !!composer.value.trim() && !inputDisabled.value)
const resumeTaskTitle = computed(() => String(resumeAction.value?.taskTitle || '').trim())
const resumeLastError = computed(() => String(resumeAction.value?.lastError || '').trim())
const resumeAttemptCount = computed(() => Number(resumeAction.value?.attemptCount || 0))
const questionPrompt = computed(() => String(pendingQuestion.value?.prompt || '').trim())
const questionReason = computed(() => String(pendingQuestion.value?.reason || '').trim())
const questionAnswerShape = computed(() => String(pendingQuestion.value?.expected_answer_shape || '').trim())
const workspaceTitle = computed(() => String(currentSession.value?.session.title || 'AI 检索工作台'))
const activeSubagentList = computed(() => Object.values(activeSubagentStatuses.value || {}).filter((item) => item.name !== 'plan-prober'))
const showExecutionPanel = computed(() => (
  executionTodos.value.length > 0
  || activeSubagentList.value.length > 0
  || ['execute_search', 'coarse_screen', 'close_read', 'generate_feature_table', 'completed', 'failed'].includes(currentSession.value?.phase || '')
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
const canSubmitHeaderRename = computed(() => {
  const nextTitle = headerTitleDraft.value.trim()
  const currentTitle = String(currentSession.value?.session.title || '').trim()
  return !!currentSession.value && !!nextTitle && nextTitle !== currentTitle
})

const inputPlaceholder = computed(() => {
  if (!currentSession.value) return '正在准备会话...'
  if (resumeAction.value?.available) return '当前失败步骤需要先恢复执行。'
  if (['execute_search', 'coarse_screen', 'close_read', 'generate_feature_table'].includes(currentSession.value.phase)) {
    return '检索执行中，请稍后再补充消息。'
  }
  return '继续修改检索计划，例如调整检索要素、检索顺序、中文/英文策略或 IPC/CPC 使用方式。'
})

const onComposerEnter = (event: KeyboardEvent) => {
  if (event.isComposing || inputDisabled.value || !currentSession.value || !composer.value.trim()) return
  submitMessage()
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

const conversationEntries = computed<Array<Record<string, any>>>(() => {
  const entries: Array<Record<string, any>> = []
  messages.value.forEach((message, index) => {
    entries.push({
      id: message.message_id || `message-${index}`,
      entryType: 'message',
      sortKey: toMillis(message.created_at),
      order: index,
      ...message,
    })
  })
  phaseMarkers.value.forEach((marker, index) => {
    entries.push({
      id: marker.id,
      entryType: 'phase',
      phase: marker.phase,
      sortKey: toMillis(marker.createdAt),
      order: 1000 + index,
    })
  })
  if (pendingAssistantMessage.value) {
    entries.push({
      id: `pending-${pendingAssistantMessage.value.messageId}`,
      entryType: 'pending-assistant',
      role: 'assistant',
      content: pendingAssistantMessage.value.content,
      createdAt: pendingAssistantMessage.value.createdAt,
      sortKey: toMillis(pendingAssistantMessage.value.createdAt),
      order: 2000,
    })
  }
  return entries.sort((left, right) => {
    if (left.sortKey !== right.sortKey) return left.sortKey - right.sortKey
    return left.order - right.order
  })
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

const phaseLabel = (phase: string): string => {
  const map: Record<string, string> = {
    collecting_requirements: '整理需求',
    awaiting_user_answer: '等待回答',
    drafting_plan: '起草计划',
    awaiting_plan_confirmation: '待确认',
    execute_search: '执行检索',
    coarse_screen: '粗筛候选文献',
    close_read: '精读并提取证据',
    generate_feature_table: '生成特征对比表',
    completed: '已完成',
    failed: '失败',
    cancelled: '已终止',
  }
  return map[phase] || phase || '未知阶段'
}

const todoStatusLabel = (status?: string): string => {
  if (status === 'in_progress') return '进行中'
  if (status === 'completed') return '已完成'
  if (status === 'failed') return '失败'
  if (status === 'paused') return '已挂起'
  return '待执行'
}

const todoStatusClass = (status?: string): string => {
  if (status === 'in_progress') return 'border border-cyan-200 bg-cyan-50 text-cyan-700'
  if (status === 'completed') return 'border border-emerald-200 bg-emerald-50 text-emerald-700'
  if (status === 'failed') return 'border border-rose-200 bg-rose-50 text-rose-700'
  if (status === 'paused') return 'border border-amber-200 bg-amber-50 text-amber-700'
  return 'border border-slate-200 bg-slate-50 text-slate-600'
}

const todoCheckClass = (status?: string): string => {
  if (status === 'completed') return 'border-emerald-200 bg-emerald-50 text-emerald-700'
  if (status === 'failed') return 'border-rose-200 bg-rose-50 text-rose-700'
  if (status === 'paused') return 'border-amber-200 bg-amber-50 text-amber-700'
  if (status === 'in_progress') return 'border-cyan-200 bg-cyan-50 text-cyan-700'
  return 'border-slate-200 bg-slate-50 text-slate-400'
}

const isPlanMessage = (entry: Record<string, any>): boolean => String(entry.kind || '').trim() === 'plan_confirmation'

const planVersionOf = (entry: Record<string, any>): number => {
  const value = Number(entry.plan_version || entry.planVersion || entry.metadata?.plan_version || 0)
  return Number.isFinite(value) ? value : 0
}

const isLatestPlanMessage = (entry: Record<string, any>): boolean => {
  return isPlanMessage(entry) && planVersionOf(entry) === activePlanVersion.value
}

const isPendingPlanEntry = (entry: Record<string, any>): boolean => {
  return isPlanMessage(entry) && !!pendingConfirmation.value && planVersionOf(entry) === confirmationPlanVersion.value
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

const scrollMessagesToBottom = async () => {
  await nextTick()
  const el = messageListRef.value
  if (!el) return
  el.scrollTop = el.scrollHeight
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

const submitMessage = async () => {
  const content = composer.value.trim()
  if (!content) return
  if (pendingQuestion.value) return
  if (resumeAction.value?.available) return
  if (!currentSession.value) return
  composer.value = ''
  await aiSearchStore.sendMessage(content)
}

const submitAnswer = async () => {
  const answer = answerDraft.value.trim()
  const questionId = String(pendingQuestion.value?.question_id || pendingQuestion.value?.questionId || '').trim()
  if (!answer || !questionId) return
  answerDraft.value = ''
  await aiSearchStore.answerQuestion(questionId, answer)
}

const confirmPlan = async () => {
  if (!confirmationPlanVersion.value) return
  await aiSearchStore.confirmPlan(confirmationPlanVersion.value)
}

const resumeExecution = async () => {
  if (!resumeAction.value?.available) return
  await aiSearchStore.resumeExecution()
}

watch(
  () => currentSession.value?.session.sessionId || '',
  (sessionId) => {
    composer.value = ''
    answerDraft.value = ''
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
  () => currentSession.value?.phase || '',
  (phase) => {
    if (['execute_search', 'coarse_screen', 'close_read', 'generate_feature_table'].includes(phase)) {
      executionPanelOpen.value = true
    }
  },
)

watch(
  () => [conversationEntries.value.length, currentSession.value?.session.sessionId || ''].join(':'),
  () => {
    scrollMessagesToBottom()
  },
)

watch(
  () => pendingQuestion.value?.question_id || pendingQuestion.value?.questionId || '',
  () => {
    answerDraft.value = ''
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
  await scrollMessagesToBottom()
})
</script>

<style scoped>
.accordion-toggle {
  @apply flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left transition hover:bg-slate-50;
}

.accordion-title {
  @apply flex items-center gap-2 text-sm font-semibold text-slate-900;
}

.accordion-meta {
  @apply rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-500;
}

.accordion-icon {
  @apply h-4 w-4 flex-shrink-0 text-slate-400 transition-transform;
}

.accordion-body {
  @apply border-t border-slate-200 px-4 py-3;
}

@media (max-width: 1023px) {
  .accordion-toggle {
    @apply px-3 py-2.5;
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
