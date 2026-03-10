<template>
  <div class="space-y-4">
    <section class="rounded-3xl border border-slate-200 bg-white/92 p-4 shadow-sm shadow-slate-200 sm:p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p class="text-xs font-semibold tracking-[0.08em] text-slate-500">Admin Console</p>
          <h1 class="mt-1 text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">平台观测中心</h1>
          <p class="mt-2 text-xs leading-5 text-slate-600 sm:text-sm">支持 Token/费用统计与系统日志排障检索。</p>
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
        <div class="flex flex-wrap items-center gap-2">
          <button
            type="button"
            class="rounded-xl px-3 py-2 text-sm font-semibold transition"
            :class="activeTab === 'usage' ? 'bg-cyan-700 text-white' : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50'"
            @click="activeTab = 'usage'"
          >
            Token与费用统计
          </button>
          <button
            type="button"
            class="rounded-xl px-3 py-2 text-sm font-semibold transition"
            :class="activeTab === 'logs' ? 'bg-cyan-700 text-white' : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50'"
            @click="activeTab = 'logs'"
          >
            系统日志
          </button>
        </div>
      </section>

      <template v-if="activeTab === 'usage'">
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
              @click="refreshUsageAll"
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
              <input v-model.trim="modelFilter" type="text" placeholder="qwen3.5-flash" class="field-input" />
            </label>
            <button
              type="button"
              class="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
              :disabled="loadingTable"
              @click="refreshUsageTable"
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
              :disabled="scope === 'all' || usageCurrentPage <= 1 || loadingTable"
              @click="usageCurrentPage -= 1; refreshUsageTable()"
            >
              上一页
            </button>
            <span>第 {{ usageCurrentPage }} 页 · 共 {{ tableData?.total ?? 0 }} 条</span>
            <button
              type="button"
              class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
              :disabled="scope === 'all' || ((tableData?.items?.length || 0) < pageSize) || loadingTable"
              @click="usageCurrentPage += 1; refreshUsageTable()"
            >
              下一页
            </button>
          </div>
        </section>
      </template>

      <template v-else>
        <section class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
          <div class="grid gap-3 md:grid-cols-4 md:items-end">
            <label class="field">
              <span>开始时间</span>
              <input v-model="logDateFrom" type="datetime-local" class="field-input" />
            </label>
            <label class="field">
              <span>结束时间</span>
              <input v-model="logDateTo" type="datetime-local" class="field-input" />
            </label>
            <label class="field">
              <span>分类</span>
              <select v-model="logCategory" class="field-input">
                <option value="">全部</option>
                <option value="user_action">user_action</option>
                <option value="task_execution">task_execution</option>
                <option value="external_api">external_api</option>
                <option value="llm_call">llm_call</option>
              </select>
            </label>
            <label class="field">
              <span>结果</span>
              <select v-model="logSuccess" class="field-input">
                <option value="">全部</option>
                <option value="true">成功</option>
                <option value="false">失败</option>
              </select>
            </label>
          </div>

          <div class="mt-3 grid gap-3 md:grid-cols-4 md:items-end">
            <label class="field">
              <span>provider</span>
              <input v-model.trim="logProvider" type="text" class="field-input" placeholder="llm / zhihuiya" />
            </label>
            <label class="field">
              <span>taskId</span>
              <input v-model.trim="logTaskId" type="text" class="field-input" placeholder="task_id" />
            </label>
            <label class="field">
              <span>ownerId</span>
              <input v-model.trim="logOwnerId" type="text" class="field-input" placeholder="authing:xxx" />
            </label>
            <label class="field">
              <span>requestId</span>
              <input v-model.trim="logRequestId" type="text" class="field-input" placeholder="request_id" />
            </label>
          </div>

          <div class="mt-3 flex flex-wrap items-end gap-2">
            <label class="field min-w-[18rem]">
              <span>关键词</span>
              <input v-model.trim="logKeyword" type="text" class="field-input" placeholder="event/message/path/provider" />
            </label>
            <button
              type="button"
              class="rounded-xl bg-cyan-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-cyan-800"
              :disabled="loadingSystemLogs || loadingSystemSummary"
              @click="refreshSystemLogsAll"
            >
              {{ loadingSystemLogs || loadingSystemSummary ? '检索中...' : '检索日志' }}
            </button>
          </div>
        </section>

        <section class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <article class="metric-card">
            <p class="metric-label">日志总数</p>
            <p class="metric-value">{{ formatNumber(systemLogSummary?.totalLogs ?? 0) }}</p>
          </article>
          <article class="metric-card">
            <p class="metric-label">失败日志数</p>
            <p class="metric-value">{{ formatNumber(systemLogSummary?.failedLogs ?? 0) }}</p>
          </article>
          <article class="metric-card">
            <p class="metric-label">失败率</p>
            <p class="metric-value">{{ formatPercent(systemLogSummary?.failedRate ?? 0) }}</p>
          </article>
          <article class="metric-card">
            <p class="metric-label">LLM 调用日志</p>
            <p class="metric-value">{{ formatNumber(systemLogSummary?.llmCallCount ?? 0) }}</p>
          </article>
        </section>

        <section class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
          <div class="overflow-x-auto rounded-xl border border-slate-200">
            <table class="min-w-full divide-y divide-slate-200 text-xs">
              <thead class="bg-slate-50 text-slate-600">
                <tr>
                  <th class="px-2.5 py-2 text-left font-semibold">时间</th>
                  <th class="px-2.5 py-2 text-left font-semibold">分类/事件</th>
                  <th class="px-2.5 py-2 text-left font-semibold">用户/任务</th>
                  <th class="px-2.5 py-2 text-left font-semibold">接口</th>
                  <th class="px-2.5 py-2 text-right font-semibold">状态</th>
                  <th class="px-2.5 py-2 text-right font-semibold">耗时</th>
                  <th class="px-2.5 py-2 text-left font-semibold">摘要</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100 bg-white text-slate-700">
                <tr
                  v-for="row in systemLogs?.items || []"
                  :key="row.logId"
                  class="cursor-pointer hover:bg-slate-50"
                  @click="openLogDetail(row.logId)"
                >
                  <td class="px-2.5 py-2 whitespace-nowrap">{{ row.timestamp }}</td>
                  <td class="px-2.5 py-2">
                    <p class="font-semibold">{{ row.category }}</p>
                    <p class="text-slate-500">{{ row.eventName }}</p>
                  </td>
                  <td class="px-2.5 py-2">
                    <p class="font-mono text-[11px]">{{ row.ownerId || '-' }}</p>
                    <p class="font-mono text-[11px] text-slate-500">{{ row.taskId || '-' }}</p>
                  </td>
                  <td class="px-2.5 py-2">
                    <p>{{ row.method || '-' }} {{ row.path || '-' }}</p>
                    <p class="text-slate-500">{{ row.provider || row.targetHost || '-' }}</p>
                  </td>
                  <td class="px-2.5 py-2 text-right">
                    <span :class="row.success ? 'text-emerald-700' : 'text-rose-700'">{{ row.success ? '成功' : '失败' }}</span>
                  </td>
                  <td class="px-2.5 py-2 text-right">{{ row.durationMs ?? '-' }}</td>
                  <td class="px-2.5 py-2 max-w-[24rem] truncate">{{ row.message || '-' }}</td>
                </tr>
                <tr v-if="!(systemLogs?.items?.length)">
                  <td colspan="7" class="px-2.5 py-8 text-center text-slate-500">当前筛选条件下暂无日志</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="mt-3 flex items-center justify-end gap-2 text-xs text-slate-600">
            <button
              type="button"
              class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
              :disabled="logCurrentPage <= 1 || loadingSystemLogs"
              @click="logCurrentPage -= 1; refreshSystemLogs()"
            >
              上一页
            </button>
            <span>第 {{ logCurrentPage }} 页 · 共 {{ systemLogs?.total ?? 0 }} 条</span>
            <button
              type="button"
              class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
              :disabled="((systemLogs?.items?.length || 0) < pageSize) || loadingSystemLogs"
              @click="logCurrentPage += 1; refreshSystemLogs()"
            >
              下一页
            </button>
          </div>
        </section>

        <section v-if="systemLogDetail" class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
          <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 class="text-sm font-semibold text-slate-900">日志详情：{{ systemLogDetail.item.logId }}</h3>
            <div class="flex items-center gap-2">
              <button
                type="button"
                class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 transition hover:bg-slate-50"
                @click="copyDetailPayload"
              >
                复制详情
              </button>
              <button
                type="button"
                class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 transition hover:bg-slate-50"
                @click="closeLogDetail"
              >
                关闭
              </button>
            </div>
          </div>
          <pre class="max-h-[28rem] overflow-auto rounded-xl border border-slate-200 bg-slate-50 p-3 text-[11px] leading-5 text-slate-800">{{ systemLogDetailText }}</pre>
        </section>
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useAdminUsageStore } from '~/stores/adminUsage'
import type { UsageRangeType, UsageScopeType } from '~/types/adminUsage'

const adminStore = useAdminUsageStore()

const activeTab = ref<'usage' | 'logs'>('usage')

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
const logDateFrom = ref(`${anchorDay.value}T00:00`)
const logDateTo = ref(`${anchorDay.value}T23:59`)

const usageCurrentPage = ref(1)
const logCurrentPage = ref(1)
const pageSize = 20

const logCategory = ref('')
const logProvider = ref('')
const logSuccess = ref('')
const logTaskId = ref('')
const logOwnerId = ref('')
const logRequestId = ref('')
const logTraceId = ref('')
const logKeyword = ref('')

const loadingAccess = computed(() => adminStore.loadingAccess)
const loadingDashboard = computed(() => adminStore.loadingDashboard)
const loadingTable = computed(() => adminStore.loadingTable)
const loadingSystemLogs = computed(() => adminStore.loadingSystemLogs)
const loadingSystemSummary = computed(() => adminStore.loadingSystemSummary)
const loadingSystemLogDetail = computed(() => adminStore.loadingSystemLogDetail)
const isAdmin = computed(() => adminStore.isAdmin)
const dashboard = computed(() => adminStore.dashboard)
const tableData = computed(() => adminStore.tableData)
const systemLogSummary = computed(() => adminStore.systemLogSummary)
const systemLogs = computed(() => adminStore.systemLogs)
const systemLogDetail = computed(() => adminStore.systemLogDetail)

const activeAnchor = computed(() => {
  if (rangeType.value === 'day') return anchorDay.value
  if (rangeType.value === 'month') return anchorMonth.value
  return String(anchorYear.value)
})

const formatNumber = (value: number) => {
  const numeric = Number(value || 0)
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(numeric)
}
const formatCost = (value: number) => {
  const numeric = Number(value || 0)
  return new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(numeric)
}
const formatPercent = (value: number) => {
  const numeric = Number(value || 0)
  return `${(numeric * 100).toFixed(2)}%`
}
const rowKey = (row: Record<string, any>) => {
  return row.taskId || row.ownerId || `all-${row.totalTokens || 0}-${row.estimatedCostCny || 0}`
}

const refreshUsageDashboard = async () => {
  await adminStore.fetchDashboard(rangeType.value, activeAnchor.value)
}
const refreshUsageTable = async () => {
  await adminStore.fetchTable({
    rangeType: rangeType.value,
    anchor: activeAnchor.value,
    scope: scope.value,
    q: keyword.value,
    taskType: taskTypeFilter.value,
    status: statusFilter.value,
    model: modelFilter.value,
    page: usageCurrentPage.value,
    pageSize,
    sortBy: scope.value === 'task' ? 'lastUsageAt' : scope.value === 'user' ? 'totalTokens' : 'taskCount',
    sortOrder: 'desc',
  })
}
const refreshUsageAll = async () => {
  usageCurrentPage.value = 1
  await Promise.all([refreshUsageDashboard(), refreshUsageTable()])
}

const refreshSystemLogs = async () => {
  await adminStore.fetchSystemLogs({
    category: logCategory.value,
    ownerId: logOwnerId.value,
    taskId: logTaskId.value,
    requestId: logRequestId.value,
    traceId: logTraceId.value,
    provider: logProvider.value,
    success: logSuccess.value,
    dateFrom: logDateFrom.value ? new Date(logDateFrom.value).toISOString() : undefined,
    dateTo: logDateTo.value ? new Date(logDateTo.value).toISOString() : undefined,
    q: logKeyword.value,
    page: logCurrentPage.value,
    pageSize,
  })
}

const refreshSystemSummary = async () => {
  await adminStore.fetchSystemLogSummary(
    logDateFrom.value ? new Date(logDateFrom.value).toISOString() : undefined,
    logDateTo.value ? new Date(logDateTo.value).toISOString() : undefined,
  )
}

const refreshSystemLogsAll = async () => {
  logCurrentPage.value = 1
  await Promise.all([refreshSystemSummary(), refreshSystemLogs()])
}

const onRangeTypeChange = () => {
  usageCurrentPage.value = 1
}

const openLogDetail = async (logId: string) => {
  await adminStore.fetchSystemLogDetail(logId)
}

const closeLogDetail = () => {
  adminStore.clearSystemLogDetail()
}

const systemLogDetailText = computed(() => {
  const detail = systemLogDetail.value
  if (!detail) return ''
  return JSON.stringify(detail, null, 2)
})

const copyDetailPayload = async () => {
  const text = systemLogDetailText.value
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch (_error) {
    // ignore
  }
}

watch([scope, keyword, taskTypeFilter, statusFilter, modelFilter], () => {
  usageCurrentPage.value = 1
})

watch([logCategory, logProvider, logSuccess, logTaskId, logOwnerId, logRequestId, logTraceId, logKeyword], () => {
  logCurrentPage.value = 1
})

watch(activeTab, async (value) => {
  if (value === 'usage') {
    if (!dashboard.value || !tableData.value) {
      await refreshUsageAll()
    }
    return
  }
  if (!systemLogSummary.value || !systemLogs.value) {
    await refreshSystemLogsAll()
  }
})

onMounted(async () => {
  await adminStore.fetchAccess(true)
  if (!adminStore.isAdmin) return
  await refreshUsageAll()
  await refreshSystemLogsAll()
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
