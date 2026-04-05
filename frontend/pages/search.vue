<template>
  <div class="space-y-4">
    <section v-if="error" class="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
      {{ error }}
    </section>

    <section class="grid items-start gap-4" :class="layoutClass">
      <aside class="rounded-3xl border border-slate-200 bg-white/95 p-4 shadow-sm shadow-slate-200 lg:sticky lg:top-14" :class="sidebarClass">
        <div v-if="sidebarCollapsed" class="mb-1 flex flex-col items-center gap-3">
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
            class="w-full rounded-xl bg-cyan-700 px-3 py-2.5 text-[13px] font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
            :disabled="loading"
            @click="createSession"
          >
            新建会话
          </button>
        </div>

        <div v-if="!sidebarCollapsed && loading && !sessions.length" class="rounded-2xl border border-dashed border-slate-200 px-3 py-6 text-center text-[13px] text-slate-500">
          正在加载会话...
        </div>
        <div v-else-if="!sidebarCollapsed && !sessions.length" class="rounded-2xl bg-slate-50/90 px-3 py-6 text-center text-[13px] text-slate-500">
          暂无会话
        </div>
        <div v-else-if="!sidebarCollapsed" class="max-h-[72vh] space-y-4 overflow-y-auto pr-1">
          <section
            v-for="group in groupedSessions"
            :key="group.key"
            class="space-y-2"
          >
            <p class="px-1 text-[11px] font-semibold tracking-[0.18em] text-slate-400">{{ group.label }}</p>
            <button
              v-for="session in group.items"
              :key="session.sessionId"
              type="button"
              class="w-full rounded-2xl border px-3 py-3 text-left transition"
              :class="sessionCardClass(session.sessionId)"
              @click="selectSession(session.sessionId)"
            >
              <div class="flex items-start justify-between gap-2">
                <p class="line-clamp-2 text-[13px] font-semibold leading-5 text-slate-900">{{ session.title }}</p>
                <span class="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold" :class="phaseBadgeClass(session.phase)">
                  {{ phaseLabel(session.phase) }}
                </span>
              </div>
              <div class="mt-2 flex items-center justify-between gap-2 text-[10px] text-slate-500">
                <span>V{{ session.activePlanVersion || 0 }}</span>
                <span>{{ session.selectedDocumentCount || 0 }} 篇</span>
              </div>
              <p class="mt-1 text-[10px] text-slate-400">{{ formatTime(session.updatedAt || session.createdAt) }}</p>
            </button>
          </section>
        </div>
      </aside>

      <section class="flex min-h-[calc(100vh-9rem)] flex-col overflow-hidden rounded-3xl border border-slate-200 bg-white/95 shadow-sm shadow-slate-200">
        <div v-if="sourceSummary" class="border-b border-cyan-200 bg-cyan-50/80 px-4 py-3">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="rounded-full border border-cyan-200 bg-white/80 px-2.5 py-0.5 text-[11px] font-semibold text-cyan-700">
                  来源于 AI 分析
                </span>
                <span v-if="sourceSummary.sourcePn" class="rounded-full border border-cyan-100 bg-cyan-100/70 px-2.5 py-0.5 text-[11px] font-medium text-cyan-900">
                  {{ sourceSummary.sourcePn }}
                </span>
              </div>
              <p class="mt-2 text-sm font-semibold text-cyan-950">{{ sourceBannerTitle }}</p>
              <p class="mt-1 text-xs leading-5 text-cyan-800">{{ sourceBannerText }}</p>
            </div>
            <p v-if="sourceSummary.sourceTitle" class="max-w-[18rem] text-right text-xs leading-5 text-cyan-900">
              {{ sourceSummary.sourceTitle }}
            </p>
          </div>
        </div>

        <div class="border-b border-slate-200 px-4 py-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="min-w-0 self-center">
              <div class="flex flex-wrap items-center gap-2">
                <p class="truncate text-sm font-semibold text-slate-900">{{ workspaceTitle }}</p>
                <span
                  v-if="sourceSummary"
                  class="rounded-full border border-cyan-200 bg-cyan-50 px-2.5 py-0.5 text-[11px] font-medium text-cyan-700"
                >
                  分析导入
                </span>
              </div>
            </div>
            <div class="flex flex-wrap items-center gap-2 self-center">
              <span class="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-600">
                {{ activePhaseLabel }}
              </span>
              <span class="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-600">
                计划 V{{ activePlanVersion || 0 }}
              </span>
            </div>
          </div>
        </div>

        <div class="border-b border-slate-200">
          <section v-if="hasDisplayedPlan">
            <button type="button" class="accordion-toggle" @click="togglePanel('plan')">
              <span class="accordion-title">
                检索计划
                <span class="accordion-meta">V{{ activePlanVersion || 0 }}</span>
              </span>
              <ChevronDownIcon class="accordion-icon" :class="{ 'rotate-180': openPanel === 'plan' }" />
            </button>
            <div v-if="openPanel === 'plan'" class="accordion-body">
              <div v-if="!hasDisplayedPlan" class="rounded-2xl border border-dashed border-slate-200 px-3 py-6 text-center text-sm text-slate-500">
                尚未生成检索计划。
              </div>
              <div v-else class="space-y-3">
                <div class="rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-3">
                  <p class="text-xs font-semibold text-slate-500">检索目标</p>
                  <p class="mt-1 text-sm text-slate-800">{{ displayedPlanObjective || '未生成' }}</p>
                </div>
                <div class="grid gap-3 lg:grid-cols-2">
                  <div class="rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-3">
                    <p class="text-xs font-semibold text-slate-500">筛选标准</p>
                    <p class="mt-1 whitespace-pre-wrap text-sm text-slate-800">{{ displayedSelectionCriteria || '未生成' }}</p>
                  </div>
                  <div class="rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-3">
                    <p class="text-xs font-semibold text-slate-500">关键限制</p>
                    <p class="mt-1 whitespace-pre-wrap text-sm text-slate-800">{{ displayedNegativeConstraints || '无' }}</p>
                  </div>
                </div>
                <div v-if="displayedExecutionNotes" class="rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-3">
                  <p class="text-xs font-semibold text-slate-500">执行说明</p>
                  <p class="mt-1 whitespace-pre-wrap text-sm text-slate-800">{{ displayedExecutionNotes }}</p>
                </div>
                <div class="space-y-2">
                  <p class="text-xs font-semibold text-slate-500">查询批次</p>
                  <article
                    v-for="batch in displayedQueryBatches"
                    :key="batch.batch_id || batch.batchId || batch.goal"
                    class="rounded-2xl border border-slate-200 bg-white px-3 py-3"
                  >
                    <p class="text-sm font-semibold text-slate-900">{{ batch.goal || '未命名批次' }}</p>
                    <p class="mt-1 text-xs text-slate-600">目标块：{{ joinValues(batch.target_blocks || batch.targetBlocks) || '无' }}</p>
                    <p class="mt-1 text-xs text-slate-600">必须词：{{ joinValues(batch.must_terms_zh || batch.mustTermsZh) || '无' }}</p>
                    <p class="mt-1 text-xs text-slate-600">可选词：{{ joinValues(batch.should_terms_zh || batch.shouldTermsZh) || '无' }}</p>
                    <p class="mt-1 text-xs text-slate-600">排除词：{{ joinValues(batch.negative_terms || batch.negativeTerms) || '无' }}</p>
                  </article>
                </div>
              </div>
            </div>
          </section>

          <section v-if="searchElementsObjective || searchElementsApplicants || searchElementsFilingDate || searchElementsPriorityDate || searchElementsCutoffDate || searchElementsMissingItems.length || searchElementsRows.length">
            <button type="button" class="accordion-toggle" @click="togglePanel('elements')">
              <span class="accordion-title">
                检索要素
                <span class="accordion-meta">{{ searchElementsRows.length }} 项</span>
              </span>
              <ChevronDownIcon class="accordion-icon" :class="{ 'rotate-180': openPanel === 'elements' }" />
            </button>
            <div v-if="openPanel === 'elements'" class="accordion-body">
              <div v-if="searchElementsObjective" class="rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-3">
                <p class="text-xs font-semibold text-slate-500">目标</p>
                <p class="mt-1 text-sm text-slate-800">{{ searchElementsObjective }}</p>
              </div>
              <div
                v-if="searchElementsApplicants || searchElementsFilingDate || searchElementsPriorityDate || searchElementsCutoffDate"
                class="mt-3 grid gap-3 lg:grid-cols-2"
              >
                <div v-if="searchElementsApplicants" class="rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-3">
                  <p class="text-xs font-semibold text-slate-500">申请人</p>
                  <p class="mt-1 text-sm text-slate-800">{{ searchElementsApplicants }}</p>
                </div>
                <div v-if="searchElementsCutoffDate" class="rounded-2xl border border-cyan-200 bg-cyan-50/70 px-3 py-3">
                  <p class="text-xs font-semibold text-cyan-700">生效检索截止日</p>
                  <p class="mt-1 text-sm font-semibold text-cyan-900">{{ searchElementsCutoffDate }}</p>
                </div>
                <div v-if="searchElementsFilingDate" class="rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-3">
                  <p class="text-xs font-semibold text-slate-500">申请日</p>
                  <p class="mt-1 text-sm text-slate-800">{{ searchElementsFilingDate }}</p>
                </div>
                <div v-if="searchElementsPriorityDate" class="rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-3">
                  <p class="text-xs font-semibold text-slate-500">优先权日</p>
                  <p class="mt-1 text-sm text-slate-800">{{ searchElementsPriorityDate }}</p>
                </div>
              </div>
              <div v-if="searchElementsMissingItems.length" class="mt-3 rounded-2xl border border-amber-200 bg-amber-50 px-3 py-3">
                <p class="text-xs font-semibold text-amber-800">待补充信息</p>
                <ul class="mt-2 list-disc space-y-1 pl-4 text-xs text-amber-800">
                  <li v-for="item in searchElementsMissingItems" :key="item">{{ item }}</li>
                </ul>
              </div>
              <div v-if="!searchElementsRows.length" class="mt-3 rounded-2xl border border-dashed border-slate-200 px-3 py-6 text-center text-sm text-slate-500">
                主 agent 尚未产出结构化检索要素。
              </div>
              <div v-else class="mt-3 max-h-[22rem] space-y-2 overflow-y-auto pr-1">
                <article
                  v-for="element in searchElementsRows"
                  :key="element.element_id || `${element.block_id}-${element.element_name}`"
                  class="rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-3"
                >
                  <div class="flex items-start justify-between gap-2">
                    <div>
                      <p class="text-sm font-semibold text-slate-900">{{ element.element_name || '未命名要素' }}</p>
                      <p v-if="elementMetaText(element)" class="mt-1 text-xs text-slate-500">{{ elementMetaText(element) }}</p>
                    </div>
                    <span class="rounded-full px-2 py-0.5 text-[11px] font-semibold" :class="elementStatusClass(element.status)">
                      {{ element.status || 'unknown' }}
                    </span>
                  </div>
                  <p class="mt-2 text-xs text-slate-600">重要性：{{ element.importance || '-' }}</p>
                  <p class="mt-1 text-xs text-slate-600">中文关键词：{{ joinValues(element.keywords_zh) || '无' }}</p>
                  <p class="mt-1 text-xs text-slate-600">英文关键词：{{ joinValues(element.keywords_en) || '无' }}</p>
                  <p v-if="element.notes" class="mt-1 whitespace-pre-wrap text-xs text-slate-600">{{ element.notes }}</p>
                </article>
              </div>
            </div>
          </section>

          <section v-if="candidateDocuments.length">
            <button type="button" class="accordion-toggle" @click="togglePanel('candidates')">
              <span class="accordion-title">
                候选结果
                <span class="accordion-meta">{{ candidateDocuments.length }} 条</span>
              </span>
              <ChevronDownIcon class="accordion-icon" :class="{ 'rotate-180': openPanel === 'candidates' }" />
            </button>
            <div v-if="openPanel === 'candidates'" class="accordion-body">
              <div v-if="!candidateDocuments.length" class="rounded-2xl border border-dashed border-slate-200 px-3 py-6 text-center text-sm text-slate-500">
                确认计划后会在这里展示候选文献池。
              </div>
              <div v-else class="max-h-[22rem] space-y-2 overflow-y-auto pr-1">
                <article
                  v-for="doc in candidateDocuments"
                  :key="doc.document_id"
                  class="rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-3"
                >
                  <div class="flex items-start justify-between gap-2">
                    <div>
                      <p class="text-sm font-semibold text-slate-900">{{ doc.title || doc.pn || '未命名文献' }}</p>
                      <p class="mt-1 text-xs text-slate-500">{{ doc.pn || '-' }}</p>
                    </div>
                    <span class="rounded-full px-2 py-0.5 text-[11px] font-semibold" :class="documentStageClass(doc.stage)">
                      {{ documentStageLabel(doc.stage) }}
                    </span>
                  </div>
                  <p v-if="doc.abstract" class="mt-2 line-clamp-3 text-xs leading-5 text-slate-600">{{ doc.abstract }}</p>
                  <p class="mt-2 text-[11px] text-slate-500">来源批次：{{ joinValues(doc.source_batches_json) || '无' }}</p>
                  <p v-if="doc.agent_reason" class="mt-1 text-[11px] text-slate-500">{{ doc.agent_reason }}</p>
                  <div class="mt-3 flex justify-end">
                    <button
                      type="button"
                      class="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400"
                      :disabled="!activePlanVersion || streaming"
                      @click="addSelectedDocument(doc.document_id)"
                    >
                      {{ doc.user_removed ? '重新加入对比文件' : '加入对比文件' }}
                    </button>
                  </div>
                </article>
              </div>
            </div>
          </section>

          <section v-if="selectedDocuments.length">
            <button type="button" class="accordion-toggle" @click="togglePanel('selected')">
              <span class="accordion-title">
                对比文件
                <span class="accordion-meta">{{ selectedDocuments.length }} 篇</span>
              </span>
              <ChevronDownIcon class="accordion-icon" :class="{ 'rotate-180': openPanel === 'selected' }" />
            </button>
            <div v-if="openPanel === 'selected'" class="accordion-body">
              <div v-if="!selectedDocuments.length" class="rounded-2xl border border-dashed border-slate-200 px-3 py-6 text-center text-sm text-slate-500">
                精读完成后会在这里展示当前对比文件集合。
              </div>
              <div v-else class="space-y-3">
                <div class="flex justify-end">
                  <button
                    type="button"
                    class="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                    :disabled="streaming || !activePlanVersion || !selectedDocuments.length"
                    @click="generateFeatureTable"
                  >
                    生成特征对比表
                  </button>
                </div>
                <div class="max-h-[22rem] space-y-2 overflow-y-auto pr-1">
                  <article
                    v-for="doc in selectedDocuments"
                    :key="doc.document_id"
                    class="rounded-2xl border border-emerald-200 bg-emerald-50/70 px-3 py-3"
                  >
                    <div class="flex items-start justify-between gap-2">
                      <div>
                        <p class="text-sm font-semibold text-slate-900">{{ doc.title || doc.pn || '未命名文献' }}</p>
                        <p class="mt-1 text-xs text-slate-500">{{ doc.pn || '-' }}</p>
                      </div>
                      <span class="rounded-full border border-emerald-200 bg-white px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
                        {{ doc.user_pinned ? '用户保留' : '已选' }}
                      </span>
                    </div>
                    <div v-if="Array.isArray(doc.key_passages_json) && doc.key_passages_json.length" class="mt-2 space-y-1">
                      <p class="text-[11px] font-semibold text-slate-500">证据段落</p>
                      <p
                        v-for="(passage, index) in doc.key_passages_json.slice(0, 2)"
                        :key="`${doc.document_id}-${index}`"
                        class="rounded-xl border border-white/80 bg-white/80 px-2.5 py-2 text-[11px] leading-5 text-slate-600"
                      >
                        {{ passage.passage || passage.reason || '无证据摘要' }}
                      </p>
                    </div>
                    <div class="mt-3 flex justify-end">
                      <button
                        type="button"
                        class="rounded-xl border border-rose-200 bg-white px-3 py-1.5 text-xs font-semibold text-rose-700 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400"
                        :disabled="!activePlanVersion || streaming"
                        @click="removeSelectedDocument(doc.document_id)"
                      >
                        移出对比文件
                      </button>
                    </div>
                  </article>
                </div>
              </div>
            </div>
          </section>

          <section v-if="featureTableRows.length">
            <button type="button" class="accordion-toggle" @click="togglePanel('feature')">
              <span class="accordion-title">
                特征对比表
                <span class="accordion-meta">{{ featureTableRows.length }} 行</span>
              </span>
              <ChevronDownIcon class="accordion-icon" :class="{ 'rotate-180': openPanel === 'feature' }" />
            </button>
            <div v-if="openPanel === 'feature'" class="accordion-body">
              <div v-if="!featureTableRows.length" class="rounded-2xl border border-dashed border-slate-200 px-3 py-6 text-center text-sm text-slate-500">
                选定对比文件后可生成结构化特征对比表。
              </div>
              <div v-else class="space-y-3">
                <div class="overflow-x-auto rounded-2xl border border-slate-200">
                  <table class="min-w-full divide-y divide-slate-200 text-xs">
                    <thead class="bg-slate-50 text-slate-600">
                      <tr>
                        <th
                          v-for="column in featureTableColumns"
                          :key="column"
                          class="whitespace-nowrap px-3 py-2 text-left font-semibold"
                        >
                          {{ column }}
                        </th>
                      </tr>
                    </thead>
                    <tbody class="divide-y divide-slate-100 bg-white">
                      <tr v-for="(row, rowIndex) in featureTableRows" :key="`feature-${rowIndex}`">
                        <td
                          v-for="column in featureTableColumns"
                          :key="`${rowIndex}-${column}`"
                          class="whitespace-pre-wrap px-3 py-2 align-top text-slate-700"
                        >
                          {{ featureCell(row, column) }}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div v-if="featureTableSummary" class="rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-3">
                  <p class="text-xs font-semibold text-slate-500">总结</p>
                  <p class="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-700">{{ featureTableSummary }}</p>
                </div>
              </div>
            </div>
          </section>
        </div>

        <div ref="messageListRef" class="flex-1 overflow-y-auto px-4 py-4">
          <div v-if="!conversationEntries.length" class="flex h-full min-h-[18rem] items-center justify-center px-4 py-8 text-center text-sm text-slate-500">
            描述检索目标、技术方案、claim 片段或约束条件。
          </div>

          <div v-else class="space-y-4">
            <template v-for="entry in conversationEntries" :key="entry.id">
            <article v-if="entry.entryType === 'system'" class="flex justify-center">
              <div class="w-full rounded-2xl border border-slate-200 bg-slate-50/90 px-3 py-2.5">
                <div class="flex items-center justify-between gap-3">
                  <p class="text-xs font-medium text-slate-600">{{ entry.content }}</p>
                  <span class="rounded-full px-2 py-0.5 text-[11px] font-semibold" :class="phaseBadgeClass(entry.phase)">
                    {{ phaseLabel(entry.phase) }}
                  </span>
                </div>
              </div>
            </article>

            <article
              v-else
              class="flex"
              :class="entry.role === 'user' ? 'justify-end' : 'justify-start'"
            >
              <div
                class="max-w-[90%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm"
                :class="entry.role === 'user'
                  ? 'bg-cyan-700 text-white shadow-cyan-100'
                  : entry.kind === 'question'
                    ? 'border border-amber-200 bg-amber-50 text-amber-900'
                    : 'border border-slate-200 bg-slate-50 text-slate-700'"
              >
                <p class="whitespace-pre-wrap break-words">{{ entry.content }}</p>
                <p class="mt-2 text-[11px]" :class="entry.role === 'user' ? 'text-cyan-100/90' : 'text-slate-400'">
                  {{ messageLabel(entry.kind) }}
                </p>
              </div>
            </article>
            </template>
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

          <AiSearchPlanConfirmationCard
            v-else-if="pendingConfirmation"
            v-model="composer"
            :input-disabled="streaming || !currentSession"
            :submit-disabled="streaming || !composer.trim() || !currentSession"
            :confirm-disabled="streaming || !confirmationPlanVersion"
            @submit="submitMessage"
            @confirm="confirmPlan"
          />

          <section v-else>
            <div class="relative">
              <textarea
                v-model="composer"
                rows="4"
                class="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 pr-32 text-sm text-slate-900 outline-none transition focus:border-cyan-500 focus:ring-2 focus:ring-cyan-100 disabled:cursor-not-allowed disabled:bg-slate-50"
                :disabled="inputDisabled || !currentSession"
                :placeholder="inputPlaceholder"
                @keydown.meta.enter.prevent="submitMessage"
                @keydown.ctrl.enter.prevent="submitMessage"
              />
              <button
                type="button"
                class="absolute bottom-3 right-3 rounded-xl bg-cyan-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-cyan-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                :disabled="!canSubmitMessage"
                @click="submitMessage"
              >
                发送消息
              </button>
            </div>
            <p v-if="currentSession?.phase === 'searching'" class="mt-2 text-xs text-slate-500">
              当前轮检索执行中，完成后可继续调整计划。
            </p>
          </section>
        </div>
      </section>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ChevronDownIcon, ChevronLeftIcon, ChevronRightIcon, PlusIcon } from '@heroicons/vue/24/outline'
import { storeToRefs } from 'pinia'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AiSearchPlanConfirmationCard from '~/components/ai-search/AiSearchPlanConfirmationCard.vue'
import AiSearchQuestionCard from '~/components/ai-search/AiSearchQuestionCard.vue'
import { useAiSearchStore } from '~/stores/aiSearch'
import type { AiSearchSessionSummary } from '~/types/aiSearch'

type PanelKey = 'plan' | 'elements' | 'candidates' | 'selected' | 'feature'
type SessionGroup = {
  key: string
  label: string
  items: AiSearchSessionSummary[]
}

const SIDEBAR_COLLAPSED_STORAGE_KEY = 'ai-search-sidebar-collapsed'

const aiSearchStore = useAiSearchStore()
const route = useRoute()
const router = useRouter()
const { activityLog, currentSession, error, loading, sessions, streaming } = storeToRefs(aiSearchStore)

const composer = ref('')
const answerDraft = ref('')
const messageListRef = ref<HTMLElement | null>(null)
const openPanel = ref<PanelKey>('plan')
const sidebarCollapsed = ref(false)

const messages = computed(() => currentSession.value?.messages || [])
const pendingQuestion = computed<Record<string, any> | null>(() => currentSession.value?.pendingQuestion || null)
const pendingConfirmation = computed<Record<string, any> | null>(() => currentSession.value?.pendingConfirmation || null)
const candidateDocuments = computed(() => currentSession.value?.candidateDocuments || [])
const selectedDocuments = computed(() => currentSession.value?.selectedDocuments || [])
const sourceSummary = computed<Record<string, any> | null>(() => currentSession.value?.sourceSummary || null)

const normalizedPlan = computed<Record<string, any> | null>(() => {
  const currentPlan = currentSession.value?.currentPlan
  if (!currentPlan) return null
  const planJson = currentPlan.plan_json || currentPlan
  return {
    ...planJson,
    plan_version: currentPlan.plan_version || planJson.plan_version,
    status: currentPlan.status || planJson.status,
  }
})

const activePlanVersion = computed(() => {
  const candidate = pendingConfirmation.value?.planVersion
    || normalizedPlan.value?.plan_version
    || currentSession.value?.session.activePlanVersion
  const value = Number(candidate || 0)
  return Number.isFinite(value) && value > 0 ? value : 0
})

const displayedPlanSummary = computed<Record<string, any> | null>(() => pendingConfirmation.value?.planSummary || null)
const displayedPlanObjective = computed(() => displayedPlanSummary.value?.objective || normalizedPlan.value?.objective || '')
const displayedSelectionCriteria = computed(() => displayedPlanSummary.value?.selectionCriteria || normalizedPlan.value?.selection_criteria || '')
const displayedNegativeConstraints = computed(() => displayedPlanSummary.value?.negativeConstraints || normalizedPlan.value?.negative_constraints || '')
const displayedExecutionNotes = computed(() => displayedPlanSummary.value?.executionNotes || normalizedPlan.value?.execution_notes || '')
const displayedQueryBatches = computed<Array<Record<string, any>>>(() => {
  const batches = displayedPlanSummary.value?.queryBatches || normalizedPlan.value?.query_batches || []
  return Array.isArray(batches) ? batches : []
})
const hasDisplayedPlan = computed(() => !!displayedPlanObjective.value || displayedQueryBatches.value.length > 0)

const searchElements = computed<Record<string, any> | null>(() => currentSession.value?.searchElements || null)
const searchElementsRows = computed<Array<Record<string, any>>>(() => {
  const rows = searchElements.value?.search_elements || []
  return Array.isArray(rows) ? rows : []
})
const searchElementsObjective = computed(() => String(searchElements.value?.objective || '').trim())
const searchElementsMissingItems = computed<string[]>(() => {
  const items = searchElements.value?.missing_items || []
  return Array.isArray(items) ? items.map((item) => String(item || '').trim()).filter(Boolean) : []
})
const searchElementsApplicants = computed(() => {
  const items = searchElements.value?.applicants || []
  return Array.isArray(items) ? items.map((item) => String(item || '').trim()).filter(Boolean).join('、') : ''
})
const searchElementsFilingDate = computed(() => String(searchElements.value?.filing_date || '').trim())
const searchElementsPriorityDate = computed(() => String(searchElements.value?.priority_date || '').trim())
const searchElementsCutoffDate = computed(() => searchElementsPriorityDate.value || searchElementsFilingDate.value)

const confirmationPlanVersion = computed(() => Number(pendingConfirmation.value?.planVersion || activePlanVersion.value || 0))
const featureTableRows = computed<Array<Record<string, any>>>(() => {
  const rows = currentSession.value?.featureTable?.table_json || []
  return Array.isArray(rows) ? rows : []
})
const featureTableColumns = computed<string[]>(() => {
  const columns: string[] = []
  for (const row of featureTableRows.value) {
    for (const key of Object.keys(row || {})) {
      if (!columns.includes(key)) columns.push(key)
    }
  }
  return columns
})
const featureTableSummary = computed(() => String(currentSession.value?.featureTable?.summary_markdown || '').trim())

const activePhaseLabel = computed(() => phaseLabel(currentSession.value?.phase || 'collecting_requirements'))
const inputDisabled = computed(() => aiSearchStore.inputDisabled || !currentSession.value)
const canSubmitMessage = computed(() => !!composer.value.trim() && !inputDisabled.value)

const questionPrompt = computed(() => String(pendingQuestion.value?.prompt || '').trim())
const questionReason = computed(() => String(pendingQuestion.value?.reason || '').trim())
const questionAnswerShape = computed(() => String(pendingQuestion.value?.expected_answer_shape || '').trim())

const workspaceTitle = computed(() => String(currentSession.value?.session.title || 'AI 检索工作台'))
const sourceBannerTitle = computed(() => {
  if (!sourceSummary.value) return ''
  if (pendingQuestion.value) return '已从 AI 分析生成检索草稿，当前还需补充少量信息'
  if (pendingConfirmation.value) return '已从 AI 分析生成检索草稿，确认计划后即可开始检索'
  return '已从 AI 分析生成检索草稿'
})
const sourceBannerText = computed(() => {
  if (!sourceSummary.value) return ''
  const fallback = '系统已根据分析结果预填检索要素，并把首轮计划起草放在当前工作台中。'
  if (pendingQuestion.value) {
    return '系统已完成第一阶段信息收集，但仍存在缺口。补充当前追问后，会继续起草或刷新检索计划。'
  }
  if (pendingConfirmation.value) {
    return '系统已完成第一阶段信息收集并起草检索计划。你只需要检查计划边界，确认后再开始真实检索。'
  }
  return String(sourceSummary.value?.summaryText || '').trim() || fallback
})
const layoutClass = computed(() => (sidebarCollapsed.value
  ? 'lg:grid-cols-[3.75rem,minmax(0,1fr)]'
  : 'lg:grid-cols-[15rem,minmax(0,1fr)] xl:grid-cols-[15.5rem,minmax(0,1fr)]'
))
const sidebarClass = computed(() => (sidebarCollapsed.value ? 'px-2.5 lg:px-2.5' : ''))

const inputPlaceholder = computed(() => {
  if (!currentSession.value) return '正在准备会话...'
  if (currentSession.value.phase === 'searching') return '检索执行中，当前版本不支持中途修改计划'
  return '输入检索目标、技术方案、claim 片段，并尽量补充申请人和申请日/优先权日'
})

const sortedSessions = computed<AiSearchSessionSummary[]>(() => {
  return [...sessions.value].sort((left, right) => {
    const diff = toMillis(right.updatedAt || right.createdAt) - toMillis(left.updatedAt || left.createdAt)
    if (diff !== 0) return diff
    return String(right.sessionId || '').localeCompare(String(left.sessionId || ''))
  })
})

const groupedSessions = computed<SessionGroup[]>(() => {
  const groups = new Map<string, SessionGroup>()

  for (const session of sortedSessions.value) {
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

const conversationEntries = computed<Array<Record<string, any>>>(() => {
  const merged: Array<Record<string, any>> = []
  messages.value.forEach((message, index) => {
    merged.push({
      id: message.message_id || `message-${index}`,
      entryType: 'message',
      sortKey: toMillis(message.created_at),
      order: index,
      ...message,
    })
  })
  activityLog.value.forEach((item, index) => {
    merged.push({
      id: `system-${item.id || index}`,
      entryType: 'system',
      content: item.text,
      phase: item.phase,
      createdAt: item.createdAt,
      sortKey: toMillis(item.createdAt),
      order: messages.value.length + index,
    })
  })
  return merged.sort((left, right) => {
    if (left.sortKey !== right.sortKey) return left.sortKey - right.sortKey
    return left.order - right.order
  })
})

const suggestedPanel = computed<PanelKey>(() => {
  if (pendingConfirmation.value) return 'plan'
  const phase = currentSession.value?.phase || ''
  if (phase === 'collecting_requirements' || phase === 'awaiting_user_answer' || phase === 'drafting_plan') {
    return searchElementsRows.value.length ? 'elements' : 'plan'
  }
  if (phase === 'results_ready' || phase === 'completed') {
    for (const item of [...activityLog.value].reverse()) {
      if (item.type === 'feature_table.updated') return 'feature'
      if (item.type === 'selection.updated') return 'selected'
      if (item.type === 'documents.updated') return 'candidates'
      if (item.type === 'search_elements.updated') return 'elements'
      if (item.type === 'plan.updated' || item.type === 'plan.awaiting_confirmation') return 'plan'
    }
    if (featureTableRows.value.length) return 'feature'
    if (selectedDocuments.value.length) return 'selected'
    if (candidateDocuments.value.length) return 'candidates'
  }
  if (searchElementsRows.value.length) return 'elements'
  return 'plan'
})

const joinValues = (values: unknown): string => {
  if (!Array.isArray(values)) return ''
  return values.map((item) => String(item || '').trim()).filter(Boolean).join('、')
}

const elementMetaText = (element: Record<string, any>): string => {
  const blockId = String(element?.block_id || '').trim()
  const role = String(element?.element_role || '').trim()
  if (blockId && role) return `${blockId} · ${role}`
  return blockId || role
}

const featureCell = (row: Record<string, any>, column: string): string => {
  const value = row?.[column]
  if (Array.isArray(value)) return value.map((item) => String(item || '').trim()).filter(Boolean).join('、')
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value ?? '')
}

const formatTime = (value?: string | null): string => {
  const text = String(value || '').trim()
  if (!text) return '--'
  const date = new Date(text)
  if (Number.isNaN(date.getTime())) return text
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

const phaseLabel = (phase: string): string => {
  const map: Record<string, string> = {
    collecting_requirements: '整理需求',
    awaiting_user_answer: '等待回答',
    drafting_plan: '起草计划',
    awaiting_plan_confirmation: '待确认',
    searching: '检索执行中',
    results_ready: '结果已就绪',
    completed: '已完成',
    failed: '失败',
    cancelled: '已终止',
  }
  return map[phase] || phase || '未知阶段'
}

const messageLabel = (kind?: string): string => {
  if (kind === 'question') return '追问'
  if (kind === 'answer') return '回答'
  return kind === 'chat' ? '对话' : '消息'
}

const phaseBadgeClass = (phase: string): string => {
  if (phase === 'awaiting_user_answer' || phase === 'awaiting_plan_confirmation') {
    return 'border border-amber-200 bg-amber-50 text-amber-700'
  }
  if (phase === 'searching') {
    return 'border border-cyan-200 bg-cyan-50 text-cyan-700'
  }
  if (phase === 'completed' || phase === 'results_ready') {
    return 'border border-emerald-200 bg-emerald-50 text-emerald-700'
  }
  if (phase === 'failed' || phase === 'cancelled') {
    return 'border border-rose-200 bg-rose-50 text-rose-700'
  }
  return 'border border-slate-200 bg-slate-50 text-slate-600'
}

const sessionCardClass = (sessionId: string): string => {
  if (sessionId === currentSession.value?.session.sessionId) {
    return 'border-cyan-300 bg-cyan-50/70 shadow-sm shadow-cyan-100'
  }
  return 'border-slate-200 bg-slate-50/50 hover:border-slate-300 hover:bg-white'
}

const elementStatusClass = (status?: string): string => {
  if (status === 'complete') return 'border border-emerald-200 bg-emerald-50 text-emerald-700'
  if (status === 'needs_answer') return 'border border-amber-200 bg-amber-50 text-amber-700'
  return 'border border-slate-200 bg-slate-50 text-slate-600'
}

const documentStageLabel = (stage?: string): string => {
  if (stage === 'shortlisted') return '粗筛保留'
  if (stage === 'selected') return '已选'
  if (stage === 'rejected') return '已排除'
  return '候选'
}

const documentStageClass = (stage?: string): string => {
  if (stage === 'shortlisted') return 'border border-cyan-200 bg-cyan-50 text-cyan-700'
  if (stage === 'selected') return 'border border-emerald-200 bg-emerald-50 text-emerald-700'
  if (stage === 'rejected') return 'border border-rose-200 bg-rose-50 text-rose-700'
  return 'border border-slate-200 bg-slate-50 text-slate-600'
}

const togglePanel = (panel: PanelKey) => {
  openPanel.value = panel
}

const toggleSidebar = () => {
  sidebarCollapsed.value = !sidebarCollapsed.value
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

const selectSession = async (sessionId: string) => {
  if (!sessionId || sessionId === currentSession.value?.session.sessionId) return
  await aiSearchStore.loadSession(sessionId)
}

const submitMessage = async () => {
  const content = composer.value.trim()
  if (!content) return
  if (pendingQuestion.value) return
  if (streaming.value || !currentSession.value) return
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

const addSelectedDocument = async (documentId: string) => {
  if (!activePlanVersion.value || !documentId) return
  await aiSearchStore.patchSelectedDocuments(activePlanVersion.value, [documentId], undefined)
}

const removeSelectedDocument = async (documentId: string) => {
  if (!activePlanVersion.value || !documentId) return
  await aiSearchStore.patchSelectedDocuments(activePlanVersion.value, undefined, [documentId])
}

const generateFeatureTable = async () => {
  if (!activePlanVersion.value || !selectedDocuments.value.length) return
  await aiSearchStore.generateFeatureTable(activePlanVersion.value)
}

watch(
  () => currentSession.value?.session.sessionId || '',
  (sessionId) => {
    openPanel.value = suggestedPanel.value
    composer.value = ''
    answerDraft.value = ''
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
  () => currentSession.value?.phase || '',
  () => {
    openPanel.value = suggestedPanel.value
  },
)

watch(
  () => pendingConfirmation.value?.planVersion || 0,
  (value, previousValue) => {
    if (value && value !== previousValue) {
      openPanel.value = 'plan'
    }
  },
)

watch(
  () => activityLog.value.length,
  (value, previousValue) => {
    if (value > previousValue && (currentSession.value?.phase === 'results_ready' || currentSession.value?.phase === 'completed')) {
      openPanel.value = suggestedPanel.value
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
  sidebarCollapsed,
  (value) => {
    if (!import.meta.client) return
    window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, value ? '1' : '0')
  },
)

onMounted(async () => {
  if (import.meta.client) {
    sidebarCollapsed.value = window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === '1'
  }
  const preferredSessionId = String(route.query.session || '').trim()
  await aiSearchStore.init(preferredSessionId)
  openPanel.value = suggestedPanel.value
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
</style>
