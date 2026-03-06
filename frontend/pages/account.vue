<template>
  <div class="space-y-4">
    <section class="rounded-3xl border border-slate-200 bg-white/92 p-4 shadow-sm shadow-slate-200 sm:p-5">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="flex items-center gap-3">
          <img
            v-if="profile?.picture"
            :src="profile.picture"
            alt="avatar"
            class="h-12 w-12 rounded-2xl border border-slate-200 object-cover"
          />
          <div
            v-else
            class="inline-flex h-12 w-12 items-center justify-center rounded-2xl border border-cyan-200 bg-cyan-50 text-sm font-semibold text-cyan-700"
          >
            {{ avatarText }}
          </div>

          <div>
            <p class="text-xs font-semibold tracking-[0.08em] text-slate-500">Personal Space</p>
            <h1 class="mt-1 text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">{{ displayName }}</h1>
            <p class="mt-1 text-xs text-slate-600">{{ displayAuthType }}</p>
          </div>
        </div>

        <div class="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-2 py-1">
          <button
            type="button"
            class="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-700 transition hover:bg-slate-100"
            @click="shiftMonth(-1)"
          >
            上月
          </button>
          <span class="min-w-20 text-center text-xs font-semibold text-slate-700">{{ monthDisplayLabel }}</span>
          <button
            type="button"
            class="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
            :disabled="!canMoveNextMonth"
            @click="shiftMonth(1)"
          >
            下月
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
            <p class="metric-desc">{{ monthTotalCreated }} / {{ projectedMonthTarget }} 个（预测）</p>
          </article>

          <article class="metric-card">
            <p class="metric-label">预期进度</p>
            <p class="metric-value">{{ expectedProgressLabel }}</p>
            <p class="metric-desc">差异 {{ progressDeltaLabel }}</p>
          </article>
        </div>

        <article class="rounded-2xl border border-slate-200 bg-white p-4">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h2 class="text-base font-semibold text-slate-900">{{ dashboardTitle }}</h2>
              <p class="text-xs text-slate-500">横轴第 1-4 周，纵轴累计任务数（含占比）</p>
            </div>
            <div class="flex items-center gap-2">
              <p
                v-if="refreshing"
                class="rounded-full border border-cyan-200 bg-cyan-50 px-2.5 py-1 text-xs text-cyan-700"
              >
                更新中...
              </p>
              <p class="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs text-slate-600">
                累计 {{ monthTotalCreated }} 个
              </p>
            </div>
          </div>

          <div class="mt-4 grid gap-4 lg:grid-cols-[1.45fr_0.55fr]">
            <div class="chart-wrap">
              <svg :viewBox="`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`" class="h-64 w-full">
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

                  <path v-if="actualAreaPath" :d="actualAreaPath" fill="rgba(8, 145, 178, 0.18)" />
                  <path v-if="actualLinePath" :d="actualLinePath" fill="none" stroke="#0891b2" stroke-width="2.5" />
                  <path
                    v-if="expectedLinePath"
                    :d="expectedLinePath"
                    fill="none"
                    stroke="#f59e0b"
                    stroke-width="2"
                    stroke-dasharray="6 4"
                  />

                  <circle
                    v-for="point in actualPoints"
                    :key="`point-${point.x}`"
                    :cx="point.x"
                    :cy="point.y"
                    r="4"
                    fill="#ffffff"
                    stroke="#0891b2"
                    stroke-width="2"
                  />

                  <text
                    v-for="(point, index) in actualPoints"
                    :key="`x-label-${index}`"
                    :x="point.x"
                    :y="CHART_HEIGHT - 10"
                    text-anchor="middle"
                    font-size="10"
                    fill="#64748b"
                  >
                    {{ xLabels[index] }}
                  </text>
                </g>
              </svg>
            </div>

            <div class="space-y-2 rounded-2xl border border-slate-200 bg-slate-50 p-3">
              <div class="rounded-xl border border-slate-200 bg-white px-3 py-2">
                <p class="text-xs text-slate-500">友好提示</p>
                <p class="mt-1 text-sm text-slate-700">{{ smartSummary }}</p>
              </div>

              <div
                v-for="item in weeklyBreakdown"
                :key="item.week"
                class="rounded-xl border border-slate-200 bg-white px-3 py-2"
              >
                <div class="flex items-center justify-between text-xs text-slate-500">
                  <span>{{ item.week }}</span>
                  <span>{{ formatPercent(item.percent) }}</span>
                </div>
                <p class="mt-1 text-sm font-semibold text-slate-900">{{ item.totalCreated }} 个</p>
                <p class="text-xs text-slate-600">目标 {{ item.targetCount }} · 分析 {{ item.analysisCreated }} · 研判 {{ item.replyCreated }}</p>
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

      <div v-if="loading" class="mt-4 rounded-2xl border border-slate-200 bg-white px-4 py-5 text-center text-sm text-slate-500">
        数据加载中...
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useAuthStore } from '~/stores/auth'
import { useTaskStore } from '~/stores/task'
import type { AccountDashboard, AccountProfile, WeeklyActivityPoint } from '~/types/account'

const CHART_WIDTH = 560
const CHART_HEIGHT = 260
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
const pageReady = ref(false)
const errorMessage = ref('')
const profile = ref<AccountProfile | null>(null)
const dashboard = ref<AccountDashboard | null>(null)

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

const weeklySeries = computed<WeeklyActivityPoint[]>(() => dashboard.value?.weeklySeries ?? [])
const monthTotalCreated = computed(() => weeklySeries.value.reduce((sum, item) => sum + item.totalCreated, 0))

const projectedMonthTarget = computed(() => {
  const baseline = Math.round((dashboard.value?.workWeek.totalCount ?? 0) * 4)
  return Math.max(monthTotalCreated.value, baseline)
})

const elapsedRatio = computed(() => {
  if (!dashboard.value) return 0

  const targetYear = dashboard.value.year
  const targetMonth = dashboard.value.month
  const currentYear = now.getFullYear()
  const currentMonth = now.getMonth() + 1

  if (targetYear < currentYear || (targetYear === currentYear && targetMonth < currentMonth)) return 1
  if (targetYear > currentYear || (targetYear === currentYear && targetMonth > currentMonth)) return 0

  const monthDays = new Date(targetYear, targetMonth, 0).getDate()
  return Math.min(1, Math.max(0, now.getDate() / monthDays))
})

const actualProgressPercent = computed(() => {
  if (!projectedMonthTarget.value) return 0
  return (monthTotalCreated.value / projectedMonthTarget.value) * 100
})

const expectedProgressPercent = computed(() => elapsedRatio.value * 100)
const progressDeltaPercent = computed(() => actualProgressPercent.value - expectedProgressPercent.value)

const xLabels = computed(() => {
  if (weeklySeries.value.length > 0) return weeklySeries.value.map((item) => item.week)
  return ['第1周', '第2周', '第3周', '第4周']
})

const expectedWeeklyValues = computed(() => {
  const weeks = xLabels.value.length
  if (weeks <= 0 || projectedMonthTarget.value <= 0) return Array.from({ length: weeks }).fill(0)
  return Array.from({ length: weeks }, (_item, index) => (projectedMonthTarget.value * (index + 1)) / weeks)
})

const cumulativeActualValues = computed(() => {
  const values = weeklySeries.value.length > 0
    ? weeklySeries.value.map((item) => item.totalCreated)
    : Array.from({ length: xLabels.value.length }).fill(0)
  let running = 0
  return values.map((value) => {
    running += value
    return running
  })
})

const chartMaxY = computed(() => {
  return Math.max(4, ...cumulativeActualValues.value, ...expectedWeeklyValues.value.map((item) => Math.ceil(item)))
})

const plotWidth = computed(() => CHART_WIDTH - PADDING.left - PADDING.right)
const plotHeight = computed(() => CHART_HEIGHT - PADDING.top - PADDING.bottom)

const actualPoints = computed(() => {
  const maxY = chartMaxY.value || 1
  return cumulativeActualValues.value.map((value, index) => {
    const x = xLabels.value.length === 1
      ? PADDING.left + plotWidth.value / 2
      : PADDING.left + (plotWidth.value * index) / (xLabels.value.length - 1)
    const y = PADDING.top + ((maxY - value) / maxY) * plotHeight.value
    return { x, y, value }
  })
})

const expectedPoints = computed(() => {
  const maxY = chartMaxY.value || 1
  return expectedWeeklyValues.value.map((value, index) => {
    const x = xLabels.value.length === 1
      ? PADDING.left + plotWidth.value / 2
      : PADDING.left + (plotWidth.value * index) / (xLabels.value.length - 1)
    const y = PADDING.top + ((maxY - value) / maxY) * plotHeight.value
    return { x, y }
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
    .map((point, index) => `${index === 0 ? 'L' : 'L'} ${point.x} ${point.y}`)
    .join(' ')

  return `M ${first.x} ${baseY} ${linePart} L ${last.x} ${baseY} Z`
})

const expectedLinePath = computed(() => {
  if (expectedPoints.value.length === 0) return ''
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

const weeklyBreakdown = computed(() => {
  const projected = Math.max(1, projectedMonthTarget.value)
  const weeklyTarget = Math.max(0, Math.round(projected / Math.max(1, xLabels.value.length)))
  return (dashboard.value?.weeklySeries ?? []).map((item) => ({
    ...item,
    percent: (item.totalCreated / projected) * 100,
    targetCount: weeklyTarget,
  }))
})

const maxDailyValue = computed(() => {
  const values = (dashboard.value?.dailySeries ?? []).map((item) => item.totalCreated)
  return Math.max(1, ...values)
})

const displayName = computed(() => {
  const candidates = [
    profile.value?.nickname,
    profile.value?.name,
    authStore.user?.nickname,
    authStore.user?.name,
    profile.value?.email,
    authStore.user?.email,
  ]
  return candidates.find((item) => item && String(item).trim().length > 0) || '访客用户'
})

const avatarText = computed(() => String(displayName.value).trim().slice(0, 1).toUpperCase() || 'U')

const displayAuthType = computed(() => {
  if (profile.value?.authType === 'authing' || authStore.isLoggedIn) return 'Authing 账号'
  return '匿名访客'
})

const dashboardTitle = computed(() => {
  if (!dashboard.value) return '月度创建趋势'
  return `${dashboard.value.year} 年 ${dashboard.value.month} 月创建趋势`
})

const actualProgressLabel = computed(() => formatPercent(actualProgressPercent.value))
const expectedProgressLabel = computed(() => formatPercent(expectedProgressPercent.value))
const progressDeltaLabel = computed(() => {
  const sign = progressDeltaPercent.value >= 0 ? '+' : ''
  return `${sign}${formatPercent(progressDeltaPercent.value)}`
})

const deltaToneClass = computed(() => {
  if (progressDeltaPercent.value >= 8) return 'metric-card-good'
  if (progressDeltaPercent.value <= -8) return 'metric-card-warn'
  return ''
})

const smartSummary = computed(() => {
  const base = dashboard.value?.summaryText || '当前暂无可用统计数据。'
  if (progressDeltaPercent.value >= 8) {
    return `${base} 当前节奏快于预期，建议优先处理高价值任务。`
  }
  if (progressDeltaPercent.value <= -8) {
    return `${base} 当前节奏低于预期，可在本周补齐关键任务。`
  }
  return `${base} 当前与预期基本一致，可保持当前节奏。`
})

const formatPercent = (value: number): string => `${Math.round(value)}%`

const shiftMonth = (delta: number) => {
  const { year, month } = parsedYearMonth.value
  const nextDate = new Date(year, month - 1 + delta, 1)
  const key = `${nextDate.getFullYear()}-${String(nextDate.getMonth() + 1).padStart(2, '0')}`
  if (delta > 0 && key > currentMonthKey) return
  selectedMonth.value = key
}

const dailyBarHeight = (value: number): number => {
  const ratio = value / maxDailyValue.value
  return Math.max(6, Math.round(ratio * 100))
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

const fetchProfile = async (token: string) => {
  const response = await fetch(`${config.public.apiBaseUrl}/api/account/profile`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })
  if (!response.ok) throw new Error(await toApiError(response))
  profile.value = await response.json()
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
}

const loadData = async (loadProfile: boolean) => {
  if (loadProfile) loading.value = true
  else refreshing.value = true

  errorMessage.value = ''

  try {
    const token = await getAuthToken()
    const jobs = [fetchDashboard(token)]
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

.chart-wrap {
  overflow: hidden;
  border-radius: 1rem;
  border: 1px solid #e2e8f0;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
  padding: 0.4rem;
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
</style>
