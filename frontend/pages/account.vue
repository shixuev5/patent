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
                <span>设置/修改密码</span>
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
            <p class="target-label">本月目标</p>
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
      <div class="inline-flex rounded-2xl border border-slate-200 bg-slate-50 p-1">
        <button
          type="button"
          class="rounded-xl bg-white px-4 py-2 text-sm font-semibold text-slate-900 shadow-sm"
        >
          概览
        </button>
      </div>

      <div v-if="errorMessage" class="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
        {{ errorMessage }}
      </div>

      <div v-else class="mt-4 space-y-4">
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <article class="metric-card">
            <p class="metric-label">最近一个工作周</p>
            <p class="metric-value">{{ dashboard?.workWeek.totalCount ?? 0 }}</p>
            <p class="metric-desc">AI 分析 {{ dashboard?.workWeek.analysisCount ?? 0 }} · AI 研判 {{ dashboard?.workWeek.replyCount ?? 0 }}</p>
          </article>

          <article class="metric-card">
            <p class="metric-label">最近一个工作月</p>
            <p class="metric-value">{{ dashboard?.workMonth.totalCount ?? 0 }}</p>
            <p class="metric-desc">AI 分析 {{ dashboard?.workMonth.analysisCount ?? 0 }} · AI 研判 {{ dashboard?.workMonth.replyCount ?? 0 }}</p>
          </article>

          <article class="metric-card" :class="deltaToneClass">
            <p class="metric-label">当前进度</p>
            <p class="metric-value">{{ actualProgressLabel }}</p>
            <p class="metric-desc">{{ monthTotalCreated }} / {{ monthTarget }} 个（目标）</p>
          </article>

          <article class="metric-card">
            <p class="metric-label">预期进度</p>
            <p class="metric-value">{{ expectedProgressLabel }}</p>
            <p class="metric-desc">差异 {{ progressDeltaLabel }}</p>
          </article>
        </div>

        <article class="rounded-2xl border border-slate-200 bg-white p-4">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 class="text-base font-semibold text-slate-900">{{ dashboardTitle }}</h2>
              <p class="text-xs text-slate-500">横轴为自然日（仅显示第1-4周标签），纵轴为累计任务数</p>
            </div>
            <div class="flex items-center gap-2">
              <p
                v-if="refreshing"
                class="rounded-full border border-cyan-200 bg-cyan-50 px-2.5 py-1 text-xs text-cyan-700"
              >
                更新中...
              </p>
            </div>
          </div>

          <div class="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_16rem] lg:items-start">
            <div class="space-y-2">
              <div class="rounded-xl border border-slate-200 bg-cyan-50/70 px-3 py-2">
                <p class="m-0 text-sm text-slate-700">{{ smartSummary }}</p>
              </div>

              <div class="chart-wrap" @mouseleave="clearHoveredDay">
              <svg
                :key="chartRenderKey"
                :viewBox="`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`"
                preserveAspectRatio="xMidYMid meet"
                class="h-[22rem] w-full"
              >
                <g>
                  <line
                    v-for="tick in yTicks"
                    :key="`line-${tick.value}`"
                    :x1="PADDING.left"
                    :x2="CHART_WIDTH - PADDING.right"
                    :y1="tick.y"
                    :y2="tick.y"
                    stroke="#e2e8f0"
                    stroke-dasharray="2 3"
                  />

                  <line
                    v-for="line in weekSeparators"
                    :key="`week-sep-${line.label}`"
                    :x1="line.x"
                    :x2="line.x"
                    :y1="PADDING.top"
                    :y2="CHART_HEIGHT - PADDING.bottom"
                    stroke="#cbd5e1"
                    stroke-dasharray="4 4"
                  />

                  <text
                    v-for="tick in yTicks"
                    :key="`label-${tick.value}`"
                    :x="PADDING.left - 8"
                    :y="tick.y + 4"
                    text-anchor="end"
                    font-size="10"
                    fill="#64748b"
                  >
                    {{ tick.value }}
                  </text>

                  <path v-if="actualAreaPath" :d="actualAreaPath" class="chart-area" />
                  <path v-if="actualLinePath" :d="actualLinePath" class="chart-line" />
                  <path v-if="expectedLinePath" :d="expectedLinePath" class="chart-expected-line" />

                  <line
                    v-if="hoveredPoint"
                    :x1="hoveredPoint.x"
                    :x2="hoveredPoint.x"
                    :y1="PADDING.top"
                    :y2="CHART_HEIGHT - PADDING.bottom"
                    stroke="#0891b2"
                    stroke-width="1.2"
                    stroke-dasharray="3 3"
                  />

                  <circle
                    v-for="point in actualPoints"
                    :key="`point-${point.day}`"
                    :cx="point.x"
                    :cy="point.y"
                    r="3.5"
                    fill="#ffffff"
                    stroke="#0891b2"
                    stroke-width="2"
                    class="chart-point"
                  />

                  <circle
                    v-for="point in actualPoints"
                    :key="`hit-${point.day}`"
                    :cx="point.x"
                    :cy="point.y"
                    r="8"
                    fill="transparent"
                    class="day-hit"
                    @mouseenter="setHoveredDay(point.day)"
                  />

                  <circle
                    v-if="hoveredPoint"
                    :cx="hoveredPoint.x"
                    :cy="hoveredPoint.y"
                    r="5"
                    fill="#0891b2"
                    opacity="0.16"
                  />

                  <g v-if="hoveredPoint" :transform="`translate(${hoveredTooltipX}, ${PADDING.top + 6})`">
                    <rect width="128" height="46" rx="8" fill="#0f172a" opacity="0.88" />
                    <text x="8" y="17" font-size="10" fill="#dbeafe">{{ hoveredPoint.date }}</text>
                    <text x="8" y="31" font-size="10" fill="#e2e8f0">{{ hoveredWeekLabel }}</text>
                    <text x="8" y="43" font-size="11" fill="#f8fafc">累计 {{ hoveredPoint.value }} 个</text>
                  </g>

                  <text
                    v-for="label in weekLabels"
                    :key="`x-label-${label.label}`"
                    :x="label.x"
                    :y="CHART_HEIGHT - 10"
                    text-anchor="middle"
                    font-size="10"
                    fill="#64748b"
                  >
                    {{ label.label }}
                  </text>
                </g>
              </svg>
              </div>
            </div>

            <div class="space-y-1.5 rounded-2xl border border-slate-200 bg-slate-50 p-2.5">
              <div
                v-for="item in weeklyBreakdown"
                :key="item.week"
                class="week-card"
                :class="{ 'week-card-pending': !item.reached }"
              >
                <div class="flex items-center justify-between text-xs text-slate-500">
                  <span>{{ item.week }}</span>
                  <span>{{ formatPercent(item.percent) }}</span>
                </div>
                <p class="mt-1 text-sm font-semibold text-slate-900">{{ item.totalCreated }} 个</p>
                <div class="mt-1 h-1.5 rounded-full bg-slate-200">
                  <div class="h-1.5 rounded-full bg-cyan-500 transition-all duration-500" :style="{ width: `${item.progressBarWidth}%` }" />
                </div>
                <p class="mb-0 mt-1 text-xs text-slate-600">目标 {{ item.targetCount }} · 分析 {{ item.analysisCreated }} · 研判 {{ item.replyCreated }}</p>
              </div>
            </div>
          </div>
        </article>

        <article class="rounded-2xl border border-slate-200 bg-white p-4">
          <div class="mb-2 flex items-center justify-between">
            <h3 class="text-sm font-semibold text-slate-900">最近一个月每日创建</h3>
            <p class="text-xs text-slate-500">按当前月份自然日展示</p>
          </div>

          <div class="daily-strip custom-scrollbar">
            <div
              v-for="item in dashboard?.dailySeries ?? []"
              :key="item.date"
              class="daily-col"
            >
              <div class="daily-bar-wrap">
                <div class="daily-bar" :style="{ height: `${dailyBarHeight(item.totalCreated)}%` }" />
              </div>
              <p class="daily-day">{{ Number(item.date.slice(8, 10)) }}</p>
            </div>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useAuthStore } from '~/stores/auth'
import { useTaskStore } from '~/stores/task'
import type {
  AccountAvatarUploadResponse,
  AccountDashboard,
  AccountProfile,
  AccountProfileUpdateRequest,
  WeeklyActivityPoint,
} from '~/types/account'
import type { UsageResponse } from '~/types/usage'

const CHART_WIDTH = 760
const CHART_HEIGHT = 320
const PADDING = {
  top: 14,
  right: 14,
  bottom: 34,
  left: 42,
}

const config = useRuntimeConfig()
const authStore = useAuthStore()
const taskStore = useTaskStore()

const now = new Date()
const selectedMonth = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)
const loading = ref(false)
const refreshing = ref(false)
const savingTarget = ref(false)
const savingDisplayName = ref(false)
const savingAvatar = ref(false)
const pageReady = ref(false)
const errorMessage = ref('')
const targetErrorMessage = ref('')
const profileSaveErrorMessage = ref('')
const monthTargetInput = ref('0')
const hoveredDay = ref<number | null>(null)
const profile = ref<AccountProfile | null>(null)
const dashboard = ref<AccountDashboard | null>(null)
const editingDisplayName = ref(false)
const profileAvatarFileInput = ref<HTMLInputElement>()
const displayNameEditorRef = ref<HTMLElement>()
const displayNameDraft = ref('')
const discardDisplayNameOnBlur = ref(false)

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

const monthDays = computed(() => new Date(parsedYearMonth.value.year, parsedYearMonth.value.month, 0).getDate())
const visibleDayCount = computed(() => {
  if (isCurrentMonthSelection.value) return Math.min(monthDays.value, now.getDate())
  return monthDays.value
})

const dailySeries = computed(() => dashboard.value?.dailySeries ?? [])
const visibleDailySeries = computed(() => dailySeries.value.slice(0, visibleDayCount.value))
const weeklySeries = computed<WeeklyActivityPoint[]>(() => dashboard.value?.weeklySeries ?? [])

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
  if (!usageHasLimit.value) return '分析 1 点 / 研判 1.5 点 · 每日上限未配置'
  if ((dailyUsage.value?.remainingPoints || 0) <= 0) return `分析 1 点 / 研判 1.5 点 · 今日已用完 · 重置 ${usageResetLabel.value}`
  return `分析 1 点 / 研判 1.5 点 · 剩余 ${usageRemainingLabel.value} · 重置 ${usageResetLabel.value}`
})
const usageInfoToneClass = computed(() => {
  if (!usageHasLimit.value || (dailyUsage.value?.remainingPoints || 0) <= 0) return 'text-amber-500'
  return 'text-slate-400'
})
const showUsageLoginPrompt = computed(() => {
  return !!dailyUsage.value
    && dailyUsage.value.authType === 'guest'
    && dailyUsage.value.remainingPoints <= 0
    && hasAuthingEnabled.value
    && !authStore.isLoggedIn
})
const usageResetLabel = computed(() => {
  if (!dailyUsage.value?.resetAt) return '--'
  try {
    const resetDate = new Date(dailyUsage.value.resetAt)
    if (Number.isNaN(resetDate.getTime())) return '--'
    return resetDate.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  } catch (_error) {
    return '--'
  }
})

const elapsedRatio = computed(() => {
  if (!dashboard.value) return 0

  const targetYear = dashboard.value.year
  const targetMonth = dashboard.value.month
  const currentYear = now.getFullYear()
  const currentMonth = now.getMonth() + 1

  if (targetYear < currentYear || (targetYear === currentYear && targetMonth < currentMonth)) return 1
  if (targetYear > currentYear || (targetYear === currentYear && targetMonth > currentMonth)) return 0

  return Math.min(1, Math.max(0, now.getDate() / monthDays.value))
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

const plotWidth = computed(() => CHART_WIDTH - PADDING.left - PADDING.right)
const plotHeight = computed(() => CHART_HEIGHT - PADDING.top - PADDING.bottom)

const xByDay = (day: number): number => {
  if (monthDays.value <= 1) return PADDING.left + plotWidth.value / 2
  return PADDING.left + ((day - 1) * plotWidth.value) / (monthDays.value - 1)
}

const actualCumulativeValues = computed(() => {
  let running = 0
  return visibleDailySeries.value.map((item) => {
    running += item.totalCreated
    return running
  })
})

const expectedCumulativeValues = computed(() => {
  if (monthTarget.value <= 0) return Array.from({ length: visibleDayCount.value }).fill(0)
  return Array.from({ length: visibleDayCount.value }, (_item, index) => {
    const day = index + 1
    return (monthTarget.value * day) / Math.max(1, monthDays.value)
  })
})

const chartMaxY = computed(() => {
  return Math.max(
    4,
    monthTarget.value,
    ...actualCumulativeValues.value,
    ...expectedCumulativeValues.value.map((item) => Math.ceil(item)),
  )
})

const actualPoints = computed(() => {
  const maxY = chartMaxY.value || 1
  return actualCumulativeValues.value.map((value, index) => {
    const day = index + 1
    const y = PADDING.top + ((maxY - value) / maxY) * plotHeight.value
    return {
      day,
      x: xByDay(day),
      y,
      value,
      date: visibleDailySeries.value[index]?.date || '',
    }
  })
})

const expectedPoints = computed(() => {
  const maxY = chartMaxY.value || 1
  return expectedCumulativeValues.value.map((value, index) => {
    const day = index + 1
    const y = PADDING.top + ((maxY - value) / maxY) * plotHeight.value
    return { day, x: xByDay(day), y }
  })
})

const actualLinePath = computed(() => {
  if (actualPoints.value.length === 0) return ''
  return actualPoints.value
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`)
    .join(' ')
})

const actualAreaPath = computed(() => {
  if (actualPoints.value.length === 0) return ''
  const baseY = CHART_HEIGHT - PADDING.bottom
  const first = actualPoints.value[0]
  const last = actualPoints.value[actualPoints.value.length - 1]
  const linePart = actualPoints.value
    .map((point) => `L ${point.x} ${point.y}`)
    .join(' ')

  return `M ${first.x} ${baseY} ${linePart} L ${last.x} ${baseY} Z`
})

const expectedLinePath = computed(() => {
  if (expectedPoints.value.length === 0 || monthTarget.value <= 0) return ''
  return expectedPoints.value
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`)
    .join(' ')
})

const yTicks = computed(() => {
  const maxY = chartMaxY.value || 1
  return Array.from({ length: 5 }, (_item, index) => {
    const ratio = index / 4
    const value = Math.round(maxY * (1 - ratio))
    const y = PADDING.top + plotHeight.value * ratio
    return { value, y }
  })
})

const weekRanges = computed(() => {
  return [
    { label: '第1周', start: 1, end: Math.min(7, monthDays.value) },
    { label: '第2周', start: 8, end: Math.min(14, monthDays.value) },
    { label: '第3周', start: 15, end: Math.min(21, monthDays.value) },
    { label: '第4周', start: 22, end: monthDays.value },
  ]
})

const weekSeparators = computed(() => {
  return weekRanges.value
    .slice(1)
    .filter((item) => item.start <= monthDays.value)
    .map((item) => ({
      label: item.label,
      x: xByDay(item.start),
    }))
})

const weekLabels = computed(() => {
  return weekRanges.value.map((item) => {
    const middle = item.start > item.end ? item.end : (item.start + item.end) / 2
    return {
      label: item.label,
      x: xByDay(Math.max(1, middle)),
    }
  })
})

const weekDayCounts = computed(() => {
  return weekRanges.value.map((item) => {
    if (item.start > monthDays.value || item.end < item.start) return 0
    return item.end - item.start + 1
  })
})

const weeklyTargets = computed(() => {
  if (monthTarget.value <= 0) return [0, 0, 0, 0]

  const totalDays = Math.max(1, monthDays.value)
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
  const selectedYear = parsedYearMonth.value.year
  const selectedMonthNum = parsedYearMonth.value.month
  const currentYear = now.getFullYear()
  const currentMonthNum = now.getMonth() + 1

  if (selectedYear < currentYear || (selectedYear === currentYear && selectedMonthNum < currentMonthNum)) {
    return 3
  }
  if (selectedYear > currentYear || (selectedYear === currentYear && selectedMonthNum > currentMonthNum)) {
    return -1
  }
  return Math.min(3, Math.floor((now.getDate() - 1) / 7))
})

const weeklyBreakdown = computed(() => {
  const defaultWeeks = Array.from({ length: 4 }, (_item, index) => ({
    week: `第${index + 1}周`,
    analysisCreated: 0,
    replyCreated: 0,
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

const maxDailyValue = computed(() => {
  const values = dailySeries.value.map((item) => item.totalCreated)
  return Math.max(1, ...values)
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
  if (!dashboard.value) return '月度创建趋势'
  return `${dashboard.value.year} 年 ${dashboard.value.month} 月创建趋势`
})

const targetHintText = computed(() => {
  if (!isCurrentMonthSelection.value) return '当前查看历史月份，目标仅可在当月修改。'
  if (monthTargetSource.value === 'carried') return '本月目标已自动沿用最近一次设置值，需要的话可直接改。'
  if (monthTargetSource.value === 'empty') return '还没有任何目标，先定一个小目标会更容易跟进节奏。'
  return '已设置本月目标，后续进度和周卡片会按这个值实时更新。'
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
    return '这个月还没立下目标，先定一个你觉得“刚好有挑战”的数字，我们再一起盯节奏。'
  }
  const expectedCount = Math.round((monthTarget.value * elapsedRatio.value) || 0)
  const deltaCount = monthTotalCreated.value - expectedCount
  const absDeltaCount = Math.abs(deltaCount)
  const deltaPrefix = deltaCount >= 0 ? '领先' : '落后'
  if (progressDeltaPercent.value >= 8) {
    return `你这个月推进得很稳，当前已比节奏${deltaPrefix} ${absDeltaCount} 个，继续保持这个手感就很好。`
  }
  if (progressDeltaPercent.value <= -8) {
    return `最近节奏稍慢一点，当前比计划${deltaPrefix} ${absDeltaCount} 个；这周抓 1-2 个关键任务就能追上来。`
  }
  if (monthTargetSource.value === 'carried') {
    return '本月沿用了之前的目标，目前进度基本贴着计划线，继续按现在的节奏推进就可以。'
  }
  return '当前进度和目标几乎同步，整体状态很平稳，照这个节奏走就行。'
})

const targetInputInvalid = computed(() => {
  const raw = String(monthTargetInput.value ?? '').trim()
  if (!raw) return true
  const value = Number(raw)
  return !Number.isInteger(value) || value < 0
})

const hoveredPoint = computed(() => {
  if (hoveredDay.value == null) return null
  return actualPoints.value.find((item) => item.day === hoveredDay.value) || null
})

const hoveredTooltipX = computed(() => {
  if (!hoveredPoint.value) return PADDING.left
  const tooltipWidth = 128
  const minX = PADDING.left + 4
  const maxX = CHART_WIDTH - PADDING.right - tooltipWidth - 4
  return Math.max(minX, Math.min(maxX, hoveredPoint.value.x - tooltipWidth / 2))
})

const hoveredWeekLabel = computed(() => {
  if (!hoveredDay.value) return ''
  const week = Math.min(4, Math.floor((hoveredDay.value - 1) / 7) + 1)
  return `第${week}周`
})

const chartRenderKey = computed(() => `${selectedMonth.value}-${visibleDayCount.value}-${monthTotalCreated.value}-${monthTarget.value}`)

const formatPercent = (value: number): string => `${Math.round(value)}%`

const dailyBarHeight = (value: number): number => {
  const ratio = value / maxDailyValue.value
  return Math.max(6, Math.round(ratio * 100))
}

const shiftMonth = (delta: number) => {
  const { year, month } = parsedYearMonth.value
  const nextDate = new Date(year, month - 1 + delta, 1)
  const key = `${nextDate.getFullYear()}-${String(nextDate.getMonth() + 1).padStart(2, '0')}`
  if (delta > 0 && key > currentMonthKey) return
  selectedMonth.value = key
}

const setHoveredDay = (day: number) => {
  hoveredDay.value = day
}

const clearHoveredDay = () => {
  hoveredDay.value = null
}

const getAuthToken = async (): Promise<string> => {
  const authed = await taskStore.ensureAuth()
  if (!authed || !taskStore.authToken) {
    throw new Error('认证失败，请稍后重试。')
  }
  return taskStore.authToken
}

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
  const response = await fetch(`${config.public.apiBaseUrl}/api/account/profile`, {
    method: 'PUT',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(await toApiError(response))
  const nextProfile = await response.json() as AccountProfile
  profile.value = nextProfile
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
    const uploadResponse = await fetch(`${config.public.apiBaseUrl}/api/account/profile/avatar`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
      },
      body: formData,
    })
    if (!uploadResponse.ok) throw new Error(await toApiError(uploadResponse))
    const uploaded = await uploadResponse.json() as AccountAvatarUploadResponse
    await putProfile(token, {
      name: String(profile.value?.name || '').trim() || null,
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
  const response = await fetch(`${config.public.apiBaseUrl}/api/account/profile`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })
  if (!response.ok) throw new Error(await toApiError(response))
  profile.value = await response.json()
  if (!editingDisplayName.value) resetDisplayNameDraft()
}

const fetchDashboard = async (token: string) => {
  const { year, month } = parsedYearMonth.value
  const response = await fetch(`${config.public.apiBaseUrl}/api/account/dashboard?year=${year}&month=${month}`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })
  if (!response.ok) throw new Error(await toApiError(response))
  dashboard.value = await response.json()
  monthTargetInput.value = String(Math.max(0, Number(dashboard.value.monthTarget || 0)))
}

const fetchUsage = async (token: string) => {
  try {
    const response = await fetch(`${config.public.apiBaseUrl}/api/usage`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })
    if (!response.ok) return
    taskStore.dailyUsage = await response.json() as UsageResponse
    if ((taskStore.dailyUsage?.remainingPoints || 0) > 0) taskStore.clearPointLimitNotice()
  } catch (error) {
    console.error('加载积分信息失败：', error)
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
    const response = await fetch(`${config.public.apiBaseUrl}/api/account/month-target`, {
      method: 'PUT',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        year,
        month,
        targetCount: nextTarget,
      }),
    })
    if (!response.ok) throw new Error(await toApiError(response))
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
      name: nextName || null,
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

  try {
    const token = await getAuthToken()
    const jobs = [fetchDashboard(token), fetchUsage(token)]
    if (loadProfile || !profile.value) jobs.push(fetchProfile(token))
    await Promise.all(jobs)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '加载个人空间数据失败。'
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

watch(selectedMonth, async () => {
  hoveredDay.value = null
  targetErrorMessage.value = ''
  if (!pageReady.value) return
  await loadData(false)
})

watch(() => authStore.isLoggedIn, async () => {
  if (!pageReady.value) return
  await loadData(true)
})

onMounted(async () => {
  if (hasAuthingEnabled.value) {
    await authStore.ensureInitialized()
  }
  pageReady.value = true
  await loadData(true)
})
</script>

<style scoped>
.metric-card {
  border: 1px solid #e2e8f0;
  border-radius: 1rem;
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  padding: 0.9rem;
}

.metric-card-good {
  border-color: #86efac;
  background: linear-gradient(180deg, #f0fdf4 0%, #ecfdf3 100%);
}

.metric-card-warn {
  border-color: #fed7aa;
  background: linear-gradient(180deg, #fffbeb 0%, #fff7ed 100%);
}

.metric-label {
  font-size: 0.72rem;
  font-weight: 600;
  letter-spacing: 0.03em;
  color: #64748b;
}

.metric-value {
  margin-top: 0.2rem;
  font-size: 1.4rem;
  font-weight: 700;
  color: #0f172a;
}

.metric-desc {
  margin-top: 0.2rem;
  font-size: 0.75rem;
  color: #475569;
}

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

.chart-wrap {
  overflow: hidden;
  border-radius: 1rem;
  border: 1px solid #e2e8f0;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
  padding: 0.4rem;
}

.chart-area {
  fill: rgba(8, 145, 178, 0.12);
  animation: fade-in 400ms ease-out;
}

.chart-line {
  fill: none;
  stroke: #0891b2;
  stroke-width: 2.5;
  stroke-dasharray: 1200;
  stroke-dashoffset: 1200;
  animation: draw-line 800ms ease-out forwards;
}

.chart-expected-line {
  fill: none;
  stroke: #f59e0b;
  stroke-width: 2;
  stroke-dasharray: 6 4;
  opacity: 0;
  animation: fade-in 550ms ease-out 120ms forwards;
}

.chart-point {
  opacity: 0;
  animation: fade-in 450ms ease-out 220ms forwards;
}

.day-hit {
  cursor: pointer;
}

.week-card {
  border-radius: 0.8rem;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  padding: 0.45rem 0.55rem;
  transition: all 0.2s ease;
}

.week-card-pending {
  background: #f8fafc;
  border-color: #e2e8f0;
  opacity: 0.5;
}

.daily-strip {
  display: grid;
  grid-template-columns: repeat(31, minmax(1rem, 1fr));
  gap: 0.35rem;
  overflow-x: auto;
  padding-bottom: 0.3rem;
}

.daily-col {
  min-width: 1rem;
}

.daily-bar-wrap {
  display: flex;
  height: 4rem;
  align-items: flex-end;
}

.daily-bar {
  width: 100%;
  border-radius: 0.4rem 0.4rem 0.25rem 0.25rem;
  background: linear-gradient(180deg, #22d3ee 0%, #0891b2 100%);
}

.daily-day {
  margin-top: 0.18rem;
  text-align: center;
  font-size: 0.65rem;
  color: #64748b;
}

@keyframes draw-line {
  to {
    stroke-dashoffset: 0;
  }
}

@keyframes fade-in {
  from {
    opacity: 0;
    transform: translateY(2px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
