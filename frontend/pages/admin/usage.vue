<template>
  <div class="space-y-4">
    <section class="rounded-3xl border border-slate-200 bg-white/92 p-4 shadow-sm shadow-slate-200 sm:p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p class="text-xs font-semibold tracking-[0.08em] text-slate-500">Admin Console</p>
          <h1 class="mt-1 text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">Token 与费用统计</h1>
          <p class="mt-2 text-xs leading-5 text-slate-600 sm:text-sm">按任务级汇总统计，支持任务/用户/全局视角与天/月/年时间范围。</p>
        </div>
      </div>
    </section>

    <section v-if="loadingAccess" class="rounded-3xl border border-slate-200 bg-white p-6 text-sm text-slate-600">
      正在验证管理员权限...
    </section>

    <section
      v-else-if="!isAdmin"
      class="rounded-3xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700"
    >
      当前账号无管理员权限，无法访问统计中心。
    </section>

    <template v-else>
      <section class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
        <div class="grid gap-3 md:grid-cols-[auto_auto_auto_auto_auto_auto] md:items-end">
          <label class="field">
            <span>时间范围</span>
            <select v-model="rangeType" class="field-input" @change="onRangeTypeChange">
              <option value="day">按天</option>
              <option value="month">按月</option>
              <option value="year">按年</option>
            </select>
          </label>

          <label v-if="rangeType === 'day'" class="field">
            <span>锚点日期</span>
            <input v-model="anchorDay" type="date" class="field-input" />
          </label>
          <label v-else-if="rangeType === 'month'" class="field">
            <span>锚点月份</span>
            <input v-model="anchorMonth" type="month" class="field-input" />
          </label>
          <label v-else class="field">
            <span>锚点年份</span>
            <input v-model.number="anchorYear" type="number" min="2020" max="2100" class="field-input" />
          </label>

          <label class="field">
            <span>表格范围</span>
            <select v-model="scope" class="field-input">
              <option value="task">按任务</option>
              <option value="user">按用户</option>
              <option value="all">全局汇总</option>
            </select>
          </label>

          <label class="field">
            <span>关键词</span>
            <input
              v-model.trim="keyword"
              type="text"
              placeholder="taskId / ownerId / model"
              class="field-input"
            />
          </label>

          <button
            type="button"
            class="rounded-xl bg-cyan-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-cyan-800"
            :disabled="loadingDashboard || loadingTable"
            @click="refreshAll"
          >
            {{ loadingDashboard || loadingTable ? '刷新中...' : '刷新统计' }}
          </button>
        </div>

        <p v-if="dashboard?.priceMissing || tableData?.priceMissing" class="mt-3 text-xs text-amber-700">
          存在未配置单价的模型，相关费用按 0 估算。
        </p>
      </section>

      <section class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <article class="metric-card">
          <p class="metric-label">总任务数</p>
          <p class="metric-value">{{ dashboard?.overview.totalTasks ?? 0 }}</p>
        </article>
        <article class="metric-card">
          <p class="metric-label">总用户数</p>
          <p class="metric-value">{{ dashboard?.overview.totalUsers ?? 0 }}</p>
        </article>
        <article class="metric-card">
          <p class="metric-label">总 Token</p>
          <p class="metric-value">{{ formatNumber(dashboard?.overview.totalTokens ?? 0) }}</p>
        </article>
        <article class="metric-card">
          <p class="metric-label">总费用（{{ dashboard?.currency || 'CNY' }}）</p>
          <p class="metric-value">{{ formatCost(dashboard?.overview.totalEstimatedCostCny ?? 0) }}</p>
        </article>
        <article class="metric-card">
          <p class="metric-label">平均 Token/任务</p>
          <p class="metric-value">{{ formatNumber(dashboard?.overview.avgTokensPerTask ?? 0) }}</p>
        </article>
        <article class="metric-card">
          <p class="metric-label">平均费用/任务</p>
          <p class="metric-value">{{ formatCost(dashboard?.overview.avgCostPerTaskCny ?? 0) }}</p>
        </article>
      </section>

      <section class="grid gap-3 xl:grid-cols-3">
        <article class="rounded-2xl border border-slate-200 bg-white p-4 xl:col-span-2">
          <h2 class="text-sm font-semibold text-slate-900">趋势图（Token / 费用）</h2>
          <div class="mt-3 space-y-2">
            <div
              v-for="item in dashboard?.trend || []"
              :key="`trend-${item.label}`"
              class="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2"
            >
              <div class="flex items-center justify-between text-[11px] text-slate-600">
                <span>{{ item.label }}</span>
                <span>Token {{ formatNumber(item.totalTokens) }} · 费用 {{ formatCost(item.estimatedCostCny) }}</span>
              </div>
              <div class="mt-1 h-1.5 rounded-full bg-slate-200">
                <div class="h-1.5 rounded-full bg-cyan-500" :style="{ width: `${trendBarWidth(item.totalTokens)}%` }" />
              </div>
            </div>
          </div>
        </article>

        <article class="rounded-2xl border border-slate-200 bg-white p-4">
          <h2 class="text-sm font-semibold text-slate-900">用户 Top10</h2>
          <div class="mt-3 space-y-2">
            <div
              v-for="item in dashboard?.topUsers || []"
              :key="`top-${item.ownerId}`"
              class="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-xs"
            >
              <p class="font-semibold text-slate-800">{{ item.ownerId }}</p>
              <p class="mt-1 text-slate-600">任务 {{ item.taskCount }} · Token {{ formatNumber(item.totalTokens) }}</p>
              <p class="text-slate-600">费用 {{ formatCost(item.estimatedCostCny) }}</p>
            </div>
          </div>
        </article>
      </section>

      <section class="rounded-2xl border border-slate-200 bg-white p-4">
        <h2 class="text-sm font-semibold text-slate-900">任务类型分布</h2>
        <div class="mt-3 space-y-2">
          <div
            v-for="item in dashboard?.byTaskType || []"
            :key="`type-${item.taskType}`"
            class="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2"
          >
            <div class="flex items-center justify-between text-xs text-slate-700">
              <span>{{ item.taskType }}</span>
              <span>任务 {{ item.taskCount }} · Token {{ formatNumber(item.totalTokens) }} · 费用 {{ formatCost(item.estimatedCostCny) }}</span>
            </div>
            <div class="mt-1 h-1.5 rounded-full bg-slate-200">
              <div class="h-1.5 rounded-full bg-emerald-500" :style="{ width: `${taskTypeBarWidth(item.totalTokens)}%` }" />
            </div>
          </div>
        </div>
      </section>

      <section class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
        <div class="mb-3 flex flex-wrap items-end gap-2">
          <label class="field">
            <span>任务类型</span>
            <select v-model="taskTypeFilter" class="field-input">
              <option value="">全部</option>
              <option value="patent_analysis">patent_analysis</option>
              <option value="office_action_reply">office_action_reply</option>
            </select>
          </label>
          <label class="field">
            <span>状态</span>
            <input v-model.trim="statusFilter" type="text" placeholder="completed / failed" class="field-input" />
          </label>
          <label class="field">
            <span>模型</span>
            <input v-model.trim="modelFilter" type="text" placeholder="deepseek-chat" class="field-input" />
          </label>
          <button
            type="button"
            class="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
            :disabled="loadingTable"
            @click="refreshTable"
          >
            {{ loadingTable ? '加载中...' : '检索表格' }}
          </button>
        </div>

        <div class="overflow-x-auto rounded-xl border border-slate-200">
          <table class="min-w-full divide-y divide-slate-200 text-xs">
            <thead class="bg-slate-50 text-slate-600">
              <tr>
                <th class="px-2.5 py-2 text-left font-semibold">主键</th>
                <th class="px-2.5 py-2 text-left font-semibold">任务/用户</th>
                <th class="px-2.5 py-2 text-right font-semibold">Token</th>
                <th class="px-2.5 py-2 text-right font-semibold">费用(CNY)</th>
                <th class="px-2.5 py-2 text-right font-semibold">调用数</th>
                <th class="px-2.5 py-2 text-left font-semibold">时间</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100 bg-white text-slate-700">
              <tr v-for="row in tableData?.items || []" :key="rowKey(row)">
                <td class="px-2.5 py-2 font-mono text-[11px]">{{ row.taskId || row.ownerId || 'all' }}</td>
                <td class="px-2.5 py-2">
                  <template v-if="scope === 'task'">
                    <p>{{ row.ownerId }}</p>
                    <p class="text-slate-500">{{ row.taskType }} · {{ row.taskStatus }}</p>
                  </template>
                  <template v-else-if="scope === 'user'">
                    <p>{{ row.ownerId }}</p>
                    <p class="text-slate-500">任务 {{ row.taskCount }}</p>
                  </template>
                  <template v-else>
                    <p>全局汇总</p>
                    <p class="text-slate-500">任务 {{ row.taskCount }} · 用户 {{ row.userCount }}</p>
                  </template>
                </td>
                <td class="px-2.5 py-2 text-right">{{ formatNumber(row.totalTokens || 0) }}</td>
                <td class="px-2.5 py-2 text-right">{{ formatCost(row.estimatedCostCny || 0) }}</td>
                <td class="px-2.5 py-2 text-right">{{ formatNumber(row.llmCallCount || 0) }}</td>
                <td class="px-2.5 py-2">{{ row.lastUsageAt || row.latestUsageAt || '-' }}</td>
              </tr>
              <tr v-if="!(tableData?.items?.length)">
                <td colspan="6" class="px-2.5 py-8 text-center text-slate-500">当前筛选条件下暂无数据</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="mt-3 flex items-center justify-end gap-2 text-xs text-slate-600">
          <button
            type="button"
            class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
            :disabled="scope === 'all' || currentPage <= 1 || loadingTable"
            @click="currentPage -= 1; refreshTable()"
          >
            上一页
          </button>
          <span>第 {{ currentPage }} 页 · 共 {{ tableData?.total ?? 0 }} 条</span>
          <button
            type="button"
            class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
            :disabled="scope === 'all' || ((tableData?.items?.length || 0) < pageSize) || loadingTable"
            @click="currentPage += 1; refreshTable()"
          >
            下一页
          </button>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useAdminUsageStore } from '~/stores/adminUsage'
import type { UsageRangeType, UsageScopeType } from '~/types/adminUsage'

const adminStore = useAdminUsageStore()

const rangeType = ref<UsageRangeType>('day')
const scope = ref<UsageScopeType>('task')
const keyword = ref('')
const taskTypeFilter = ref('')
const statusFilter = ref('')
const modelFilter = ref('')

const now = new Date()
const anchorDay = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`)
const anchorMonth = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)
const anchorYear = ref(now.getFullYear())

const currentPage = ref(1)
const pageSize = 20

const loadingAccess = computed(() => adminStore.loadingAccess)
const loadingDashboard = computed(() => adminStore.loadingDashboard)
const loadingTable = computed(() => adminStore.loadingTable)
const isAdmin = computed(() => adminStore.isAdmin)
const dashboard = computed(() => adminStore.dashboard)
const tableData = computed(() => adminStore.tableData)

const activeAnchor = computed(() => {
  if (rangeType.value === 'day') return anchorDay.value
  if (rangeType.value === 'month') return anchorMonth.value
  return String(anchorYear.value)
})

const trendMaxToken = computed(() => {
  const values = (dashboard.value?.trend || []).map((item) => Number(item.totalTokens || 0))
  return Math.max(1, ...values)
})
const taskTypeMaxToken = computed(() => {
  const values = (dashboard.value?.byTaskType || []).map((item) => Number(item.totalTokens || 0))
  return Math.max(1, ...values)
})

const formatNumber = (value: number) => {
  const numeric = Number(value || 0)
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(numeric)
}
const formatCost = (value: number) => {
  const numeric = Number(value || 0)
  return new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(numeric)
}
const trendBarWidth = (value: number) => {
  const numeric = Number(value || 0)
  return Math.max(0, Math.min(100, (numeric / trendMaxToken.value) * 100))
}
const taskTypeBarWidth = (value: number) => {
  const numeric = Number(value || 0)
  return Math.max(0, Math.min(100, (numeric / taskTypeMaxToken.value) * 100))
}
const rowKey = (row: Record<string, any>) => {
  return row.taskId || row.ownerId || `all-${row.totalTokens || 0}-${row.estimatedCostCny || 0}`
}

const refreshDashboard = async () => {
  await adminStore.fetchDashboard(rangeType.value, activeAnchor.value)
}
const refreshTable = async () => {
  await adminStore.fetchTable({
    rangeType: rangeType.value,
    anchor: activeAnchor.value,
    scope: scope.value,
    q: keyword.value,
    taskType: taskTypeFilter.value,
    status: statusFilter.value,
    model: modelFilter.value,
    page: currentPage.value,
    pageSize,
    sortBy: scope.value === 'task' ? 'lastUsageAt' : scope.value === 'user' ? 'totalTokens' : 'taskCount',
    sortOrder: 'desc',
  })
}
const refreshAll = async () => {
  currentPage.value = 1
  await Promise.all([refreshDashboard(), refreshTable()])
}

const onRangeTypeChange = () => {
  currentPage.value = 1
}

watch([scope, keyword, taskTypeFilter, statusFilter, modelFilter], () => {
  currentPage.value = 1
})

onMounted(async () => {
  await adminStore.fetchAccess(true)
  if (!adminStore.isAdmin) return
  await refreshAll()
})
</script>

<style scoped>
.metric-card {
  @apply rounded-2xl border border-slate-200 bg-white p-4 shadow-sm shadow-slate-100;
}

.metric-label {
  @apply text-xs font-medium text-slate-500;
}

.metric-value {
  @apply mt-1 text-xl font-semibold text-slate-900;
}

.field {
  @apply flex min-w-[10rem] flex-col gap-1 text-xs font-medium text-slate-600;
}

.field-input {
  @apply rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition focus:border-cyan-400 focus:ring-2 focus:ring-cyan-100;
}
</style>
