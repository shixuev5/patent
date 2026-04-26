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
          <button
            type="button"
            class="rounded-xl px-3 py-2 text-sm font-semibold transition"
            :class="activeTab === 'users' ? 'bg-cyan-700 text-white' : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50'"
            @click="activeTab = 'users'"
          >
            用户列表
          </button>
          <button
            type="button"
            class="rounded-xl px-3 py-2 text-sm font-semibold transition"
            :class="activeTab === 'tasks' ? 'bg-cyan-700 text-white' : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50'"
            @click="activeTab = 'tasks'"
          >
            任务列表
          </button>
        </div>
      </section>

      <template v-if="activeTab === 'usage'">
        <section class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
          <div class="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-3 py-3">
            <div class="space-y-1 text-xs text-slate-600">
              <p>
                价格缓存：
                <span class="font-semibold text-slate-900">{{ pricingStatus?.hasUsableCache ? `${pricingStatus.entryCount} 条` : '未就绪' }}</span>
                <span v-if="pricingStatus?.isExpired" class="ml-2 text-amber-700">已过期，当前使用旧缓存估算</span>
              </p>
              <p>
                最近成功同步：{{ formatDateTime(pricingStatus?.lastSuccessAt) }}
                <span class="mx-1">·</span>
                下次过期：{{ formatDateTime(pricingStatus?.expiresAt) }}
              </p>
              <p v-if="pricingStatus?.errorMessage" class="text-amber-700">
                最近同步异常：{{ pricingStatus.errorMessage }}
              </p>
            </div>
            <button
              type="button"
              class="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
              :disabled="loadingPricingRefresh"
              @click="refreshPricingStatusNow"
            >
              {{ loadingPricingRefresh ? '刷新中...' : '刷新价格' }}
            </button>
          </div>

          <div class="mb-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              class="rounded-xl px-3 py-2 text-sm font-semibold transition"
              :class="usageScope === 'user' ? 'bg-cyan-700 text-white' : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50'"
              @click="usageScope = 'user'"
            >
              用户消耗统计
            </button>
            <button
              type="button"
              class="rounded-xl px-3 py-2 text-sm font-semibold transition"
              :class="usageScope === 'task' ? 'bg-cyan-700 text-white' : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50'"
              @click="usageScope = 'task'"
            >
              任务明细流水
            </button>
          </div>

          <div
            v-if="usageScope === 'user'"
            class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 xl:items-end"
          >
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
              <span>关键词</span>
              <input
                v-model.trim="keyword"
                type="text"
                placeholder="用户ID / 用户名称"
                class="field-input"
              />
            </label>
            <button
              type="button"
              class="query-btn query-action"
              :disabled="loadingTable"
              @click="refreshUsageAll"
            >
              {{ loadingTable ? '查询中...' : '查询' }}
            </button>
          </div>

          <div
            v-else
            class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 xl:items-end"
          >
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
              <span>任务类型</span>
              <select v-model="taskTypeFilter" class="field-input">
                <option value="">全部</option>
                <option value="patent_analysis">AI 分析</option>
                <option value="ai_review">AI 审查</option>
                <option value="ai_reply">AI 答复</option>
                <option value="ai_search">AI 检索</option>
              </select>
            </label>
            <label class="field">
              <span>状态</span>
              <select v-model="statusFilter" class="field-input">
                <option value="">全部</option>
                <option value="completed">已完成</option>
                <option value="failed">失败</option>
                <option value="cancelled">已取消</option>
              </select>
            </label>
            <label class="field">
              <span>模型</span>
              <input v-model.trim="modelFilter" type="text" placeholder="例如：qwen3.5-flash" class="field-input" />
            </label>
            <label class="field">
              <span>关键词</span>
              <input
                v-model.trim="keyword"
                type="text"
                placeholder="任务ID / 用户ID / 用户名称"
                class="field-input"
              />
            </label>
            <button
              type="button"
              class="query-btn query-action"
              :disabled="loadingTable"
              @click="refreshUsageAll"
            >
              {{ loadingTable ? '查询中...' : '查询' }}
            </button>
          </div>
          <p v-if="tableData?.priceMissing" class="mt-3 text-xs text-amber-700">
            存在未命中价格缓存的模型，相关费用为预估值，部分条目可能未计入。
          </p>
        </section>

        <section class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <article class="metric-card">
            <p class="metric-label">总任务数</p>
            <p class="metric-value">{{ usageSummary.totalTasks }}</p>
          </article>
          <article class="metric-card">
            <p class="metric-label">总用户数</p>
            <p class="metric-value">{{ usageSummary.totalUsers }}</p>
          </article>
          <article class="metric-card">
            <p class="metric-label">总 Token</p>
            <p class="metric-value">{{ formatNumber(usageSummary.totalTokens) }}</p>
          </article>
          <article class="metric-card">
            <p class="metric-label">总费用（{{ tableData?.currency || 'CNY' }}）</p>
            <p class="metric-value">{{ formatCost(usageSummary.totalEstimatedCostCny) }}</p>
          </article>
          <article class="metric-card">
            <p class="metric-label">{{ usageScope === 'user' ? '平均 Token/用户' : '平均 Token/任务' }}</p>
            <p class="metric-value">{{ formatNumber(usageSummary.avgTokensPerEntity) }}</p>
          </article>
          <article class="metric-card">
            <p class="metric-label">{{ usageScope === 'user' ? '平均费用/用户' : '平均费用/任务' }}</p>
            <p class="metric-value">{{ formatCost(usageSummary.avgCostPerEntityCny) }}</p>
          </article>
        </section>

        <section class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
          <div class="overflow-x-auto rounded-xl border border-slate-200">
            <table class="admin-table-fixed min-w-[66rem] divide-y divide-slate-200 text-xs">
              <colgroup v-if="usageScope === 'user'">
                <col class="w-[11rem]" />
                <col class="w-[8rem]" />
                <col class="w-[7rem]" />
                <col class="w-[7.5rem]" />
                <col class="w-[7rem]" />
                <col class="w-[7.5rem]" />
                <col class="w-[9.5rem]" />
              </colgroup>
              <colgroup v-else>
                <col class="w-[8.5rem]" />
                <col class="w-[6rem]" />
                <col class="w-[6.5rem]" />
                <col class="w-[6.5rem]" />
                <col class="w-[10.5rem]" />
                <col class="w-[7rem]" />
                <col class="w-[7.5rem]" />
                <col class="w-[9.5rem]" />
              </colgroup>
              <thead class="bg-slate-50 text-slate-600">
                <tr v-if="usageScope === 'user'">
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">用户ID(ownerId)</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">用户名称</th>
                  <th class="px-2.5 py-2 text-right font-semibold whitespace-nowrap">发起任务总数</th>
                  <th class="px-2.5 py-2 text-right font-semibold whitespace-nowrap">总消耗Token</th>
                  <th class="px-2.5 py-2 text-right font-semibold whitespace-nowrap">总调用次数</th>
                  <th class="px-2.5 py-2 text-right font-semibold whitespace-nowrap">总费用(CNY)</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">最近使用时间</th>
                </tr>
                <tr v-else>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">任务ID</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">所属用户</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">任务类型</th>
                  <th class="px-2.5 py-2 text-center font-semibold whitespace-nowrap">状态</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">使用模型</th>
                  <th class="px-2.5 py-2 text-right font-semibold whitespace-nowrap">消耗Token</th>
                  <th class="px-2.5 py-2 text-right font-semibold whitespace-nowrap">费用(CNY)</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">完成时间</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100 bg-white text-slate-700">
                <tr v-for="row in usageItems" :key="rowKey(row)">
                  <template v-if="usageScope === 'user'">
                    <td class="px-2.5 py-2 font-mono text-[11px]">
                      <p class="truncate">{{ row.ownerId || '-' }}</p>
                    </td>
                    <td class="px-2.5 py-2">
                      <p class="truncate">{{ row.userName || '未知用户' }}</p>
                    </td>
                    <td class="px-2.5 py-2 text-right">{{ formatNumber(row.taskCount || 0) }}</td>
                    <td class="px-2.5 py-2 text-right">{{ formatNumber(row.totalTokens || 0) }}</td>
                    <td class="px-2.5 py-2 text-right">{{ formatNumber(row.llmCallCount || 0) }}</td>
                    <td class="px-2.5 py-2 text-right">{{ formatCost(row.estimatedCostCny || 0) }}</td>
                    <td class="px-2.5 py-2 whitespace-nowrap">{{ formatDateTime(row.latestUsageAt) }}</td>
                  </template>
                  <template v-else>
                    <td class="px-2.5 py-2 font-mono text-[11px]">
                      <p class="truncate">{{ row.taskId || '-' }}</p>
                    </td>
                    <td class="px-2.5 py-2">
                      <p class="truncate">{{ row.userName || '未知用户' }}</p>
                      <p class="truncate text-slate-500">{{ row.ownerId || '-' }}</p>
                    </td>
                    <td class="px-2.5 py-2 whitespace-nowrap">{{ formatTaskTypeLabel(row.taskType) }}</td>
                    <td class="px-2.5 py-2 text-center whitespace-nowrap">
                      <span :class="String(row.taskStatus || '').toLowerCase() === 'failed' ? 'text-rose-600' : ''">{{ formatTaskStatusLabel(row.taskStatus) }}</span>
                    </td>
                    <td class="px-2.5 py-2">
                      <p class="truncate">{{ row.models?.length ? row.models.join(', ') : '-' }}</p>
                    </td>
                    <td class="px-2.5 py-2 text-right">{{ formatNumber(row.totalTokens || 0) }}</td>
                    <td class="px-2.5 py-2 text-right">{{ formatCost(row.estimatedCostCny || 0) }}</td>
                    <td class="px-2.5 py-2 whitespace-nowrap">{{ formatDateTime(row.lastUsageAt) }}</td>
                  </template>
                </tr>
                <tr v-if="!usageItems.length">
                  <td :colspan="usageScope === 'task' ? 8 : 7" class="px-2.5 py-8 text-center text-slate-500">当前筛选条件下暂无数据</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="mt-3 flex items-center justify-end gap-2 text-xs text-slate-600">
            <button
              type="button"
              class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
              :disabled="usageCurrentPage <= 1 || loadingTable"
              @click="usageCurrentPage -= 1; refreshUsageTable()"
            >
              上一页
            </button>
            <span>第 {{ usageCurrentPage }} 页 · 共 {{ tableData?.total ?? 0 }} 条</span>
            <button
              type="button"
              class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
              :disabled="(usageItems.length < pageSize) || loadingTable"
              @click="usageCurrentPage += 1; refreshUsageTable()"
            >
              下一页
            </button>
          </div>
        </section>
      </template>

      <template v-else-if="activeTab === 'logs'">
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
                <option value="user_action">用户行为</option>
                <option value="task_execution">任务执行</option>
                <option value="external_api">外部接口</option>
                <option value="llm_call">LLM 调用</option>
              </select>
            </label>
            <label class="field">
              <span>状态</span>
              <select v-model="logSuccess" class="field-input">
                <option value="">全部</option>
                <option value="true">成功</option>
                <option value="false">失败</option>
              </select>
            </label>
          </div>

          <div class="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4 xl:items-end">
            <label class="field">
              <span>服务提供方</span>
              <select v-model="logProvider" class="field-input">
                <option value="">全部</option>
                <option value="llm">LLM</option>
                <option value="zhihuiya">智慧芽</option>
                <option value="mineru">MinerU</option>
                <option value="authing">Authing</option>
                <option value="cloudflare">Cloudflare</option>
                <option value="openalex">OpenAlex</option>
                <option value="tavily">Tavily</option>
                <option value="unknown">未知</option>
              </select>
            </label>
            <label class="field">
              <span>任务ID</span>
              <input v-model.trim="logTaskId" type="text" class="field-input" placeholder="请输入任务ID" />
            </label>
            <label class="field">
              <span>用户</span>
              <input v-model.trim="logUserName" type="text" class="field-input" placeholder="按用户名称检索" />
            </label>
            <label class="field">
              <span>请求ID</span>
              <input v-model.trim="logRequestId" type="text" class="field-input" placeholder="请输入请求ID" />
            </label>
            <label class="field">
              <span>关键词</span>
              <input v-model.trim="logKeyword" type="text" class="field-input" placeholder="按事件/消息/路径/服务方检索" />
            </label>
            <button
              type="button"
              class="query-btn query-action"
              :disabled="loadingSystemLogs || loadingSystemSummary"
              @click="refreshSystemLogsAll"
            >
              {{ loadingSystemLogs || loadingSystemSummary ? '查询中...' : '查询' }}
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
            <table class="admin-log-table min-w-[82rem] divide-y divide-slate-200 text-xs">
              <colgroup>
                <col class="w-[10rem]" />
                <col class="w-[8rem]" />
                <col class="w-[8rem]" />
                <col class="w-[18rem]" />
                <col class="w-[5rem]" />
                <col class="w-[5rem]" />
                <col class="w-[18rem]" />
                <col class="w-[5rem]" />
              </colgroup>
              <thead class="bg-slate-50 text-slate-600">
                <tr>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">时间</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">分类/事件</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">用户/任务</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">接口</th>
                  <th class="px-2.5 py-2 text-center font-semibold whitespace-nowrap">状态</th>
                  <th class="px-2.5 py-2 text-right font-semibold whitespace-nowrap">耗时(秒)</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">摘要</th>
                  <th class="sticky right-0 z-10 border-l border-slate-200 bg-slate-100/90 px-2.5 py-2 text-left font-semibold whitespace-nowrap">操作</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100 bg-white text-slate-700">
                <tr
                  v-for="row in systemLogs?.items || []"
                  :key="row.logId"
                  class="hover:bg-slate-50"
                >
                  <td class="px-2.5 py-2 whitespace-nowrap">{{ formatDateTime(row.timestamp) }}</td>
                  <td class="px-2.5 py-2">
                    <p class="font-semibold">{{ formatLogCategory(row.category) }}</p>
                    <p class="text-slate-500">{{ formatLogEvent(row.eventName) }}</p>
                  </td>
                  <td class="px-2.5 py-2">
                    <p>{{ row.userName || '未知用户' }}</p>
                    <p class="font-mono text-[11px] text-slate-500">{{ row.taskId || '-' }}</p>
                  </td>
                  <td class="px-2.5 py-2">
                    <p class="max-w-[16rem] truncate">{{ formatLogInterface(row) }}</p>
                    <p class="max-w-[16rem] truncate text-slate-500">{{ row.provider || row.targetHost || '-' }}</p>
                  </td>
                  <td class="px-2.5 py-2 text-center">
                    <span :class="row.success ? 'text-emerald-700' : 'text-rose-700'">{{ row.success ? '成功' : '失败' }}</span>
                  </td>
                  <td class="px-2.5 py-2 text-right">{{ formatLogDurationSeconds(row.durationMs) }}</td>
                  <td class="px-2.5 py-2">
                    <p class="max-w-[16rem] truncate">{{ row.message || '-' }}</p>
                  </td>
                  <td class="sticky right-0 border-l border-slate-200 bg-slate-50/90 px-2.5 py-2">
                    <button
                      type="button"
                      class="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
                      :disabled="loadingSystemLogDetail && detailLoadingLogId === row.logId"
                      @click="openLogDetail(row.logId)"
                    >
                      {{ loadingSystemLogDetail && detailLoadingLogId === row.logId ? '加载中' : '查看' }}
                    </button>
                  </td>
                </tr>
                <tr v-if="!(systemLogs?.items?.length)">
                  <td colspan="8" class="px-2.5 py-8 text-center text-slate-500">当前筛选条件下暂无日志</td>
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

      </template>

      <template v-else-if="activeTab === 'users'">
        <section class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <article class="metric-card">
            <p class="metric-label">总用户数</p>
            <p class="metric-value">{{ formatNumber(userStats.totalUsers) }}</p>
          </article>
          <article class="metric-card">
            <p class="metric-label">注册用户数</p>
            <p class="metric-value">{{ formatNumber(userStats.registeredUsers) }}</p>
          </article>
          <article class="metric-card">
            <p class="metric-label">新增用户数（7天）</p>
            <p class="metric-value">{{ formatNumber(userStats.newUsers7d) }}</p>
          </article>
          <article class="metric-card">
            <p class="metric-label">活跃用户数（近30天）</p>
            <p class="metric-value">{{ formatNumber(userStats.activeUsers30d) }}</p>
          </article>
        </section>

        <section class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
          <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 xl:items-end">
            <label class="field">
              <span>角色</span>
              <select v-model="entityUserRole" class="field-input">
                <option value="">全部</option>
                <option value="admin">管理员</option>
                <option value="member">成员</option>
                <option value="guest">访客</option>
              </select>
            </label>
            <label class="field">
              <span>关键词</span>
              <input
                v-model.trim="entityUserKeyword"
                type="text"
                class="field-input"
                placeholder="按用户名称或邮箱检索"
              />
            </label>
            <button
              type="button"
              class="query-btn query-action"
              :disabled="loadingEntityUsers"
              @click="refreshEntityUsersAll"
            >
              {{ loadingEntityUsers ? '查询中...' : '查询' }}
            </button>
          </div>
        </section>

        <section class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
          <div class="overflow-x-auto rounded-xl border border-slate-200">
            <table class="admin-table-fixed min-w-[72rem] divide-y divide-slate-200 text-xs">
              <colgroup>
                <col class="w-[8rem]" />
                <col class="w-[8rem]" />
                <col class="w-[4rem]" />
                <col class="w-[4rem]" />
                <col class="w-[10rem]" />
                <col class="w-[10rem]" />
                <col class="w-[4rem]" />
              </colgroup>
              <thead class="bg-slate-50 text-slate-600">
                <tr>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">用户</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">邮箱</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">角色</th>
                  <th class="px-2.5 py-2 text-right font-semibold whitespace-nowrap">任务数</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">创建时间</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">最近任务时间</th>
                  <th class="sticky right-0 z-10 border-l border-slate-200 bg-slate-100/90 px-2.5 py-2 text-left font-semibold whitespace-nowrap">操作</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100 bg-white text-slate-700">
                <tr
                  v-for="row in entityUsers?.items || []"
                  :key="row.ownerId"
                  class="hover:bg-slate-50"
                >
                  <td class="px-2.5 py-2">
                    <p class="truncate">{{ row.userName || '未命名用户' }}</p>
                  </td>
                  <td class="px-2.5 py-2">
                    <p class="truncate">{{ row.email || '-' }}</p>
                  </td>
                  <td class="px-2.5 py-2 whitespace-nowrap">{{ formatUserRoleLabel(row.role) }}</td>
                  <td class="px-2.5 py-2 text-right">{{ formatNumber(row.taskCount || 0) }}</td>
                  <td class="px-2.5 py-2 whitespace-nowrap">{{ formatDateTime(row.createdAt) }}</td>
                  <td class="px-2.5 py-2 whitespace-nowrap">{{ formatDateTime(row.latestTaskAt || row.createdAt) }}</td>
                  <td class="sticky right-0 w-[4.75rem] min-w-[4.75rem] border-l border-slate-200 bg-slate-50/90 px-1 py-2 sm:w-[5.75rem] sm:min-w-[5.75rem]">
                    <div class="flex flex-nowrap items-center gap-1">
                      <button
                        type="button"
                        class="table-action-btn"
                        :disabled="!row.userName"
                        @click="openTasksByUser(row.userName)"
                      >
                        任务
                      </button>
                      <button
                        type="button"
                        class="table-action-btn"
                        :disabled="!row.userName"
                        @click="openLogsByUser(row.userName)"
                      >
                        日志
                      </button>
                    </div>
                  </td>
                </tr>
                <tr v-if="!(entityUsers?.items?.length)">
                  <td colspan="7" class="px-2.5 py-8 text-center text-slate-500">当前筛选条件下暂无用户</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="mt-3 flex items-center justify-end gap-2 text-xs text-slate-600">
            <button
              type="button"
              class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
              :disabled="entityUserCurrentPage <= 1 || loadingEntityUsers"
              @click="entityUserCurrentPage -= 1; refreshEntityUsers()"
            >
              上一页
            </button>
            <span>第 {{ entityUserCurrentPage }} 页 · 共 {{ entityUsers?.total ?? 0 }} 条</span>
            <button
              type="button"
              class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
              :disabled="((entityUsers?.items?.length || 0) < pageSize) || loadingEntityUsers"
              @click="entityUserCurrentPage += 1; refreshEntityUsers()"
            >
              下一页
            </button>
          </div>
        </section>
      </template>

      <template v-else-if="activeTab === 'tasks'">
        <section>
          <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <article
              v-for="item in taskTypeWindows"
              :key="item.taskType"
              class="metric-card"
            >
              <p class="metric-label">{{ formatTaskTypeLabel(item.taskType) }}</p>
              <div class="mt-2 grid grid-cols-3 gap-2 text-xs text-slate-600">
                <div>
                  <p class="text-slate-500">1天</p>
                  <p class="mt-1 text-xl font-semibold text-slate-900">{{ formatNumber(item.count1d) }}</p>
                </div>
                <div>
                  <p class="text-slate-500">7天</p>
                  <p class="mt-1 text-xl font-semibold text-slate-900">{{ formatNumber(item.count7d) }}</p>
                </div>
                <div>
                  <p class="text-slate-500">30天</p>
                  <p class="mt-1 text-xl font-semibold text-slate-900">{{ formatNumber(item.count30d) }}</p>
                </div>
              </div>
            </article>
            <article
              v-if="!taskTypeWindows.length"
              class="rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500 sm:col-span-2 xl:col-span-3"
            >
              暂无任务类型统计数据
            </article>
          </div>
        </section>

        <section class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
          <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 xl:items-end">
            <label class="field">
              <span>开始日期</span>
              <input v-model="entityTaskDateFrom" type="date" class="field-input" />
            </label>
            <label class="field">
              <span>结束日期</span>
              <input v-model="entityTaskDateTo" type="date" class="field-input" />
            </label>
            <label class="field">
              <span>类型</span>
              <select v-model="entityTaskType" class="field-input">
                <option value="">全部</option>
                <option value="patent_analysis">AI 分析</option>
                <option value="ai_review">AI 审查</option>
                <option value="ai_reply">AI 答复</option>
                <option value="ai_search">AI 检索</option>
              </select>
            </label>
            <label class="field">
              <span>状态</span>
              <select v-model="entityTaskStatus" class="field-input">
                <option value="">全部</option>
                <option value="pending">待处理</option>
                <option value="processing">处理中</option>
                <option value="completed">已完成</option>
                <option value="failed">失败</option>
                <option value="cancelled">已取消</option>
                <option value="paused">暂停</option>
              </select>
            </label>
            <label class="field">
              <span>用户</span>
              <input
                v-model.trim="entityTaskUserName"
                type="text"
                class="field-input"
                placeholder="请输入用户名称"
              />
            </label>
            <label class="field">
              <span>关键词</span>
              <input
                v-model.trim="entityTaskKeyword"
                type="text"
                class="field-input"
                placeholder="按任务ID、标题或PN检索"
              />
            </label>
            <button
              type="button"
              class="query-btn query-action"
              :disabled="loadingEntityTasks"
              @click="refreshEntityTasksAll"
            >
              {{ loadingEntityTasks ? '查询中...' : '查询' }}
            </button>
          </div>
        </section>

        <section class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 sm:p-5">
          <div class="overflow-x-auto rounded-xl border border-slate-200">
            <table class="admin-table-fixed min-w-[72rem] divide-y divide-slate-200 text-xs">
              <colgroup>
                <col class="w-[4rem]" />
                <col class="w-[6rem]" />
                <col class="w-[8rem]" />
                <col class="w-[6rem]" />
                <col class="w-[4rem]" />
                <col class="w-[4rem]" />
                <col class="w-[6rem]" />
                <col class="w-[4rem]" />
              </colgroup>
              <thead class="bg-slate-50 text-slate-600">
                <tr>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">任务ID</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">任务名称</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">用户</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">类型</th>
                  <th class="px-2.5 py-2 text-center font-semibold whitespace-nowrap">状态</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">任务耗时</th>
                  <th class="px-2.5 py-2 text-left font-semibold whitespace-nowrap">创建时间</th>
                  <th class="sticky right-0 z-10 border-l border-slate-200 bg-slate-100/90 px-2.5 py-2 text-left font-semibold whitespace-nowrap">操作</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-100 bg-white text-slate-700">
                <tr
                  v-for="row in entityTasks?.items || []"
                  :key="row.taskId"
                  class="hover:bg-slate-50"
                >
                  <td class="px-2.5 py-2 font-mono text-[11px]">
                    <p class="truncate">{{ row.taskId }}</p>
                  </td>
                  <td class="px-2.5 py-2">
                    <p class="truncate">{{ row.title || '-' }}</p>
                  </td>
                  <td class="px-2.5 py-2">
                    <p class="truncate">{{ row.userName || '未知用户' }}</p>
                  </td>
                  <td class="px-2.5 py-2 whitespace-nowrap">{{ formatTaskTypeLabel(row.taskType) }}</td>
                  <td class="px-2.5 py-2 text-center whitespace-nowrap">
                    <span :class="String(row.status || '').toLowerCase() === 'failed' ? 'text-rose-600' : ''">{{ formatTaskStatusLabel(row.status) }}</span>
                  </td>
                  <td class="px-2.5 py-2 whitespace-nowrap">{{ formatDuration(row.durationSeconds) }}</td>
                  <td class="px-2.5 py-2 whitespace-nowrap">{{ formatDateTime(row.createdAt) }}</td>
                  <td class="sticky right-0 w-[7rem] min-w-[7rem] border-l border-slate-200 bg-slate-50/90 px-1 py-2 sm:w-[8.5rem] sm:min-w-[8.5rem]">
                    <div class="flex flex-nowrap items-center gap-1">
                      <button
                        type="button"
                        class="table-action-btn"
                        :disabled="loadingEntityTaskDetail && detailLoadingTaskId === row.taskId"
                        @click="openEntityTaskDetail(row.taskId)"
                      >
                        {{ loadingEntityTaskDetail && detailLoadingTaskId === row.taskId ? '加载' : '详情' }}
                      </button>
                      <button
                        type="button"
                        class="table-action-btn"
                        :disabled="!canDownloadEntityTask(row) || downloadingEntityTaskIds.has(row.taskId)"
                        @click="downloadEntityTask(row)"
                      >
                        {{ downloadingEntityTaskIds.has(row.taskId) ? '下载中' : '下载' }}
                      </button>
                      <button
                        type="button"
                        class="table-action-btn"
                        @click="openLogsByTask(row.taskId)"
                      >
                        日志
                      </button>
                    </div>
                  </td>
                </tr>
                <tr v-if="!(entityTasks?.items?.length)">
                  <td colspan="8" class="px-2.5 py-8 text-center text-slate-500">当前筛选条件下暂无任务</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="mt-3 flex items-center justify-end gap-2 text-xs text-slate-600">
            <button
              type="button"
              class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
              :disabled="entityTaskCurrentPage <= 1 || loadingEntityTasks"
              @click="entityTaskCurrentPage -= 1; refreshEntityTasks()"
            >
              上一页
            </button>
            <span>第 {{ entityTaskCurrentPage }} 页 · 共 {{ entityTasks?.total ?? 0 }} 条</span>
            <button
              type="button"
              class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
              :disabled="((entityTasks?.items?.length || 0) < pageSize) || loadingEntityTasks"
              @click="entityTaskCurrentPage += 1; refreshEntityTasks()"
            >
              下一页
            </button>
          </div>
        </section>
      </template>
    </template>

    <div v-if="systemLogDetail" class="modal-overlay" @click="closeLogDetail">
      <div class="modal-panel" @click.stop>
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
        <pre class="max-h-[70vh] overflow-auto rounded-xl border border-slate-200 bg-slate-50 p-3 text-[11px] leading-5 text-slate-800">{{ systemLogDetailText }}</pre>
      </div>
    </div>

    <div v-if="entityTaskDetail" class="modal-overlay" @click="closeEntityTaskDetail">
      <div class="modal-panel" @click.stop>
        <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 class="text-sm font-semibold text-slate-900">任务详情：{{ entityTaskDetail.item.taskId }}</h3>
          <div class="flex items-center gap-2">
            <button
              type="button"
              class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 transition hover:bg-slate-50"
              @click="copyEntityTaskDetailPayload"
            >
              复制详情
            </button>
            <button
              type="button"
              class="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 transition hover:bg-slate-50"
              @click="closeEntityTaskDetail"
            >
              关闭
            </button>
          </div>
        </div>
        <pre class="max-h-[70vh] overflow-auto rounded-xl border border-slate-200 bg-slate-50 p-3 text-[11px] leading-5 text-slate-800">{{ entityTaskDetailText }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useAdminUsageStore } from '~/stores/adminUsage'
import { useTaskStore } from '~/stores/task'
import type {
  AdminEntityTaskItem,
  AdminUsageSummary,
  AdminUsageTaskRow,
  AdminUsageUserRow,
  UsageRangeType,
  UsageScopeType,
} from '~/types/adminUsage'

const adminStore = useAdminUsageStore()
const taskStore = useTaskStore()

const activeTab = ref<'usage' | 'logs' | 'users' | 'tasks'>('usage')

const rangeType = ref<UsageRangeType>('day')
const usageScope = ref<'user' | 'task'>('user')
const keyword = ref('')
const taskTypeFilter = ref('')
const statusFilter = ref('')
const modelFilter = ref('')

const now = new Date()
const formatDateInput = (value: Date) => {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}
const anchorDay = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`)
const anchorMonth = ref(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)
const anchorYear = ref(now.getFullYear())
const logDateFrom = ref(`${anchorDay.value}T00:00`)
const logDateTo = ref(`${anchorDay.value}T23:59`)
const defaultTaskDateTo = formatDateInput(now)
const defaultTaskDateFromDate = new Date(now)
defaultTaskDateFromDate.setDate(defaultTaskDateFromDate.getDate() - 6)
const defaultTaskDateFrom = formatDateInput(defaultTaskDateFromDate)

const usageCurrentPage = ref(1)
const logCurrentPage = ref(1)
const entityUserCurrentPage = ref(1)
const entityTaskCurrentPage = ref(1)
const pageSize = 10

const logCategory = ref('')
const logProvider = ref('')
const logSuccess = ref('false')
const logTaskId = ref('')
const logUserName = ref('')
const logRequestId = ref('')
const logTraceId = ref('')
const logKeyword = ref('')
const detailLoadingLogId = ref('')
const detailLoadingTaskId = ref('')
const downloadingEntityTaskIds = ref(new Set<string>())
const entityUserKeyword = ref('')
const entityUserRole = ref('')
const entityTaskKeyword = ref('')
const entityTaskUserName = ref('')
const entityTaskType = ref('')
const entityTaskStatus = ref('')
const entityTaskDateFrom = ref(defaultTaskDateFrom)
const entityTaskDateTo = ref(defaultTaskDateTo)

const TASK_TYPE_LABELS: Record<string, string> = {
  patent_analysis: 'AI 分析',
  ai_review: 'AI 审查',
  ai_reply: 'AI 答复',
  ai_search: 'AI 检索',
}

const TASK_STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  processing: '处理中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  paused: '暂停',
}

const LOG_CATEGORY_LABELS: Record<string, string> = {
  user_action: '用户行为',
  task_execution: '任务执行',
  external_api: '外部接口',
  llm_call: 'LLM 调用',
}

const LOG_EVENT_LABELS: Record<string, string> = {
  http_request: 'HTTP 请求',
  requests_call: '外部请求',
  requests_call_exception: '外部请求异常',
  task_created: '任务创建',
  task_started: '任务启动',
  task_progress: '任务进度',
  task_completed: '任务完成',
  task_failed: '任务失败',
  task_cancelled: '任务取消',
  task_exception: '任务异常',
  task_timeout: '任务超时',
  task_download: '任务下载',
  task_deleted: '任务删除',
  task_bulk_deleted: '任务批量删除',
  chat_completion_json: '文本模型调用(JSON)',
  chat_completion_text: '文本模型调用',
  vision_completion_text: '视觉模型调用',
  vision_completion_json: '视觉模型调用(JSON)',
}

const USER_ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  member: '成员',
  guest: '访客',
}

const EMPTY_USAGE_SUMMARY: AdminUsageSummary = {
  totalTasks: 0,
  totalUsers: 0,
  totalTokens: 0,
  totalEstimatedCostCny: 0,
  totalLlmCallCount: 0,
  avgTokensPerEntity: 0,
  avgCostPerEntityCny: 0,
  entityType: 'task',
  priceMissing: false,
}

const loadingAccess = computed(() => adminStore.loadingAccess)
const loadingTable = computed(() => adminStore.loadingTable)
const loadingPricingRefresh = computed(() => adminStore.loadingPricingRefresh)
const loadingSystemLogs = computed(() => adminStore.loadingSystemLogs)
const loadingSystemSummary = computed(() => adminStore.loadingSystemSummary)
const loadingSystemLogDetail = computed(() => adminStore.loadingSystemLogDetail)
const loadingEntityUsers = computed(() => adminStore.loadingEntityUsers)
const loadingEntityTasks = computed(() => adminStore.loadingEntityTasks)
const loadingEntityTaskDetail = computed(() => adminStore.loadingEntityTaskDetail)
const isAdmin = computed(() => adminStore.isAdmin)
const tableData = computed(() => adminStore.tableData)
const pricingStatus = computed(() => adminStore.pricingStatus)
const systemLogSummary = computed(() => adminStore.systemLogSummary)
const systemLogs = computed(() => adminStore.systemLogs)
const systemLogDetail = computed(() => adminStore.systemLogDetail)
const entityUsers = computed(() => adminStore.entityUsers)
const entityUserStatsData = computed(() => adminStore.entityUserStats)
const entityTasks = computed(() => adminStore.entityTasks)
const entityTaskStatsData = computed(() => adminStore.entityTaskStats)
const entityTaskDetail = computed(() => adminStore.entityTaskDetail)
const userStats = computed(() => {
  const stats = entityUserStatsData.value?.userStats
  return {
    totalUsers: Number(stats?.totalUsers || 0),
    registeredUsers: Number(stats?.registeredUsers || 0),
    activeUsers1d: Number(stats?.activeUsers1d || 0),
    activeUsers7d: Number(stats?.activeUsers7d || 0),
    activeUsers30d: Number(stats?.activeUsers30d || 0),
    newUsers1d: Number(stats?.newUsers1d || 0),
    newUsers7d: Number(stats?.newUsers7d || 0),
    newUsers30d: Number(stats?.newUsers30d || 0),
  }
})
const taskTypeWindows = computed(() => entityTaskStatsData.value?.taskTypeWindows || [])
const usageSummary = computed(() => tableData.value?.summary || {
  ...EMPTY_USAGE_SUMMARY,
  entityType: usageScope.value,
})
const usageItems = computed<Array<AdminUsageTaskRow | AdminUsageUserRow>>(
  () => (tableData.value?.items || []) as Array<AdminUsageTaskRow | AdminUsageUserRow>
)

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
const adminDateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})
const formatDateTime = (value: string | null | undefined) => {
  const text = String(value || '').trim()
  if (!text) return '-'
  const normalizedText = text.replace(' ', 'T')
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(normalizedText)
    ? normalizedText
    : (normalizedText.includes('T') ? `${normalizedText}Z` : `${normalizedText}T00:00:00Z`)
  const parsed = new Date(normalized)
  if (Number.isNaN(parsed.getTime())) return text.slice(0, 19).replace('T', ' ')
  return adminDateTimeFormatter.format(parsed).replace(/\//g, '-')
}
const toLocalDateTimeFilter = (value: string | null | undefined): string | undefined => {
  const text = String(value || '').trim()
  if (!text) return undefined
  // datetime-local 通常是 YYYY-MM-DDTHH:mm，这里统一补全为本地无时区格式
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text)) return `${text}:00`
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(text)) return text
  return text
}
const formatDuration = (value: number | null | undefined) => {
  const total = Number(value)
  if (!Number.isFinite(total) || total < 0) return '-'
  const seconds = Math.floor(total)
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m`
  return `${seconds}s`
}
const formatTaskTypeLabel = (value: string | null | undefined) => {
  const text = String(value || '').trim()
  if (!text) return '-'
  return TASK_TYPE_LABELS[text] || text
}
const formatTaskStatusLabel = (value: string | null | undefined) => {
  const text = String(value || '').trim().toLowerCase()
  if (!text) return '-'
  return TASK_STATUS_LABELS[text] || text
}
const buildUrlWithToken = (url: string, token: string): string => {
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}token=${encodeURIComponent(token)}`
}
const canDownloadEntityTask = (row: AdminEntityTaskItem): boolean => {
  const taskId = String(row.taskId || '').trim()
  const status = String(row.status || '').trim().toLowerCase()
  return !!taskId && status === 'completed'
}
const buildEntityTaskDownloadFilename = (row: AdminEntityTaskItem): string => {
  const taskType = String(row.taskType || '').trim().toLowerCase()
  const artifactName = String(row.title || row.taskId || '').trim() || String(row.taskId || 'task')
  if (taskType === 'ai_reply') return `AI 答复报告_${artifactName}.pdf`
  if (taskType === 'ai_review') return `AI 审查报告_${artifactName}.pdf`
  if (taskType === 'ai_search') return `AI 检索结果_${artifactName}.zip`
  return `AI 分析报告_${artifactName}.pdf`
}
const downloadEntityTask = async (row: AdminEntityTaskItem) => {
  const taskId = String(row.taskId || '').trim()
  if (!taskId) return
  if (!canDownloadEntityTask(row)) return
  if (downloadingEntityTaskIds.value.has(taskId)) return

  const config = useRuntimeConfig()
  const rawDownloadUrl = `${config.public.apiBaseUrl}/api/admin/entities/tasks/${encodeURIComponent(taskId)}/download`

  const authed = await taskStore.ensureAuth()
  if (!authed || !taskStore.authToken) return

  const downloadUrl = buildUrlWithToken(rawDownloadUrl, taskStore.authToken)

  try {
    downloadingEntityTaskIds.value.add(taskId)
    const link = document.createElement('a')
    link.href = downloadUrl
    link.target = '_blank'
    link.rel = 'noopener'
    link.download = buildEntityTaskDownloadFilename(row)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  } catch (_error) {
    window.open(downloadUrl, '_blank')
  } finally {
    downloadingEntityTaskIds.value.delete(taskId)
  }
}
const formatLogCategory = (value: string | null | undefined) => {
  const text = String(value || '').trim()
  if (!text) return '-'
  return LOG_CATEGORY_LABELS[text] || text
}
const formatLogInterface = (row: { method?: string | null; path?: string | null }) => {
  const method = String(row.method || '').trim().toUpperCase()
  const path = String(row.path || '').trim()
  if (method || path) {
    return `${method || '-'} ${path || '-'}`.trim()
  }
  return '-'
}
const formatLogDurationSeconds = (value: number | null | undefined) => {
  const durationMs = Number(value)
  if (!Number.isFinite(durationMs) || durationMs < 0) return '-'
  const durationSeconds = durationMs / 1000
  return `${durationSeconds.toFixed(2)}s`
}
const formatLogEvent = (value: string | null | undefined) => {
  const text = String(value || '').trim()
  if (!text) return '-'
  return LOG_EVENT_LABELS[text] || text
}
const formatUserRoleLabel = (value: string | null | undefined) => {
  const text = String(value || '').trim().toLowerCase()
  if (!text) return '-'
  return USER_ROLE_LABELS[text] || text
}
const rowKey = (row: AdminUsageTaskRow | AdminUsageUserRow) => {
  if ('taskId' in row) return row.taskId || '-'
  return row.ownerId || '-'
}

const refreshUsageTable = async () => {
  const scope = usageScope.value as UsageScopeType
  await adminStore.fetchTable({
    rangeType: rangeType.value,
    anchor: activeAnchor.value,
    scope,
    q: keyword.value,
    taskType: taskTypeFilter.value,
    status: statusFilter.value,
    model: modelFilter.value,
    page: usageCurrentPage.value,
    pageSize,
    sortBy: scope === 'task' ? 'lastUsageAt' : 'totalTokens',
    sortOrder: 'desc',
  })
}
const refreshUsageAll = async () => {
  usageCurrentPage.value = 1
  await refreshUsageTable()
}

const refreshPricingStatusNow = async () => {
  const refreshed = await adminStore.refreshPricing(true)
  if (!refreshed) {
    await adminStore.fetchPricingStatus()
    return
  }
  if (activeTab.value === 'usage') {
    await refreshUsageTable()
  }
}

const refreshSystemLogs = async () => {
  await adminStore.fetchSystemLogs({
    category: logCategory.value,
    userName: logUserName.value,
    taskId: logTaskId.value,
    requestId: logRequestId.value,
    traceId: logTraceId.value,
    provider: logProvider.value,
    success: logSuccess.value,
    dateFrom: toLocalDateTimeFilter(logDateFrom.value),
    dateTo: toLocalDateTimeFilter(logDateTo.value),
    q: logKeyword.value,
    page: logCurrentPage.value,
    pageSize,
  })
}

const refreshSystemSummary = async () => {
  await adminStore.fetchSystemLogSummary(
    toLocalDateTimeFilter(logDateFrom.value),
    toLocalDateTimeFilter(logDateTo.value),
  )
}

const refreshSystemLogsAll = async () => {
  logCurrentPage.value = 1
  await Promise.all([refreshSystemSummary(), refreshSystemLogs()])
}

const refreshEntityUsers = async () => {
  await adminStore.fetchEntityUsers({
    q: entityUserKeyword.value,
    role: entityUserRole.value,
    page: entityUserCurrentPage.value,
    pageSize,
    sortBy: 'latestTaskAt',
    sortOrder: 'desc',
  })
}

const refreshEntityUserStats = async () => {
  await adminStore.fetchEntityUserStats()
}

const refreshEntityUsersAll = async () => {
  entityUserCurrentPage.value = 1
  await refreshEntityUsers()
}

const refreshEntityTasks = async () => {
  await adminStore.fetchEntityTasks({
    q: entityTaskKeyword.value,
    userName: entityTaskUserName.value,
    taskType: entityTaskType.value,
    status: entityTaskStatus.value,
    dateFrom: entityTaskDateFrom.value ? `${entityTaskDateFrom.value}T00:00:00` : undefined,
    dateTo: entityTaskDateTo.value ? `${entityTaskDateTo.value}T23:59:59` : undefined,
    page: entityTaskCurrentPage.value,
    pageSize,
    sortBy: 'createdAt',
    sortOrder: 'desc',
  })
}

const refreshEntityTaskStats = async () => {
  await adminStore.fetchEntityTaskStats()
}

const refreshEntityTasksAll = async () => {
  entityTaskCurrentPage.value = 1
  await refreshEntityTasks()
}

const onRangeTypeChange = () => {
  usageCurrentPage.value = 1
}

const openLogDetail = async (logId: string) => {
  detailLoadingLogId.value = logId
  try {
    await adminStore.fetchSystemLogDetail(logId)
  } finally {
    detailLoadingLogId.value = ''
  }
}

const closeLogDetail = () => {
  adminStore.clearSystemLogDetail()
}

const openEntityTaskDetail = async (taskId: string) => {
  detailLoadingTaskId.value = taskId
  try {
    await adminStore.fetchEntityTaskDetail(taskId)
  } finally {
    detailLoadingTaskId.value = ''
  }
}

const closeEntityTaskDetail = () => {
  adminStore.clearEntityTaskDetail()
}

const systemLogDetailText = computed(() => {
  const detail = systemLogDetail.value
  if (!detail) return ''
  return JSON.stringify(detail, null, 2)
})

const entityTaskDetailText = computed(() => {
  const detail = entityTaskDetail.value
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

const copyEntityTaskDetailPayload = async () => {
  const text = entityTaskDetailText.value
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
  } catch (_error) {
    // ignore
  }
}

const openLogsByUser = async (userName: string | null | undefined) => {
  const text = String(userName || '').trim()
  if (!text) return
  activeTab.value = 'logs'
  logUserName.value = text
  logTaskId.value = ''
  logCurrentPage.value = 1
  await refreshSystemLogsAll()
}

const openTasksByUser = async (userName: string | null | undefined) => {
  const text = String(userName || '').trim()
  if (!text) return
  entityTaskUserName.value = text
  entityTaskCurrentPage.value = 1
  activeTab.value = 'tasks'
  await refreshEntityTasks()
}

const openLogsByTask = async (taskId: string) => {
  const text = String(taskId || '').trim()
  if (!text) return
  activeTab.value = 'logs'
  logTaskId.value = text
  logCurrentPage.value = 1
  await refreshSystemLogsAll()
}

watch(usageScope, async () => {
  if (usageScope.value === 'user') {
    taskTypeFilter.value = ''
    statusFilter.value = ''
    modelFilter.value = ''
  }
  usageCurrentPage.value = 1
  if (activeTab.value === 'usage') {
    await refreshUsageTable()
  }
})

watch([keyword, taskTypeFilter, statusFilter, modelFilter], () => {
  usageCurrentPage.value = 1
})

watch([logCategory, logProvider, logSuccess, logTaskId, logUserName, logRequestId, logTraceId, logKeyword], () => {
  logCurrentPage.value = 1
})

watch([entityUserKeyword, entityUserRole], () => {
  entityUserCurrentPage.value = 1
})

watch([entityTaskKeyword, entityTaskUserName, entityTaskType, entityTaskStatus, entityTaskDateFrom, entityTaskDateTo], () => {
  entityTaskCurrentPage.value = 1
})

watch(activeTab, async (value) => {
  if (value === 'usage') {
    if (!pricingStatus.value) {
      await adminStore.fetchPricingStatus()
    }
    if (!tableData.value) {
      await refreshUsageAll()
    }
    return
  }
  if (value === 'logs') {
    if (!systemLogSummary.value || !systemLogs.value) {
      await refreshSystemLogsAll()
    }
    return
  }
  if (value === 'users') {
    const jobs: Promise<unknown>[] = []
    if (!entityUsers.value) {
      jobs.push(refreshEntityUsersAll())
    }
    if (!entityUserStatsData.value) {
      jobs.push(refreshEntityUserStats())
    }
    if (jobs.length) {
      await Promise.all(jobs)
    }
    return
  }
  const jobs: Promise<unknown>[] = []
  if (!entityTasks.value) {
    jobs.push(refreshEntityTasksAll())
  }
  if (!entityTaskStatsData.value) {
    jobs.push(refreshEntityTaskStats())
  }
  if (jobs.length) {
    await Promise.all(jobs)
  }
})

onMounted(async () => {
  await adminStore.fetchAccess(false)
  if (!adminStore.isAdmin) return
  await Promise.all([
    adminStore.fetchPricingStatus(),
    refreshUsageAll(),
  ])
})
</script>

<style scoped>
.admin-table-fixed {
  table-layout: fixed;
}

.admin-log-table {
  table-layout: fixed;
}

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
  @apply flex min-w-0 w-full flex-col gap-1 text-xs font-medium text-slate-600;
}

.field-input {
  @apply min-w-0 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition focus:border-cyan-400 focus:ring-2 focus:ring-cyan-100;
}

.query-btn {
  @apply inline-flex h-10 min-w-[8.5rem] items-center justify-center rounded-xl bg-cyan-700 px-6 text-sm font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:opacity-60;
}

.query-action {
  @apply w-full justify-self-stretch self-end sm:w-auto sm:justify-self-start;
}

.table-action-btn {
  @apply rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700 transition hover:bg-slate-50 disabled:opacity-50;
}

.modal-overlay {
  @apply fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-3;
}

.modal-panel {
  @apply w-full max-w-4xl rounded-2xl border border-slate-200 bg-white p-4 shadow-2xl shadow-slate-900/20;
}
</style>
