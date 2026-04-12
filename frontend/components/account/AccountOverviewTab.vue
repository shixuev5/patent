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

          <div class="chart-wrap" @mouseleave="clearHoveredDay">
            <svg
              :key="chartRenderKey"
              :viewBox="`0 0 ${chartWidth} ${chartHeight}`"
              preserveAspectRatio="xMidYMid meet"
              class="h-[22rem] w-full"
            >
              <g>
                <line
                  v-for="tick in yTicks"
                  :key="`line-${tick.value}`"
                  :x1="padding.left"
                  :x2="chartWidth - padding.right"
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
                  :y1="padding.top"
                  :y2="chartHeight - padding.bottom"
                  stroke="#cbd5e1"
                  stroke-dasharray="4 4"
                />

                <text
                  v-for="tick in yTicks"
                  :key="`label-${tick.value}`"
                  :x="padding.left - 8"
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
                  :y1="padding.top"
                  :y2="chartHeight - padding.bottom"
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

                <g v-if="hoveredPoint" :transform="`translate(${hoveredTooltipX}, ${padding.top + 6})`">
                  <rect width="128" height="46" rx="8" fill="#0f172a" opacity="0.88" />
                  <text x="8" y="17" font-size="10" fill="#dbeafe">{{ hoveredPoint.date }}</text>
                  <text x="8" y="31" font-size="10" fill="#e2e8f0">{{ hoveredWeekLabel }}</text>
                  <text x="8" y="43" font-size="11" fill="#f8fafc">累计 {{ hoveredPoint.value }} 个</text>
                </g>

                <text
                  v-for="label in weekLabels"
                  :key="`x-label-${label.label}`"
                  :x="label.x"
                  :y="chartHeight - 10"
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
            <p class="mb-0 mt-1 text-xs text-slate-600">目标 {{ item.targetCount }} · 分析 {{ item.analysisCreated }} · 审查 {{ item.reviewCreated ?? 0 }} · 答复 {{ item.replyCreated }}</p>
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
</template>

<script setup lang="ts">
import type { AccountDashboard } from '~/types/account'

interface ChartPadding {
  top: number
  right: number
  bottom: number
  left: number
}

interface TickItem {
  value: number
  y: number
}

interface WeekLine {
  label: string
  x: number
}

interface HoveredPoint {
  day: number
  x: number
  y: number
  value: number
  date: string
}

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

defineProps<{
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
  chartRenderKey: string
  chartWidth: number
  chartHeight: number
  padding: ChartPadding
  yTicks: TickItem[]
  weekSeparators: WeekLine[]
  actualAreaPath: string
  actualLinePath: string
  expectedLinePath: string
  hoveredPoint: HoveredPoint | null
  hoveredTooltipX: number
  hoveredWeekLabel: string
  actualPoints: HoveredPoint[]
  weekLabels: WeekLine[]
  weeklyBreakdown: WeeklyBreakdownItem[]
  formatPercent: (value: number) => string
  dailyBarHeight: (value: number) => number
  clearHoveredDay: () => void
  setHoveredDay: (day: number) => void
}>()
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
