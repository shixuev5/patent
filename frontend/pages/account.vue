<template>
  <div class="space-y-4">
    <section class="rounded-3xl border border-slate-200 bg-white/92 p-4 shadow-sm shadow-slate-200 sm:p-5">
      <div class="account-top-row">
        <div class="account-profile-col">
          <button
            type="button"
            class="avatar-trigger"
            :class="{ 'is-uploading': savingAvatar }"
            :disabled="savingAvatar || editingDisplayName"
            :aria-label="savingAvatar ? '头像上传中' : '更新头像'"
            @click="triggerAvatarPicker"
          >
            <img
              v-if="profile?.picture"
              :src="profile.picture"
              alt="avatar"
              class="avatar-image"
            />
            <div
              v-else
              class="avatar-fallback"
            >
              {{ avatarText }}
            </div>
            <span v-if="!savingAvatar" class="avatar-edit-badge" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 20h9" stroke-linecap="round" />
                <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4z" stroke-linejoin="round" />
              </svg>
            </span>
            <span v-if="savingAvatar" class="avatar-uploading-mask" aria-hidden="true">
              <span class="avatar-uploading-spinner" />
              上传中
            </span>
          </button>
          <input
            ref="profileAvatarFileInput"
            type="file"
            accept="image/*"
            class="sr-only"
            @change="onProfileAvatarChange"
          />

          <div class="min-w-0 pt-1">
            <p class="text-xs font-semibold tracking-[0.08em] text-slate-500">Personal Space</p>
            <div class="name-display-wrap">
              <h1
                v-if="!editingDisplayName"
                class="profile-name-heading text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl"
              >
                {{ displayName }}
              </h1>
              <h1
                v-else
                ref="displayNameEditorRef"
                class="profile-name-heading profile-name-heading-editing text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl"
                contenteditable="plaintext-only"
                spellcheck="false"
                @blur="onDisplayNameBlur"
                @keydown="onDisplayNameEditorKeydown"
              />
              <button
                type="button"
                class="name-edit-trigger"
                :disabled="savingDisplayName || savingAvatar || editingDisplayName"
                aria-label="编辑名称"
                @click="startDisplayNameEdit"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M12 20h9" stroke-linecap="round" />
                  <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4z" stroke-linejoin="round" />
                </svg>
              </button>
            </div>
            <div class="profile-meta-row">
              <p class="text-xs text-slate-600">{{ displayAuthType }}</p>
              <button
                v-if="hasAuthingEnabled && authStore.isLoggedIn"
                type="button"
                class="account-link-btn"
                @click="openPasswordReset"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
                  <rect x="5" y="11" width="14" height="9" rx="2" />
                  <path d="M8 11V8a4 4 0 1 1 8 0v3" stroke-linecap="round" />
                </svg>
                <span>重置密码</span>
              </button>
            </div>
            <p v-if="profileSaveErrorMessage" class="profile-inline-error">{{ profileSaveErrorMessage }}</p>
          </div>
        </div>

        <div class="account-controls">
          <div class="month-switcher">
            <button
              type="button"
              class="month-switcher-btn"
              @click="shiftMonth(-1)"
            >
              上月
            </button>
            <span class="month-switcher-label">{{ monthDisplayLabel }}</span>
            <button
              type="button"
              class="month-switcher-btn"
              :disabled="!canMoveNextMonth"
              @click="shiftMonth(1)"
            >
              下月
            </button>
          </div>

          <div class="target-panel">
            <p class="target-label">本周期目标</p>
            <div class="target-actions">
              <input
                v-model="monthTargetInput"
                type="number"
                min="0"
                :disabled="!isCurrentMonthSelection || savingTarget"
                class="target-input"
              />
              <button
                type="button"
                class="target-save-btn"
                :disabled="!isCurrentMonthSelection || targetInputInvalid || savingTarget"
                @click="saveMonthTarget"
              >
                {{ savingTarget ? '保存中...' : '保存目标' }}
              </button>
            </div>
          </div>
          <p class="target-hint">{{ targetHintText }}</p>
          <p v-if="targetErrorMessage" class="target-error">{{ targetErrorMessage }}</p>
        </div>
      </div>

      <div v-if="dailyUsage" class="account-usage-row">
        <div class="usage-progress-panel">
          <div class="usage-bar-wrap">
            <div class="usage-bar-track">
              <div
                class="usage-bar-fill transition-all duration-300"
                :class="usageHasLimit ? 'bg-cyan-600' : 'bg-slate-300'"
                :style="{ width: `${usageProgressPercent}%` }"
              />
              <div class="usage-progress-label">
                <span>{{ usageProgressCenterLabel }}</span>
              </div>
            </div>
          </div>
          <p class="mt-1 text-[11px] whitespace-nowrap overflow-hidden text-ellipsis" :class="usageInfoToneClass">{{ usageInfoLine }}</p>

          <button
            v-if="showUsageLoginPrompt"
            type="button"
            class="mt-2 rounded-xl bg-cyan-700 px-2.5 py-1.5 text-[11px] font-semibold text-white transition hover:bg-cyan-800"
            @click="openUsageLogin"
          >
            登录/注册获取更多积分
          </button>
        </div>
      </div>
    </section>

    <section class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
      <AccountTabs
        :active-tab="activeTab"
        :has-notification-tab="hasNotificationTab"
        :has-wechat-tab="hasWechatTab"
        @update:active-tab="activeTab = $event"
      />

      <div v-if="errorMessage" class="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
        {{ errorMessage }}
      </div>

      <AccountOverviewTab
        v-else-if="activeTab === 'overview'"
        class="mt-4"
        :dashboard="dashboard"
        :refreshing="refreshing"
        :delta-tone-class="deltaToneClass"
        :actual-progress-label="actualProgressLabel"
        :expected-progress-label="expectedProgressLabel"
        :month-total-created="monthTotalCreated"
        :month-target="monthTarget"
        :progress-delta-label="progressDeltaLabel"
        :dashboard-title="dashboardTitle"
        :smart-summary="smartSummary"
        :weekly-breakdown="weeklyBreakdown"
      />

      <AccountNotificationTab
        v-else-if="activeTab === 'notifications'"
        :notification-email-enabled="notificationEmailEnabledInput"
        :work-notification-email="workNotificationEmailInput"
        :personal-notification-email="personalNotificationEmailInput"
        :work-notification-email-invalid="workNotificationEmailInvalid"
        :personal-notification-email-invalid="personalNotificationEmailInvalid"
        :saving-notification-settings="savingNotificationSettings"
        :notification-form-invalid="notificationFormInvalid"
        :notification-settings-error-message="notificationSettingsErrorMessage"
        @update:notification-email-enabled="notificationEmailEnabledInput = $event"
        @update:work-notification-email="workNotificationEmailInput = $event"
        @update:personal-notification-email="personalNotificationEmailInput = $event"
        @save="saveNotificationSettings"
      />

      <AccountWechatIntegrationTab
        v-else
        :binding-status="wechatIntegration?.bindingStatus || 'unbound'"
        :binding="wechatIntegration?.binding || null"
        :login-session="wechatIntegration?.loginSession || null"
        :push-task-completed="pushTaskCompletedInput"
        :push-task-failed="pushTaskFailedInput"
        :push-ai-search-pending-action="pushAiSearchPendingActionInput"
        :saving-settings="savingWechatSettings"
        :starting-login-session="startingWechatLoginSession"
        :disconnecting="disconnectingWechat"
        :error-message="wechatIntegrationErrorMessage"
        @update:push-task-completed="pushTaskCompletedInput = $event"
        @update:push-task-failed="pushTaskFailedInput = $event"
        @update:push-ai-search-pending-action="pushAiSearchPendingActionInput = $event"
        @save-settings="saveWechatSettings"
        @start-login-session="startWechatLoginSession"
        @disconnect="disconnectWechat"
      />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import AccountTabs from '~/components/account/AccountTabs.vue'
import AccountNotificationTab from '~/components/account/AccountNotificationTab.vue'
import AccountOverviewTab from '~/components/account/AccountOverviewTab.vue'
import AccountWechatIntegrationTab from '~/components/account/AccountWechatIntegrationTab.vue'
import { useAuthStore } from '~/stores/auth'
import { useTaskStore } from '~/stores/task'
import { cachedGetJson, invalidateQueries, requestRaw, setCachedQueryData } from '~/utils/apiClient'
import type {
  AccountAvatarUploadResponse,
  AccountDashboard,
  AccountNotificationSettings,
  AccountNotificationSettingsUpdateRequest,
  AccountProfile,
  AccountProfileUpdateRequest,
  AccountWeChatIntegration,
  AccountWeChatIntegrationUpdateRequest,
  WeeklyActivityPoint,
} from '~/types/account'
import type { UsageResponse } from '~/types/usage'

const config = useRuntimeConfig()
const authStore = useAuthStore()
const taskStore = useTaskStore()
const usageNowTs = ref(Date.now())
let usageCountdownTimer: ReturnType<typeof setInterval> | null = null
let wechatLoginPollTimer: ReturnType<typeof setInterval> | null = null
const EMAIL_PATTERN = /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}$/i

const now = new Date()
const selectedMonth = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)
const activeTab = ref<'overview' | 'notifications' | 'wechat'>('overview')
const loading = ref(false)
const refreshing = ref(false)
const savingTarget = ref(false)
const savingDisplayName = ref(false)
const savingAvatar = ref(false)
const savingNotificationSettings = ref(false)
const savingWechatSettings = ref(false)
const startingWechatLoginSession = ref(false)
const disconnectingWechat = ref(false)
const pageReady = ref(false)
const errorMessage = ref('')
const targetErrorMessage = ref('')
const profileSaveErrorMessage = ref('')
const notificationSettingsErrorMessage = ref('')
const wechatIntegrationErrorMessage = ref('')
const monthTargetInput = ref('0')
const profile = ref<AccountProfile | null>(null)
const dashboard = ref<AccountDashboard | null>(null)
const wechatIntegration = ref<AccountWeChatIntegration | null>(null)
const editingDisplayName = ref(false)
const profileAvatarFileInput = ref<HTMLInputElement>()
const displayNameEditorRef = ref<HTMLElement>()
const displayNameDraft = ref('')
const discardDisplayNameOnBlur = ref(false)
const notificationEmailEnabledInput = ref(false)
const workNotificationEmailInput = ref('')
const personalNotificationEmailInput = ref('')
const pushTaskCompletedInput = ref(true)
const pushTaskFailedInput = ref(true)
const pushAiSearchPendingActionInput = ref(true)

const hasAuthingEnabled = computed(() => String(config.public.authingAppId || '').trim().length > 0)
const currentMonthKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`

const parsedYearMonth = computed(() => {
  const match = selectedMonth.value.match(/^(\d{4})-(\d{2})$/)
  if (!match) {
    return {
      year: now.getFullYear(),
      month: now.getMonth() + 1,
    }
  }

  const year = Number(match[1])
  const month = Number(match[2])
  if (!Number.isInteger(year) || !Number.isInteger(month) || month < 1 || month > 12) {
    return {
      year: now.getFullYear(),
      month: now.getMonth() + 1,
    }
  }

  return { year, month }
})

const monthDisplayLabel = computed(() => `${parsedYearMonth.value.year}年${parsedYearMonth.value.month}月`)
const canMoveNextMonth = computed(() => selectedMonth.value < currentMonthKey)
const isCurrentMonthSelection = computed(() => selectedMonth.value === currentMonthKey)
const hasNotificationTab = computed(() => profile.value?.authType === 'authing' || authStore.isLoggedIn)
const hasWechatTab = computed(() => profile.value?.authType === 'authing' || authStore.isLoggedIn)

const dailySeries = computed(() => dashboard.value?.dailySeries ?? [])
const weeklySeries = computed<WeeklyActivityPoint[]>(() => dashboard.value?.weeklySeries ?? [])
const periodStartDate = computed(() => {
  const raw = String(dashboard.value?.periodStart || '')
  return raw ? new Date(`${raw}T00:00:00`) : null
})
const periodEndDate = computed(() => {
  const raw = String(dashboard.value?.periodEnd || '')
  return raw ? new Date(`${raw}T00:00:00`) : null
})
const periodDays = computed(() => {
  if (!periodStartDate.value || !periodEndDate.value) return dailySeries.value.length
  const diff = periodEndDate.value.getTime() - periodStartDate.value.getTime()
  return Math.max(0, Math.floor(diff / 86400000) + 1)
})

const monthTotalCreated = computed(() => dailySeries.value.reduce((sum, item) => sum + item.totalCreated, 0))
const monthTarget = computed(() => Math.max(0, Number(dashboard.value?.monthTarget ?? 0)))
const monthTargetSource = computed(() => dashboard.value?.monthTargetSource ?? 'empty')
const dailyUsage = computed(() => taskStore.dailyUsage)
const usageHasLimit = computed(() => Number(dailyUsage.value?.dailyPointLimit || 0) > 0)
const usageRemainingLabel = computed(() => usageHasLimit.value
  ? `${taskStore.formatPointValue(dailyUsage.value?.remainingPoints || 0)} 点`
  : '--')
const usageProgressPercent = computed(() => {
  const used = Number(dailyUsage.value?.usedPoints || 0)
  const limit = Number(dailyUsage.value?.dailyPointLimit || 0)
  if (limit <= 0) return 0
  return Math.max(0, Math.min(100, Math.round((used / limit) * 100)))
})
const usageProgressCenterLabel = computed(() => {
  const used = taskStore.formatPointValue(dailyUsage.value?.usedPoints || 0)
  if (!usageHasLimit.value) return `${used} / --`
  const limit = taskStore.formatPointValue(dailyUsage.value?.dailyPointLimit || 0)
  return `${used} / ${limit}`
})
const usageInfoLine = computed(() => {
  if (!usageHasLimit.value) return '分析 1 点 / 审查 1 点 / 答复 2 点 · 每日上限未配置'
  if ((dailyUsage.value?.remainingPoints || 0) <= 0) return `分析 1 点 / 审查 1 点 / 答复 2 点 · 今日已用完 · 距重置 ${usageResetLabel.value}`
  return `分析 1 点 / 审查 1 点 / 答复 2 点 · 剩余 ${usageRemainingLabel.value} · 距重置 ${usageResetLabel.value}`
})
const usageInfoToneClass = computed(() => {
  if (!usageHasLimit.value || (dailyUsage.value?.remainingPoints || 0) <= 0) return 'text-amber-500'
  return 'text-slate-400'
})
const normalizedWorkNotificationEmail = computed(() => String(workNotificationEmailInput.value || '').trim())
const normalizedPersonalNotificationEmail = computed(() => String(personalNotificationEmailInput.value || '').trim())
const workNotificationEmailInvalid = computed(() => {
  if (!normalizedWorkNotificationEmail.value) return false
  return !EMAIL_PATTERN.test(normalizedWorkNotificationEmail.value)
})
const personalNotificationEmailInvalid = computed(() => {
  if (!normalizedPersonalNotificationEmail.value) return false
  return !EMAIL_PATTERN.test(normalizedPersonalNotificationEmail.value)
})
const notificationFormInvalid = computed(() => {
  if (!notificationEmailEnabledInput.value) {
    return workNotificationEmailInvalid.value || personalNotificationEmailInvalid.value
  }
  if (!normalizedWorkNotificationEmail.value && !normalizedPersonalNotificationEmail.value) return true
  return workNotificationEmailInvalid.value || personalNotificationEmailInvalid.value
})
const showUsageLoginPrompt = computed(() => {
  return !!dailyUsage.value
    && dailyUsage.value.authType === 'guest'
    && dailyUsage.value.remainingPoints <= 0
    && hasAuthingEnabled.value
    && !authStore.isLoggedIn
})
const formatRemainingTime = (ms: number): string => {
  const totalMinutes = Math.max(1, Math.ceil(ms / 60_000))
  const days = Math.floor(totalMinutes / (24 * 60))
  const hours = Math.floor((totalMinutes % (24 * 60)) / 60)
  const minutes = totalMinutes % 60

  if (days > 0) return hours > 0 ? `${days}天${hours}小时` : `${days}天`
  if (hours > 0) return minutes > 0 ? `${hours}小时${minutes}分钟` : `${hours}小时`
  return `${minutes}分钟`
}
const usageResetLabel = computed(() => {
  if (!dailyUsage.value?.resetAt) return '--'
  try {
    const resetDate = new Date(dailyUsage.value.resetAt)
    if (Number.isNaN(resetDate.getTime())) return '--'
    const remainingMs = resetDate.getTime() - usageNowTs.value
    if (remainingMs <= 0) return '即将重置'
    return formatRemainingTime(remainingMs)
  } catch (_error) {
    return '--'
  }
})

const elapsedRatio = computed(() => {
  if (!dashboard.value) return 0

  if (!periodStartDate.value || !periodEndDate.value) return 0
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  if (today < periodStartDate.value) return 0
  if (today >= periodEndDate.value) return 1
  const elapsedDays = Math.floor((today.getTime() - periodStartDate.value.getTime()) / 86400000) + 1
  return Math.min(1, Math.max(0, elapsedDays / Math.max(1, periodDays.value)))
})

const actualProgressPercent = computed(() => {
  if (!monthTarget.value) return 0
  return (monthTotalCreated.value / monthTarget.value) * 100
})

const expectedProgressPercent = computed(() => {
  if (!monthTarget.value) return 0
  return elapsedRatio.value * 100
})

const progressDeltaPercent = computed(() => actualProgressPercent.value - expectedProgressPercent.value)

const weekRanges = computed(() => {
  return [
    { label: '第1周', start: 1, end: Math.min(7, periodDays.value) },
    { label: '第2周', start: 8, end: Math.min(14, periodDays.value) },
    { label: '第3周', start: 15, end: Math.min(21, periodDays.value) },
    { label: '第4周', start: 22, end: periodDays.value },
  ]
})

const weekDayCounts = computed(() => {
  return weekRanges.value.map((item) => {
    if (item.start > periodDays.value || item.end < item.start) return 0
    return item.end - item.start + 1
  })
})

const weeklyTargets = computed(() => {
  if (monthTarget.value <= 0) return [0, 0, 0, 0]

  const totalDays = Math.max(1, periodDays.value)
  const rawTargets = weekDayCounts.value.map((days) => (monthTarget.value * days) / totalDays)
  const floorTargets = rawTargets.map((value) => Math.floor(value))
  let remainder = monthTarget.value - floorTargets.reduce((sum, item) => sum + item, 0)

  const fractions = rawTargets
    .map((value, index) => ({
      index,
      frac: value - Math.floor(value),
    }))
    .sort((a, b) => b.frac - a.frac)

  for (let i = 0; i < fractions.length && remainder > 0; i += 1) {
    floorTargets[fractions[i].index] += 1
    remainder -= 1
  }

  return floorTargets
})

const currentWeekIndex = computed(() => {
  if (!periodStartDate.value || !periodEndDate.value) return -1
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  if (today < periodStartDate.value) return -1
  if (today >= periodEndDate.value) return 3
  const dayOffset = Math.floor((today.getTime() - periodStartDate.value.getTime()) / 86400000)
  return Math.min(3, Math.floor(dayOffset / 7))
})

const weeklyBreakdown = computed(() => {
  const defaultWeeks = Array.from({ length: 4 }, (_item, index) => ({
    week: `第${index + 1}周`,
    analysisCreated: 0,
    totalCreated: 0,
  }))

  return defaultWeeks.map((fallback, index) => {
    const item = weeklySeries.value[index] || fallback
    const targetCount = weeklyTargets.value[index] ?? 0
    const percent = targetCount > 0 ? (item.totalCreated / targetCount) * 100 : 0
    return {
      ...item,
      targetCount,
      percent,
      progressBarWidth: Math.max(0, Math.min(100, percent)),
      reached: index <= currentWeekIndex.value,
    }
  })
})

const displayName = computed(() => {
  const candidates = [
    profile.value?.name,
    profile.value?.nickname,
    authStore.user?.name,
    authStore.user?.nickname,
    profile.value?.phone,
    authStore.user?.phone,
    profile.value?.email,
    authStore.user?.email,
  ]
  return candidates.find((item) => item && String(item).trim().length > 0) || '访客用户'
})

const avatarText = computed(() => String(displayName.value).trim().slice(0, 1).toUpperCase() || 'U')

const displayAuthType = computed(() => {
  if (profile.value?.authType === 'authing' || authStore.isLoggedIn) return '认证账号'
  return '匿名访客'
})

const openPasswordReset = async () => {
  await authStore.openPasswordReset()
}

const openUsageLogin = async () => {
  await authStore.login()
}

const dashboardTitle = computed(() => {
  if (!dashboard.value) return '结案周期趋势'
  return dashboard.value.periodLabel || `${dashboard.value.year} 年 ${dashboard.value.month} 月结案周期趋势`
})

const targetHintText = computed(() => {
  if (!isCurrentMonthSelection.value) return '当前查看历史结案月，目标仅可在当前结案月修改。'
  if (monthTargetSource.value === 'carried') return '本周期目标已自动沿用最近一次设置值，需要的话可直接改。'
  if (monthTargetSource.value === 'empty') return '当前结案周期还没有目标，先定一个 AI 分析目标更容易跟进节奏。'
  return '已设置本周期目标，后续进度和周卡片会按这个值实时更新。'
})

const actualProgressLabel = computed(() => formatPercent(actualProgressPercent.value))
const expectedProgressLabel = computed(() => formatPercent(expectedProgressPercent.value))

const progressDeltaLabel = computed(() => {
  const sign = progressDeltaPercent.value >= 0 ? '+' : ''
  return `${sign}${formatPercent(progressDeltaPercent.value)}`
})

const deltaToneClass = computed(() => {
  if (monthTarget.value <= 0) return ''
  if (progressDeltaPercent.value >= 8) return 'metric-card-good'
  if (progressDeltaPercent.value <= -8) return 'metric-card-warn'
  return ''
})

const smartSummary = computed(() => {
  if (monthTarget.value <= 0) {
    return '当前结案周期还没立下目标，先定一个你觉得刚好有挑战的 AI 分析数量。'
  }
  const expectedCount = Math.round((monthTarget.value * elapsedRatio.value) || 0)
  const deltaCount = monthTotalCreated.value - expectedCount
  const absDeltaCount = Math.abs(deltaCount)
  const deltaPrefix = deltaCount >= 0 ? '领先' : '落后'
  if (progressDeltaPercent.value >= 8) {
    return `当前结案周期推进得很稳，AI 分析数量已比节奏${deltaPrefix} ${absDeltaCount} 个。`
  }
  if (progressDeltaPercent.value <= -8) {
    return `当前 AI 分析节奏稍慢，和计划相差 ${absDeltaCount} 个；优先推进临近完成案件会更有效。`
  }
  if (monthTargetSource.value === 'carried') {
    return '本周期沿用了之前的目标，目前进度基本贴着计划线，继续按现在的节奏推进即可。'
  }
  return '当前 AI 分析进度和目标基本同步，整体节奏比较平稳。'
})

const targetInputInvalid = computed(() => {
  const raw = String(monthTargetInput.value ?? '').trim()
  if (!raw) return true
  const value = Number(raw)
  return !Number.isInteger(value) || value < 0
})

const formatPercent = (value: number): string => `${Math.round(value)}%`

const shiftMonth = (delta: number) => {
  const { year, month } = parsedYearMonth.value
  const nextDate = new Date(year, month - 1 + delta, 1)
  const key = `${nextDate.getFullYear()}-${String(nextDate.getMonth() + 1).padStart(2, '0')}`
  if (delta > 0 && key > currentMonthKey) return
  selectedMonth.value = key
}

const getAuthToken = async (): Promise<string> => {
  const authed = await taskStore.ensureAuth()
  if (!authed || !taskStore.authToken) {
    throw new Error('认证失败，请稍后重试。')
  }
  return taskStore.authToken
}

const getAuthScopeKey = (): string => `${taskStore.authMode}:${taskStore.userId || 'anonymous'}`
const getAccountProfileQueryKey = (): readonly unknown[] => ['api', getAuthScopeKey(), 'account', 'profile']
const getAccountNotificationSettingsQueryKey = (): readonly unknown[] => ['api', getAuthScopeKey(), 'account', 'notification-settings']
const getAccountWechatIntegrationQueryKey = (): readonly unknown[] => ['api', getAuthScopeKey(), 'account', 'wechat-integration']
const getAccountDashboardQueryKey = (year: number, month: number): readonly unknown[] => [
  'api',
  getAuthScopeKey(),
  'account',
  'dashboard',
  year,
  month,
]
const getUsageQueryKey = (): readonly unknown[] => ['api', getAuthScopeKey(), 'usage', 'all']

const toApiError = async (response: Response): Promise<string> => {
  try {
    return await taskStore.parseApiError(response)
  } catch (_error) {
    return `请求失败（HTTP ${response.status}）`
  }
}

const clearAvatarFileInput = () => {
  if (profileAvatarFileInput.value) profileAvatarFileInput.value.value = ''
}

const isSupportedAvatarFile = (file: File): boolean => {
  const name = file.name.toLowerCase()
  const type = String(file.type || '').toLowerCase()
  const hasValidSuffix = ['.png', '.jpg', '.jpeg', '.webp', '.gif'].some((suffix) => name.endsWith(suffix))
  const hasValidType = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(type)
  return hasValidSuffix || hasValidType
}

const resetDisplayNameDraft = () => {
  displayNameDraft.value = String(profile.value?.name || displayName.value || '').trim()
}

const syncNotificationSettingsForm = (nextSettings: AccountNotificationSettings | null) => {
  notificationEmailEnabledInput.value = !!nextSettings?.notificationEmailEnabled
  workNotificationEmailInput.value = String(nextSettings?.workNotificationEmail || '')
  personalNotificationEmailInput.value = String(nextSettings?.personalNotificationEmail || '')
}

const syncWechatIntegrationForm = (nextIntegration: AccountWeChatIntegration | null) => {
  wechatIntegration.value = nextIntegration
  pushTaskCompletedInput.value = !!nextIntegration?.binding?.pushTaskCompleted
  pushTaskFailedInput.value = !!nextIntegration?.binding?.pushTaskFailed
  pushAiSearchPendingActionInput.value = !!nextIntegration?.binding?.pushAiSearchPendingAction
}

const stopWechatLoginPolling = () => {
  if (wechatLoginPollTimer) {
    clearInterval(wechatLoginPollTimer)
    wechatLoginPollTimer = null
  }
}

const syncDisplayNameEditor = () => {
  if (!displayNameEditorRef.value) return
  displayNameEditorRef.value.textContent = displayNameDraft.value || ' '
}

const focusDisplayNameEditor = () => {
  const editor = displayNameEditorRef.value
  if (!editor) return
  editor.focus()
  const selection = window.getSelection()
  if (!selection) return
  const range = document.createRange()
  range.selectNodeContents(editor)
  range.collapse(false)
  selection.removeAllRanges()
  selection.addRange(range)
}

const putProfile = async (token: string, payload: AccountProfileUpdateRequest): Promise<AccountProfile> => {
  const response = await requestRaw({
    baseUrl: config.public.apiBaseUrl,
    path: '/api/account/profile',
    method: 'PUT',
    token,
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(await toApiError(response))
  const nextProfile = await response.json() as AccountProfile
  profile.value = nextProfile
  setCachedQueryData(getAccountProfileQueryKey(), nextProfile)
  await invalidateQueries(['api', getAuthScopeKey(), 'account'])
  syncAuthUserProfile(nextProfile)
  return nextProfile
}

const triggerAvatarPicker = () => {
  if (savingAvatar.value || savingDisplayName.value) return
  profileSaveErrorMessage.value = ''
  profileAvatarFileInput.value?.click()
}

const onProfileAvatarChange = async (event: Event) => {
  const target = event.target as HTMLInputElement
  const selected = target.files?.[0]
  if (!selected) {
    return
  }
  if (!isSupportedAvatarFile(selected)) {
    profileSaveErrorMessage.value = '头像仅支持 PNG/JPG/JPEG/WEBP/GIF。'
    target.value = ''
    return
  }
  if (selected.size > 2 * 1024 * 1024) {
    profileSaveErrorMessage.value = '头像大小不能超过 2MB。'
    target.value = ''
    return
  }
  if (!profile.value) {
    target.value = ''
    profileSaveErrorMessage.value = '资料尚未加载完成，请稍后重试。'
    return
  }

  savingAvatar.value = true
  profileSaveErrorMessage.value = ''
  try {
    const token = await getAuthToken()
    const formData = new FormData()
    formData.append('file', selected)
    const uploadResponse = await requestRaw({
      baseUrl: config.public.apiBaseUrl,
      path: '/api/account/profile/avatar',
      method: 'POST',
      token,
      body: formData,
    })
    if (!uploadResponse.ok) throw new Error(await toApiError(uploadResponse))
    const uploaded = await uploadResponse.json() as AccountAvatarUploadResponse
    const currentName = String(profile.value?.name || '').trim()
    if (!currentName) throw new Error('请先设置显示名称后再更新头像。')
    await putProfile(token, {
      name: currentName,
      picture: uploaded.url,
    })
    resetDisplayNameDraft()
    taskStore.showGlobalNotice('success', '头像已更新。')
  } catch (error) {
    profileSaveErrorMessage.value = error instanceof Error ? error.message : '更新头像失败。'
  } finally {
    savingAvatar.value = false
    clearAvatarFileInput()
  }
}

const startDisplayNameEdit = () => {
  if (savingDisplayName.value || savingAvatar.value || editingDisplayName.value) return
  profileSaveErrorMessage.value = ''
  resetDisplayNameDraft()
  editingDisplayName.value = true
  nextTick(() => {
    syncDisplayNameEditor()
    focusDisplayNameEditor()
  })
}

const onDisplayNameEditorKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Enter') {
    event.preventDefault()
    ;(event.target as HTMLElement).blur()
    return
  }
  if (event.key === 'Escape') {
    event.preventDefault()
    discardDisplayNameOnBlur.value = true
    ;(event.target as HTMLElement).blur()
  }
}

const parseEditorDisplayName = (): string => {
  const raw = displayNameEditorRef.value?.textContent || ''
  return raw.replace(/\s+/g, ' ').trim()
}

const onDisplayNameBlur = async () => {
  if (!editingDisplayName.value) return
  if (discardDisplayNameOnBlur.value) {
    discardDisplayNameOnBlur.value = false
    editingDisplayName.value = false
    resetDisplayNameDraft()
    return
  }
  await saveDisplayName(parseEditorDisplayName())
}

const syncAuthUserProfile = (nextProfile: AccountProfile) => {
  if (!authStore.user) return
  authStore.user = {
    ...authStore.user,
    name: nextProfile.name,
    picture: nextProfile.picture,
  }
}

const fetchProfile = async (token: string) => {
  const nextProfile = await cachedGetJson<AccountProfile>({
    baseUrl: config.public.apiBaseUrl,
    path: '/api/account/profile',
    token,
    queryKey: getAccountProfileQueryKey(),
    staleTime: 30 * 1000,
    gcTime: 24 * 60 * 60 * 1000,
  })
  profile.value = nextProfile
  if (!editingDisplayName.value) resetDisplayNameDraft()
}

const fetchNotificationSettings = async (token: string) => {
  const nextSettings = await cachedGetJson<AccountNotificationSettings>({
    baseUrl: config.public.apiBaseUrl,
    path: '/api/account/notification-settings',
    token,
    queryKey: getAccountNotificationSettingsQueryKey(),
    staleTime: 30 * 1000,
    gcTime: 24 * 60 * 60 * 1000,
  })
  syncNotificationSettingsForm(nextSettings)
}

const fetchWechatIntegration = async (token: string) => {
  try {
    const nextIntegration = await cachedGetJson<AccountWeChatIntegration>({
      baseUrl: config.public.apiBaseUrl,
      path: '/api/account/wechat-integration',
      token,
      queryKey: getAccountWechatIntegrationQueryKey(),
      staleTime: 5 * 1000,
      gcTime: 24 * 60 * 60 * 1000,
    })
    wechatIntegrationErrorMessage.value = ''
    syncWechatIntegrationForm(nextIntegration)
  } catch (error) {
    syncWechatIntegrationForm(null)
    wechatIntegrationErrorMessage.value = error instanceof Error ? error.message : '加载微信接入状态失败。'
  }
}

const fetchDashboard = async (token: string) => {
  const { year, month } = parsedYearMonth.value
  const nextDashboard = await cachedGetJson<AccountDashboard>({
    baseUrl: config.public.apiBaseUrl,
    path: `/api/account/dashboard?year=${year}&month=${month}`,
    token,
    queryKey: getAccountDashboardQueryKey(year, month),
    staleTime: 20 * 1000,
    gcTime: 24 * 60 * 60 * 1000,
  })
  dashboard.value = nextDashboard
  monthTargetInput.value = String(Math.max(0, Number(dashboard.value.monthTarget || 0)))
}

const fetchUsage = async (token: string) => {
  try {
    const usage = await cachedGetJson<UsageResponse>({
      baseUrl: config.public.apiBaseUrl,
      path: '/api/usage',
      token,
      queryKey: getUsageQueryKey(),
      staleTime: 10 * 1000,
      gcTime: 30 * 60 * 1000,
    })
    taskStore.dailyUsage = usage
    if ((taskStore.dailyUsage?.remainingPoints || 0) > 0) taskStore.clearPointLimitNotice()
  } catch (error) {
    console.error('加载积分信息失败：', error)
  }
}

const saveWechatSettings = async () => {
  wechatIntegrationErrorMessage.value = ''
  if (!hasWechatTab.value || !wechatIntegration.value?.binding) return
  const payload: AccountWeChatIntegrationUpdateRequest = {
    pushTaskCompleted: pushTaskCompletedInput.value,
    pushTaskFailed: pushTaskFailedInput.value,
    pushAiSearchPendingAction: pushAiSearchPendingActionInput.value,
  }
  savingWechatSettings.value = true
  try {
    const token = await getAuthToken()
    const response = await requestRaw({
      baseUrl: config.public.apiBaseUrl,
      path: '/api/account/wechat-integration/settings',
      method: 'PUT',
      token,
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    })
    if (!response.ok) throw new Error(await toApiError(response))
    const nextIntegration = await response.json() as AccountWeChatIntegration
    syncWechatIntegrationForm(nextIntegration)
    setCachedQueryData(getAccountWechatIntegrationQueryKey(), nextIntegration)
    taskStore.showGlobalNotice('success', '微信推送设置已更新。')
  } catch (error) {
    wechatIntegrationErrorMessage.value = error instanceof Error ? error.message : '保存微信设置失败。'
  } finally {
    savingWechatSettings.value = false
  }
}

const startWechatLoginSession = async () => {
  wechatIntegrationErrorMessage.value = ''
  startingWechatLoginSession.value = true
  try {
    const token = await getAuthToken()
    const response = await requestRaw({
      baseUrl: config.public.apiBaseUrl,
      path: '/api/account/wechat-integration/login-session',
      method: 'POST',
      token,
    })
    if (!response.ok) throw new Error(await toApiError(response))
    const loginSession = await response.json() as AccountWeChatIntegration['loginSession']
    const nextIntegration: AccountWeChatIntegration = {
      bindingStatus: 'logging_in',
      binding: wechatIntegration.value?.binding || null,
      loginSession: loginSession || null,
    }
    syncWechatIntegrationForm(nextIntegration)
    setCachedQueryData(getAccountWechatIntegrationQueryKey(), nextIntegration)
  } catch (error) {
    wechatIntegrationErrorMessage.value = error instanceof Error ? error.message : '生成微信登录二维码失败。'
  } finally {
    startingWechatLoginSession.value = false
  }
}

const pollWechatLoginSession = async () => {
  const loginSessionId = String(wechatIntegration.value?.loginSession?.loginSessionId || '').trim()
  if (!loginSessionId) return
  try {
    const token = await getAuthToken()
    const response = await requestRaw({
      baseUrl: config.public.apiBaseUrl,
      path: `/api/account/wechat-integration/login-session/${loginSessionId}`,
      method: 'GET',
      token,
    })
    if (!response.ok) throw new Error(await toApiError(response))
    const nextLoginSession = await response.json() as AccountWeChatIntegration['loginSession']
    const nextIntegration: AccountWeChatIntegration = {
      bindingStatus: nextLoginSession?.status === 'online' ? 'bound' : 'logging_in',
      binding: wechatIntegration.value?.binding || null,
      loginSession: nextLoginSession || null,
    }
    syncWechatIntegrationForm(nextIntegration)
    const nextStatus = String(nextLoginSession?.status || '').trim()
    if (!['pending', 'qr_ready', 'scanned'].includes(nextStatus)) {
      stopWechatLoginPolling()
    }
    if (nextStatus === 'online') {
      await fetchWechatIntegration(token)
    }
  } catch (error) {
    wechatIntegrationErrorMessage.value = error instanceof Error ? error.message : '轮询微信登录状态失败。'
    stopWechatLoginPolling()
  }
}

const disconnectWechat = async () => {
  wechatIntegrationErrorMessage.value = ''
  disconnectingWechat.value = true
  try {
    const token = await getAuthToken()
    const response = await requestRaw({
      baseUrl: config.public.apiBaseUrl,
      path: '/api/account/wechat-integration/disconnect',
      method: 'POST',
      token,
    })
    if (!response.ok) throw new Error(await toApiError(response))
    const nextIntegration = await response.json() as AccountWeChatIntegration
    syncWechatIntegrationForm(nextIntegration)
    setCachedQueryData(getAccountWechatIntegrationQueryKey(), nextIntegration)
    stopWechatLoginPolling()
    taskStore.showGlobalNotice('success', '微信绑定已解除。')
  } catch (error) {
    wechatIntegrationErrorMessage.value = error instanceof Error ? error.message : '解绑微信失败。'
  } finally {
    disconnectingWechat.value = false
  }
}

const saveNotificationSettings = async () => {
  notificationSettingsErrorMessage.value = ''
  if (!hasNotificationTab.value) return

  const payload: AccountNotificationSettingsUpdateRequest = {
    notificationEmailEnabled: notificationEmailEnabledInput.value,
    workNotificationEmail: normalizedWorkNotificationEmail.value || null,
    personalNotificationEmail: normalizedPersonalNotificationEmail.value || null,
  }

  if (notificationEmailEnabledInput.value && !payload.workNotificationEmail && !payload.personalNotificationEmail) {
    notificationSettingsErrorMessage.value = '开启邮件通知后，工作邮箱和个人邮箱至少填写一个。'
    return
  }
  if (workNotificationEmailInvalid.value) {
    notificationSettingsErrorMessage.value = '工作邮箱格式无效，请检查后重试。'
    return
  }
  if (personalNotificationEmailInvalid.value) {
    notificationSettingsErrorMessage.value = '个人邮箱格式无效，请检查后重试。'
    return
  }

  savingNotificationSettings.value = true
  try {
    const token = await getAuthToken()
    const response = await requestRaw({
      baseUrl: config.public.apiBaseUrl,
      path: '/api/account/notification-settings',
      method: 'PUT',
      token,
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    })
    if (!response.ok) throw new Error(await toApiError(response))
    const nextSettings = await response.json() as AccountNotificationSettings
    syncNotificationSettingsForm(nextSettings)
    setCachedQueryData(getAccountNotificationSettingsQueryKey(), nextSettings)
    await invalidateQueries(['api', getAuthScopeKey(), 'account'])
    taskStore.showGlobalNotice('success', '邮件通知设置已更新。')
  } catch (error) {
    notificationSettingsErrorMessage.value = error instanceof Error ? error.message : '保存邮件通知设置失败。'
  } finally {
    savingNotificationSettings.value = false
  }
}

const saveMonthTarget = async () => {
  targetErrorMessage.value = ''
  if (!isCurrentMonthSelection.value) {
    targetErrorMessage.value = '仅支持设置当前月目标。'
    return
  }

  const nextTarget = Number(monthTargetInput.value)
  if (!Number.isInteger(nextTarget) || nextTarget < 0) {
    targetErrorMessage.value = '目标必须是大于等于 0 的整数。'
    return
  }

  savingTarget.value = true
  try {
    const token = await getAuthToken()
    const { year, month } = parsedYearMonth.value
    const response = await requestRaw({
      baseUrl: config.public.apiBaseUrl,
      path: '/api/account/month-target',
      method: 'PUT',
      token,
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        year,
        month,
        targetCount: nextTarget,
      }),
    })
    if (!response.ok) throw new Error(await toApiError(response))
    await invalidateQueries(['api', getAuthScopeKey(), 'account'])
    await invalidateQueries(['api', getAuthScopeKey(), 'usage'])
    await fetchDashboard(token)
  } catch (error) {
    targetErrorMessage.value = error instanceof Error ? error.message : '保存目标失败。'
  } finally {
    savingTarget.value = false
  }
}

const saveDisplayName = async (nextName: string) => {
  profileSaveErrorMessage.value = ''
  if (!profile.value) {
    profileSaveErrorMessage.value = '资料尚未加载完成，请稍后重试。'
    editingDisplayName.value = false
    return
  }
  if (nextName.length > 32) {
    profileSaveErrorMessage.value = '显示名称不能超过 32 个字符。'
    editingDisplayName.value = false
    resetDisplayNameDraft()
    return
  }
  if (!nextName) {
    profileSaveErrorMessage.value = '显示名称不能为空。'
    editingDisplayName.value = false
    resetDisplayNameDraft()
    return
  }
  const currentName = String(profile.value?.name || '').trim()
  if (nextName === currentName) {
    editingDisplayName.value = false
    resetDisplayNameDraft()
    return
  }

  savingDisplayName.value = true
  try {
    const token = await getAuthToken()
    await putProfile(token, {
      name: nextName,
      picture: profile.value?.picture || null,
    })
    resetDisplayNameDraft()
    taskStore.showGlobalNotice('success', '资料已更新。')
  } catch (error) {
    profileSaveErrorMessage.value = error instanceof Error ? error.message : '更新资料失败。'
  } finally {
    savingDisplayName.value = false
    editingDisplayName.value = false
  }
}

const loadData = async (loadProfile: boolean) => {
  if (loadProfile) loading.value = true
  else refreshing.value = true

  errorMessage.value = ''
  notificationSettingsErrorMessage.value = ''
  wechatIntegrationErrorMessage.value = ''

  try {
    const token = await getAuthToken()
    if (loadProfile || !profile.value) {
      await fetchProfile(token)
    }
    const jobs = [fetchDashboard(token), fetchUsage(token)]
    if (hasNotificationTab.value) {
      jobs.push(fetchNotificationSettings(token))
    } else {
      syncNotificationSettingsForm(null)
      if (activeTab.value === 'notifications') activeTab.value = 'overview'
    }
    if (hasWechatTab.value) {
      jobs.push(fetchWechatIntegration(token))
    } else {
      syncWechatIntegrationForm(null)
      stopWechatLoginPolling()
      if (activeTab.value === 'wechat') activeTab.value = 'overview'
    }
    await Promise.all(jobs)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '加载个人空间数据失败。'
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

watch(selectedMonth, async () => {
  targetErrorMessage.value = ''
  if (!pageReady.value) return
  await loadData(false)
})

watch(() => authStore.isLoggedIn, async () => {
  if (!pageReady.value) return
  await loadData(true)
})

watch(hasNotificationTab, (value) => {
  if (value) return
  activeTab.value = 'overview'
  syncNotificationSettingsForm(null)
})

watch(hasWechatTab, (value) => {
  if (value) return
  if (activeTab.value === 'wechat') activeTab.value = 'overview'
  syncWechatIntegrationForm(null)
  stopWechatLoginPolling()
})

watch(
  () => ({
    loginSessionId: String(wechatIntegration.value?.loginSession?.loginSessionId || '').trim(),
    status: String(wechatIntegration.value?.loginSession?.status || '').trim(),
  }),
  ({ loginSessionId, status }) => {
    stopWechatLoginPolling()
    if (!loginSessionId) return
    if (!['pending', 'qr_ready', 'scanned'].includes(status)) return
    wechatLoginPollTimer = setInterval(() => {
      void pollWechatLoginSession()
    }, 4000)
  },
)

onMounted(async () => {
  usageCountdownTimer = setInterval(() => {
    usageNowTs.value = Date.now()
  }, 30_000)
  pageReady.value = true
  await loadData(true)
})

onUnmounted(() => {
  if (usageCountdownTimer) {
    clearInterval(usageCountdownTimer)
    usageCountdownTimer = null
  }
  stopWechatLoginPolling()
})
</script>

<style scoped>
.account-top-row {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}

.account-profile-col {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  gap: 1rem;
}

.account-controls {
  display: flex;
  width: 100%;
  flex-direction: column;
  gap: 0.62rem;
}

.month-switcher {
  display: grid;
  width: 100%;
  grid-template-columns: 4.5rem 1fr 4.5rem;
  align-items: center;
  gap: 0.35rem;
  border-radius: 9999px;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  padding: 0.14rem 0.22rem;
}

.month-switcher-btn {
  min-height: 1.8rem;
  border-radius: 9999px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  font-size: 0.75rem;
  font-weight: 600;
  color: #475569;
  transition: all 0.18s ease;
}

.month-switcher-btn:hover:not(:disabled) {
  background: #f1f5f9;
}

.month-switcher-btn:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.month-switcher-label {
  text-align: center;
  font-size: 0.82rem;
  font-weight: 700;
  color: #334155;
}

.target-panel {
  width: 100%;
}

.target-label {
  margin: 0 0 0.42rem 0;
  font-size: 0.78rem;
  color: #64748b;
}

.target-actions {
  display: grid;
  width: 100%;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  gap: 0.45rem;
}

.target-input {
  width: 100%;
  min-height: 2.1rem;
  border: 1px solid #cbd5e1;
  border-radius: 0.55rem;
  background: #ffffff;
  padding: 0.25rem 0.65rem;
  font-size: 0.8rem;
  color: #0f172a;
}

.target-input:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.target-save-btn {
  width: 100%;
  min-height: 2.1rem;
  border-radius: 0.55rem;
  border: 1px solid #0891b2;
  background: #06b6d4;
  padding: 0.24rem 0.65rem;
  font-size: 0.8rem;
  font-weight: 600;
  color: #ecfeff;
  transition: all 0.18s ease;
}

.target-save-btn:hover:not(:disabled) {
  background: #0891b2;
}

.target-save-btn:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.target-hint,
.target-error {
  margin: 0;
  text-align: left;
  font-size: 0.75rem;
}

.target-hint {
  color: #64748b;
}

.target-error {
  color: #e11d48;
}

.usage-progress-panel {
  width: 100%;
  border: 0;
  border-radius: 0;
  background: transparent;
  padding: 0;
}

.usage-bar-wrap {
  width: 100%;
}

.usage-bar-track {
  position: relative;
  height: 1rem;
  overflow: visible;
  border-radius: 9999px;
  background: #e2e8f0;
}

.usage-bar-fill {
  height: 100%;
  border-radius: 9999px;
}

.usage-progress-label {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.usage-progress-label span {
  font-size: 0.62rem;
  line-height: 1;
  color: #334155;
  font-weight: 600;
}

.account-usage-row {
  margin-top: 0.95rem;
}

@media (min-width: 640px) {
  .account-controls {
    width: 100%;
    flex-direction: column;
    gap: 0.5rem;
  }

  .month-switcher {
    width: 100%;
    min-width: 14.6rem;
    grid-template-columns: 3.5rem minmax(6rem, 1fr) 3.5rem;
    gap: 0.25rem;
    padding: 0.12rem 0.18rem;
  }

  .month-switcher-btn {
    min-height: 1.62rem;
    font-size: 0.7rem;
  }

  .month-switcher-label {
    font-size: 0.78rem;
  }

  .target-panel {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 0.45rem;
  }

  .target-label {
    margin: 0;
    text-align: right;
    white-space: nowrap;
    font-size: 0.72rem;
  }

  .target-actions {
    width: 100%;
    grid-template-columns: 5.2rem auto;
    gap: 0.35rem;
  }

  .target-input,
  .target-save-btn {
    min-height: 1.68rem;
    font-size: 0.68rem;
    padding-top: 0.12rem;
    padding-bottom: 0.12rem;
  }

  .target-save-btn {
    min-width: 4.75rem;
    white-space: nowrap;
  }

  .target-hint,
  .target-error {
    text-align: left;
  }
}

@media (min-width: 1024px) {
  .account-top-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(20rem, 38rem);
    align-items: start;
    gap: 1rem;
  }

  .account-controls {
    align-items: flex-end;
  }

  .month-switcher {
    align-self: flex-end;
    width: auto;
  }

  .target-panel {
    align-self: flex-end;
    width: auto;
  }

  .target-actions {
    width: auto;
  }

  .target-hint,
  .target-error {
    align-self: flex-end;
    text-align: right;
  }
}

.avatar-trigger {
  position: relative;
  display: inline-flex;
  height: 6.25rem;
  width: 6.25rem;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  border-radius: 1.25rem;
  border: 1px solid #cbd5e1;
  background: #ffffff;
  transition: all 0.2s ease;
}

.avatar-trigger:hover:not(:disabled) {
  border-color: #67e8f9;
  box-shadow: 0 0 0 3px rgba(103, 232, 249, 0.28);
}

.avatar-trigger.is-uploading {
  border-color: #22d3ee;
  box-shadow: 0 0 0 3px rgba(34, 211, 238, 0.24);
}

.avatar-trigger:disabled {
  cursor: not-allowed;
  opacity: 0.72;
}

.avatar-image,
.avatar-fallback {
  height: 100%;
  width: 100%;
}

.avatar-image {
  object-fit: cover;
}

.avatar-fallback {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%);
  font-size: 2rem;
  font-weight: 700;
  color: #64748b;
}

.avatar-edit-badge {
  position: absolute;
  right: 0.4rem;
  bottom: 0.4rem;
  display: inline-flex;
  height: 1.45rem;
  width: 1.45rem;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
  border: 1px solid #0891b2;
  background: #06b6d4;
  color: #ecfeff;
  box-shadow: 0 2px 8px rgba(8, 145, 178, 0.3);
}

.avatar-edit-badge svg {
  height: 0.78rem;
  width: 0.78rem;
}

.avatar-uploading-mask {
  position: absolute;
  inset: 0;
  z-index: 3;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
  background: rgba(15, 23, 42, 0.42);
  color: #f8fafc;
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.avatar-uploading-spinner {
  height: 1.02rem;
  width: 1.02rem;
  border-radius: 9999px;
  border: 2px solid rgba(236, 254, 255, 0.45);
  border-top-color: #ecfeff;
  animation: avatar-spin 0.85s linear infinite;
}

@keyframes avatar-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.name-display-wrap {
  margin-top: 0.3rem;
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.profile-name-heading {
  margin: 0;
  min-height: 2.5rem;
  line-height: 1.15;
}

.profile-name-heading-editing {
  min-width: 8rem;
  outline: none;
  border-bottom: 1px dashed #94a3b8;
}

.name-edit-trigger {
  display: inline-flex;
  height: 1.05rem;
  width: 1.05rem;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  padding: 0;
  color: #94a3b8;
  transition: all 0.18s ease;
}

.name-edit-trigger svg {
  height: 0.9rem;
  width: 0.9rem;
}

.name-edit-trigger:hover:not(:disabled) {
  color: #64748b;
}

.name-edit-trigger:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.profile-meta-row {
  margin-top: 0.35rem;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
}

.profile-meta-row p {
  margin: 0;
}

.account-link-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  border: 0;
  background: transparent;
  padding: 0;
  font-size: 0.78rem;
  font-weight: 500;
  line-height: 1;
  color: #64748b;
  transition: color 0.18s ease;
}

.account-link-btn svg {
  height: 0.78rem;
  width: 0.78rem;
}

.account-link-btn:hover {
  color: #0284c7;
  text-decoration: underline;
  text-underline-offset: 2px;
}

.profile-inline-error {
  margin-top: 0.25rem;
  margin-bottom: 0;
  font-size: 0.75rem;
  color: #e11d48;
}

</style>
