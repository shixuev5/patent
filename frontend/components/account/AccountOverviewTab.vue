<template>
  <div class="space-y-4">
    <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <article class="metric-card">
        <p class="metric-label">最近一个工作周</p>
        <p class="metric-value">{{ dashboard?.workWeek.totalCount ?? 0 }}</p>
        <p class="metric-desc">分析 {{ dashboard?.workWeek.analysisCount ?? 0 }} · 审查 {{ dashboard?.workWeek.reviewCount ?? 0 }} · 答复 {{ dashboard?.workWeek.replyCount ?? 0 }}</p>
      </article>

      <article class="metric-card">
        <p class="metric-label">最近一个工作月</p>
        <p class="metric-value">{{ dashboard?.workMonth.totalCount ?? 0 }}</p>
        <p class="metric-desc">分析 {{ dashboard?.workMonth.analysisCount ?? 0 }} · 审查 {{ dashboard?.workMonth.reviewCount ?? 0 }} · 答复 {{ dashboard?.workMonth.replyCount ?? 0 }}</p>
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

          <div class="chart-wrap">
            <div ref="trendChartRef" class="chart-canvas trend-chart" />
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
            <p class="mb-0 mt-1 text-xs text-slate-600">目标 {{ item.targetCount }} · 分析 {{ item.analysisCreated }} · 审查 {{ item.reviewCreated ?? 0 }} · 答复 {{ item.replyCreated }}</p>
          </div>
        </div>
      </div>
    </article>

    <article class="rounded-2xl border border-slate-200 bg-white p-4">
      <div class="mb-2 flex items-center justify-between gap-3">
        <h3 class="text-sm font-semibold text-slate-900">最近一个月每日创建</h3>
        <p class="text-xs text-slate-500">按当前月份自然日展示</p>
      </div>

      <div class="daily-chart-wrap">
        <div ref="dailyChartRef" class="chart-canvas daily-chart" />
      </div>
    </article>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { AccountDashboard } from '~/types/account'

interface WeeklyBreakdownItem {
  week: string
  analysisCreated: number
  reviewCreated?: number
  replyCreated: number
  totalCreated: number
  targetCount: number
  percent: number
  progressBarWidth: number
  reached: boolean
}

const props = defineProps<{
  dashboard: AccountDashboard | null
  refreshing: boolean
  deltaToneClass: string
  actualProgressLabel: string
  expectedProgressLabel: string
  monthTotalCreated: number
  monthTarget: number
  progressDeltaLabel: string
  dashboardTitle: string
  smartSummary: string
  weeklyBreakdown: WeeklyBreakdownItem[]
}>()

const trendChartRef = ref<HTMLDivElement | null>(null)
const dailyChartRef = ref<HTMLDivElement | null>(null)
type EChartsInstance = {
  setOption: (option: Record<string, any>, notMerge?: boolean) => void
  resize: () => void
  dispose: () => void
}

type EChartsGlobal = {
  init: (element: HTMLDivElement) => EChartsInstance
}

declare global {
  interface Window {
    echarts?: EChartsGlobal
  }
}

let trendChart: EChartsInstance | null = null
let dailyChart: EChartsInstance | null = null
let resizeObserver: ResizeObserver | null = null
let chartBootstrapTimer: ReturnType<typeof setTimeout> | null = null

const now = new Date()

const monthDays = computed(() => {
  const year = props.dashboard?.year ?? now.getFullYear()
  const month = props.dashboard?.month ?? (now.getMonth() + 1)
  return new Date(year, month, 0).getDate()
})

const isCurrentMonthDashboard = computed(() => (
  !!props.dashboard
  && props.dashboard.year === now.getFullYear()
  && props.dashboard.month === now.getMonth() + 1
))

const visibleDayCount = computed(() => (
  isCurrentMonthDashboard.value
    ? Math.min(monthDays.value, now.getDate())
    : monthDays.value
))

const dailySeriesByDay = computed(() => {
  const series = Array.from({ length: monthDays.value }, (_item, index) => ({
    date: '',
    totalCreated: 0,
    day: index + 1,
  }))
  for (const item of props.dashboard?.dailySeries || []) {
    const day = Number(String(item.date || '').slice(8, 10))
    if (!Number.isInteger(day) || day < 1 || day > series.length) continue
    series[day - 1] = {
      date: item.date,
      totalCreated: Number(item.totalCreated || 0),
      day,
    }
  }
  return series
})

const dayCategories = computed(() => Array.from({ length: monthDays.value }, (_item, index) => String(index + 1)))

const actualCumulativeValues = computed<(number | null)[]>(() => {
  let running = 0
  return dailySeriesByDay.value.map((item, index) => {
    if (index >= visibleDayCount.value) return null
    running += Number(item.totalCreated || 0)
    return running
  })
})

const expectedCumulativeValues = computed<(number | null)[]>(() => {
  if (props.monthTarget <= 0) return Array.from({ length: monthDays.value }).fill(null)
  return Array.from({ length: monthDays.value }, (_item, index) => {
    if (index >= visibleDayCount.value) return null
    return Number(((props.monthTarget * (index + 1)) / Math.max(1, monthDays.value)).toFixed(2))
  })
})

const chartMaxY = computed(() => {
  const actual = actualCumulativeValues.value.filter((item): item is number => typeof item === 'number')
  const expected = expectedCumulativeValues.value.filter((item): item is number => typeof item === 'number').map(item => Math.ceil(item))
  return Math.max(4, props.monthTarget, ...actual, ...expected)
})

const weekRanges = computed(() => ([
  { label: '第1周', start: 1, end: Math.min(7, monthDays.value) },
  { label: '第2周', start: 8, end: Math.min(14, monthDays.value) },
  { label: '第3周', start: 15, end: Math.min(21, monthDays.value) },
  { label: '第4周', start: 22, end: monthDays.value },
]))

const weekSeparatorDays = computed(() => (
  weekRanges.value
    .slice(1)
    .filter(item => item.start <= monthDays.value)
    .map(item => item.start)
))

const weekLabelMap = computed<Record<number, string>>(() => {
  const mapping: Record<number, string> = {}
  for (const range of weekRanges.value) {
    const midpoint = Math.max(range.start, Math.floor((range.start + range.end) / 2))
    mapping[midpoint] = range.label
  }
  return mapping
})

const dailyTotals = computed(() => dailySeriesByDay.value.map(item => Number(item.totalCreated || 0)))

const echartsGlobal = (): EChartsGlobal | null => {
  if (typeof window === 'undefined') return null
  return window.echarts || null
}

const trendChartOption = computed<Record<string, any>>(() => ({
  animationDuration: 420,
  animationDurationUpdate: 220,
  grid: {
    left: 14,
    right: 16,
    top: 16,
    bottom: 34,
    containLabel: true,
  },
  tooltip: {
    trigger: 'axis',
    backgroundColor: 'rgba(15, 23, 42, 0.92)',
    borderWidth: 0,
    textStyle: {
      color: '#f8fafc',
      fontSize: 12,
    },
    extraCssText: 'border-radius: 12px; box-shadow: 0 12px 28px rgba(15,23,42,0.22);',
    formatter: (rawParams: any) => {
      const params = Array.isArray(rawParams) ? rawParams : [rawParams]
      const day = Number(params[0]?.axisValue || 0)
      const actual = actualCumulativeValues.value[day - 1]
      const expected = expectedCumulativeValues.value[day - 1]
      const item = dailySeriesByDay.value[day - 1]
      const dateLabel = item?.date || `${props.dashboard?.year ?? now.getFullYear()}-${String(props.dashboard?.month ?? (now.getMonth() + 1)).padStart(2, '0')}-${String(day).padStart(2, '0')}`
      const weekLabel = `第${Math.min(4, Math.floor((Math.max(day, 1) - 1) / 7) + 1)}周`
      return [
        `<div style="font-weight:600;margin-bottom:4px;">${dateLabel}</div>`,
        `<div style="opacity:0.82;margin-bottom:4px;">${weekLabel}</div>`,
        `<div>实际累计：${actual == null ? '-' : actual} 个</div>`,
        `<div>预期累计：${expected == null ? '-' : Math.round(expected * 100) / 100} 个</div>`,
      ].join('')
    },
  },
  xAxis: {
    type: 'category',
    boundaryGap: false,
    data: dayCategories.value,
    axisTick: { show: false },
    axisLine: {
      lineStyle: {
        color: '#cbd5e1',
      },
    },
    axisLabel: {
      interval: 0,
      margin: 12,
      fontSize: 10,
      color: '#64748b',
      formatter: (value: string) => weekLabelMap.value[Number(value)] || '',
    },
  },
  yAxis: {
    type: 'value',
    min: 0,
    max: chartMaxY.value,
    axisLabel: {
      color: '#64748b',
      fontSize: 10,
    },
    splitLine: {
      show: true,
      lineStyle: {
        color: '#e2e8f0',
        type: 'dashed',
      },
    },
  },
  series: [
    {
      name: '实际累计',
      type: 'line',
      data: actualCumulativeValues.value,
      smooth: false,
      showSymbol: true,
      symbol: 'circle',
      symbolSize: 7,
      connectNulls: false,
      lineStyle: {
        width: 3,
        color: '#0891b2',
      },
      itemStyle: {
        color: '#ffffff',
        borderColor: '#0891b2',
        borderWidth: 2,
      },
      areaStyle: {
        color: 'rgba(8, 145, 178, 0.12)',
      },
      emphasis: {
        scale: true,
      },
    },
    {
      name: '预期累计',
      type: 'line',
      data: expectedCumulativeValues.value,
      smooth: false,
      showSymbol: false,
      connectNulls: false,
      lineStyle: {
        width: 2,
        color: '#f59e0b',
        type: 'dashed',
      },
      markLine: {
        silent: true,
        symbol: ['none', 'none'],
        label: {
          show: false,
        },
        lineStyle: {
          color: '#cbd5e1',
          type: 'dashed',
          width: 1,
        },
        data: weekSeparatorDays.value.map(day => ({ xAxis: String(day) })),
      },
    },
  ],
}))

const dailyChartOption = computed<Record<string, any>>(() => ({
  animationDuration: 280,
  animationDurationUpdate: 180,
  grid: {
    left: 2,
    right: 2,
    top: 6,
    bottom: 16,
    containLabel: true,
  },
  tooltip: {
    trigger: 'axis',
    axisPointer: {
      type: 'line',
      lineStyle: {
        color: 'rgba(14, 165, 233, 0.18)',
        width: 1,
      },
    },
    backgroundColor: 'rgba(15, 23, 42, 0.92)',
    borderWidth: 0,
    textStyle: {
      color: '#f8fafc',
      fontSize: 12,
    },
    extraCssText: 'border-radius: 12px; box-shadow: 0 12px 28px rgba(15,23,42,0.22);',
    formatter: (rawParams: any) => {
      const params = Array.isArray(rawParams) ? rawParams : [rawParams]
      const day = Number(params[0]?.axisValue || 0)
      const value = dailyTotals.value[day - 1] || 0
      const item = dailySeriesByDay.value[day - 1]
      const dateLabel = item?.date || `${props.dashboard?.year ?? now.getFullYear()}-${String(props.dashboard?.month ?? (now.getMonth() + 1)).padStart(2, '0')}-${String(day).padStart(2, '0')}`
      return [
        `<div style="font-weight:600;margin-bottom:4px;">${dateLabel}</div>`,
        `<div>当日创建：${value} 个</div>`,
      ].join('')
    },
  },
  xAxis: {
    type: 'category',
    data: dayCategories.value,
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: {
      color: '#94a3b8',
      fontSize: 9,
      interval: 0,
      hideOverlap: false,
    },
  },
  yAxis: {
    type: 'value',
    min: 0,
    minInterval: 1,
    show: false,
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: {
      show: false,
    },
  },
  series: [
    {
      name: '每日创建',
      type: 'bar',
      data: dailyTotals.value,
      barWidth: '70%',
      barMaxWidth: 42,
      barMinWidth: 18,
      barMinHeight: 6,
      itemStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: '#39c1e6' },
            { offset: 1, color: '#1499cc' },
          ],
        },
        borderRadius: [10, 10, 10, 10],
      },
      emphasis: {
        itemStyle: {
          color: '#0369a1',
        },
      },
    },
  ],
}))

const initTrendChart = () => {
  const echarts = echartsGlobal()
  if (!echarts || !trendChartRef.value) return false
  if (!trendChart) trendChart = echarts.init(trendChartRef.value)
  trendChart.setOption(trendChartOption.value, true)
  trendChart.resize()
  return true
}

const initDailyChart = () => {
  const echarts = echartsGlobal()
  if (!echarts || !dailyChartRef.value) return false
  if (!dailyChart) dailyChart = echarts.init(dailyChartRef.value)
  dailyChart.setOption(dailyChartOption.value, true)
  dailyChart.resize()
  return true
}

const syncCharts = async () => {
  await nextTick()
  const trendReady = initTrendChart()
  const dailyReady = initDailyChart()
  if (!trendReady || !dailyReady) {
    if (chartBootstrapTimer) clearTimeout(chartBootstrapTimer)
    chartBootstrapTimer = setTimeout(() => {
      void syncCharts()
    }, 120)
  }
}

const resizeCharts = () => {
  trendChart?.resize()
  dailyChart?.resize()
}

const formatPercent = (value: number): string => `${Math.round(value)}%`

watch([trendChartOption, dailyChartOption], () => {
  void syncCharts()
})

onMounted(() => {
  void syncCharts()
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      resizeCharts()
    })
    if (trendChartRef.value) resizeObserver.observe(trendChartRef.value)
    if (dailyChartRef.value) resizeObserver.observe(dailyChartRef.value)
  }
  window.addEventListener('resize', resizeCharts)
})

onBeforeUnmount(() => {
  if (chartBootstrapTimer) clearTimeout(chartBootstrapTimer)
  resizeObserver?.disconnect()
  window.removeEventListener('resize', resizeCharts)
  trendChart?.dispose()
  dailyChart?.dispose()
  trendChart = null
  dailyChart = null
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

.chart-wrap,
.daily-chart-wrap {
  overflow: hidden;
  border-radius: 1rem;
  border: 1px solid #e2e8f0;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
  padding: 0.4rem;
}

.daily-chart-wrap {
  border-color: #e7eef5;
  background: linear-gradient(180deg, #fcfdff 0%, #ffffff 100%);
  padding: 0.35rem 0.35rem 0.1rem;
}

.chart-canvas {
  width: 100%;
}

.trend-chart {
  height: 22rem;
}

.daily-chart {
  height: 8.25rem;
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

@media (max-width: 640px) {
  .trend-chart {
    height: 18rem;
  }

  .daily-chart {
    height: 7rem;
  }
}
</style>
