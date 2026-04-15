import type {
  AiSearchPendingAssistantMessage,
  AiSearchPhaseMarker,
  AiSearchSessionSummary,
  AiSearchSnapshot,
  AiSearchSubagentStatus,
} from '~/types/aiSearch'

type MockRuntime = {
  activeRun: { runKey: string, sessionId: string, startedAt: string, phase: string } | null
  pendingAssistantMessage: AiSearchPendingAssistantMessage | null
  phaseMarkers: AiSearchPhaseMarker[]
  activeSubagentStatuses: Record<string, AiSearchSubagentStatus>
  streaming: boolean
  error: string
}

export type AiSearchMockState = {
  currentSessionId: string
  sessions: AiSearchSessionSummary[]
  snapshotsById: Record<string, AiSearchSnapshot>
  runtimeById: Record<string, MockRuntime>
}

const baseTime = new Date('2026-04-15T10:00:00.000Z').getTime()
const isoAt = (offsetMinutes: number): string => new Date(baseTime + offsetMinutes * 60_000).toISOString()

const processMessage = (
  messageId: string,
  createdAt: string,
  content: string,
  metadata: Record<string, any>,
) => ({
  message_id: messageId,
  role: 'assistant',
  kind: 'process',
  content,
  stream_status: 'completed',
  metadata,
  created_at: createdAt,
})

const groupStatusMetadata = (
  eventId: string,
  name: string,
  label: string,
  status: 'running' | 'completed' | 'failed',
) => ({
  eventId,
  processType: 'subagent',
  status,
  name,
  label,
  summary: label,
  statusText: status === 'completed' ? `${label}已完成` : status === 'failed' ? `${label}失败` : `${label}开始执行`,
  subagentName: name,
  subagentLabel: label,
  displayKind: 'group_status',
  displayGroupKey: name,
  dedupeKey: name,
})

const detailMetadata = (
  eventId: string,
  subagentName: string,
  subagentLabel: string,
  toolName: string,
  toolLabel: string,
  summary: string,
  status: 'running' | 'completed' | 'failed',
) => ({
  eventId,
  processType: 'tool',
  status,
  toolName,
  toolLabel,
  summary,
  statusText: status === 'completed' ? `${summary}已完成` : status === 'failed' ? `${summary}失败` : summary,
  subagentName,
  subagentLabel,
  displayKind: 'detail',
  displayGroupKey: subagentName,
  dedupeKey: eventId.replace(/:(running|completed|failed)$/u, ''),
})

const planExecutionSpec = {
  sub_plans: [
    {
      sub_plan_id: 'sub-plan-1',
      goal: '围绕 CN115791220A 的控制逻辑与关键约束做现有技术检索',
      retrieval_steps: [
        {
          step_id: 'step-1',
          title: '核心方案宽召回',
          purpose: '先覆盖主权利要求中的控制链路与关键结构。',
          activation_mode: 'immediate',
          activation_summary: '',
        },
        {
          step_id: 'step-2',
          title: '约束条件补强',
          purpose: '针对容易遗漏的从属约束做追加布尔检索。',
          activation_mode: 'conditional',
          activation_summary: '当首轮召回过宽或缺少关键结构时触发。',
        },
      ],
      query_blueprints: [
        {
          semantic_text: '围绕控制器、采集模块、反馈调整和目标输出的专利检索',
          display_search_elements: [
            {
              block_id: 'A',
              element_name: '控制器与执行逻辑',
              keywords_zh: ['控制器', '反馈调节', '执行单元'],
              keywords_en: ['controller', 'feedback adjustment', 'actuator'],
              ipc_cpc_ref: ['G05B', 'H02J'],
            },
            {
              block_id: 'B',
              element_name: '采集与判断模块',
              keywords_zh: ['采集模块', '阈值判断', '状态识别'],
              keywords_en: ['sampling module', 'threshold decision', 'state detection'],
              ipc_cpc_ref: ['G01R'],
            },
          ],
        },
      ],
    },
  ],
}

const makeSummary = (
  sessionId: string,
  title: string,
  phase: string,
  status: string,
  activityState: 'running' | 'paused' | 'none',
  updatedAt: string,
  selectedDocumentCount = 0,
  activePlanVersion: number | null = null,
): AiSearchSessionSummary => ({
  sessionId,
  taskId: sessionId,
  title,
  status,
  phase,
  activityState,
  pinned: false,
  activePlanVersion,
  selectedDocumentCount,
  createdAt: isoAt(-180),
  updatedAt,
})

export const buildAiSearchConversationMockState = (): AiSearchMockState => {
  const questionSession = makeSummary(
    'mock-question',
    'Mock · 待补充问题',
    'awaiting_user_answer',
    'paused',
    'paused',
    isoAt(10),
  )
  const planSession = makeSummary(
    'mock-plan',
    'Mock · 计划待确认',
    'awaiting_plan_confirmation',
    'paused',
    'paused',
    isoAt(20),
    0,
    2,
  )
  const runningSession = makeSummary(
    'mock-running',
    'Mock · 执行中',
    'execute_search',
    'processing',
    'running',
    isoAt(30),
    2,
    2,
  )
  const decisionSession = makeSummary(
    'mock-decision',
    'Mock · 人工决策',
    'awaiting_human_decision',
    'paused',
    'paused',
    isoAt(40),
    2,
    2,
  )
  const completedSession = makeSummary(
    'mock-completed',
    'Mock · 已完成',
    'completed',
    'completed',
    'none',
    isoAt(50),
    3,
    2,
  )

  const sessions = [completedSession, decisionSession, runningSession, planSession, questionSession]

  const snapshotsById: Record<string, AiSearchSnapshot> = {
    'mock-question': {
      session: questionSession,
      run: {
        runId: 'run-question',
        phase: 'awaiting_user_answer',
        status: 'paused',
        planVersion: null,
        selectedDocumentCount: 0,
      },
      conversation: {
        messages: [
          {
            message_id: 'q-user-1',
            role: 'user',
            kind: 'chat',
            content: '帮我分析 CN115791220A 的检索方向。',
            created_at: isoAt(0),
          },
          {
            message_id: 'q-assistant-1',
            role: 'assistant',
            kind: 'chat',
            content: '我先帮你拆成可执行的检索需求，需要先确认你更偏向哪类分析目标。',
            created_at: isoAt(1),
          },
          {
            message_id: 'q-question-1',
            role: 'assistant',
            kind: 'question',
            question_id: 'question-1',
            content: '你希望如何分析 CN115791220A 专利？\n\n1. 现有技术检索：查找可能影响新颖性/创造性的对比文献。\n2. 专利分析：梳理该专利的技术方案、创新点和关键约束。\n\n如果做现有技术检索，请补充全文、关键权利要求或重点检索目标。',
            created_at: isoAt(2),
          },
        ],
        pendingAction: {
          actionType: 'question',
          question_id: 'question-1',
          createdAt: isoAt(2),
        },
      },
      executionMessageQueue: { items: [] },
      plan: { currentPlan: null },
      retrieval: { todos: [], activeTodo: null, documents: { candidates: [], selected: [] } },
      analysis: { activeBatch: null, latestCloseReadResult: null, latestFeatureCompareResult: null },
      artifacts: { attachments: [] },
      analysisSeed: null,
    },
    'mock-plan': {
      session: planSession,
      run: {
        runId: 'run-plan',
        phase: 'awaiting_plan_confirmation',
        status: 'paused',
        planVersion: 2,
        selectedDocumentCount: 0,
      },
      conversation: {
        messages: [
          {
            message_id: 'p-user-1',
            role: 'user',
            kind: 'chat',
            content: '分析 CN115791220A 的现有技术，重点看控制链路。',
            created_at: isoAt(5),
          },
          {
            message_id: 'p-plan-old',
            role: 'assistant',
            kind: 'plan_confirmation',
            plan_version: 1,
            content: '### 检索计划 v1\n\n- 先做宽召回\n- 再补控制器相关关键词',
            created_at: isoAt(6),
          },
          {
            message_id: 'p-plan-current',
            role: 'assistant',
            kind: 'plan_confirmation',
            plan_version: 2,
            content: '### 检索计划 v2\n\n- 聚焦主权利要求的控制链路\n- 先做语义召回，再补布尔检索\n- 命中候选后进入精读和特征对比',
            created_at: isoAt(8),
          },
        ],
        pendingAction: {
          actionType: 'plan_confirmation',
          plan_version: 2,
          confirmation_label: '实施此计划',
          createdAt: isoAt(8),
        },
      },
      executionMessageQueue: { items: [] },
      plan: {
        currentPlan: {
          planVersion: 2,
          executionSpec: planExecutionSpec,
        },
      },
      retrieval: { todos: [], activeTodo: null, documents: { candidates: [], selected: [] } },
      analysis: { activeBatch: null, latestCloseReadResult: null, latestFeatureCompareResult: null },
      artifacts: { attachments: [] },
      analysisSeed: null,
    },
    'mock-running': {
      session: runningSession,
      run: {
        runId: 'run-running',
        phase: 'execute_search',
        status: 'processing',
        planVersion: 2,
        activeRetrievalTodoId: 'todo-2',
        selectedDocumentCount: 2,
      },
      conversation: {
        messages: [
          {
            message_id: 'r-user-1',
            role: 'user',
            kind: 'chat',
            content: '开始执行这个检索计划。',
            created_at: isoAt(10),
          },
          {
            message_id: 'r-assistant-1',
            role: 'assistant',
            kind: 'chat',
            content: '已开始执行检索计划，我会按步骤推进并实时反馈关键状态。',
            created_at: isoAt(11),
          },
          processMessage('r-process-1', isoAt(12), '检索规划', groupStatusMetadata('planner:started', 'planner', '检索规划', 'running')),
          processMessage(
            'r-process-2',
            isoAt(13),
            '读取规划上下文',
            detailMetadata('planner-context:running', 'planner', '检索规划', 'get_planning_context', '读取规划上下文', '读取规划上下文', 'running'),
          ),
          processMessage(
            'r-process-3',
            isoAt(14),
            '读取规划上下文',
            detailMetadata('planner-context:completed', 'planner', '检索规划', 'get_planning_context', '读取规划上下文', '读取规划上下文', 'completed'),
          ),
          processMessage('r-process-4', isoAt(15), '检索规划', groupStatusMetadata('planner:completed', 'planner', '检索规划', 'completed')),
          processMessage('r-process-5', isoAt(16), '检索执行', groupStatusMetadata('query-executor:started', 'query-executor', '检索执行', 'running')),
          processMessage(
            'r-process-6',
            isoAt(17),
            '执行检索步骤',
            detailMetadata('query-step-1:running', 'query-executor', '检索执行', 'run_execution_step', '执行检索步骤', '执行检索步骤', 'running'),
          ),
          processMessage(
            'r-process-7',
            isoAt(18),
            '提交执行步骤摘要',
            detailMetadata('query-step-2:running', 'query-executor', '检索执行', 'run_execution_step', '执行检索步骤', '提交执行步骤摘要', 'running'),
          ),
        ],
        pendingAction: null,
      },
      executionMessageQueue: {
        items: [
          {
            queueMessageId: 'queue-1',
            runId: 'run-running',
            content: '补充关注传感器采样频率相关方案',
            ordinal: 1,
            createdAt: isoAt(19),
          },
        ],
      },
      plan: {
        currentPlan: {
          planVersion: 2,
          executionSpec: planExecutionSpec,
        },
      },
      retrieval: {
        todos: [
          { todo_id: 'todo-1', title: '整理检索要素', status: 'completed' },
          { todo_id: 'todo-2', title: '执行首轮语义召回', status: 'in_progress' },
          { todo_id: 'todo-3', title: '筛选候选文献', status: 'pending' },
        ],
        activeTodo: { todo_id: 'todo-2', title: '执行首轮语义召回', status: 'in_progress' },
        documents: {
          candidates: [],
          selected: [
            { documentId: 'doc-selected-1', title: 'CN101234567A' },
            { documentId: 'doc-selected-2', title: 'US2024001234A1' },
          ],
        },
      },
      analysis: { activeBatch: null, latestCloseReadResult: null, latestFeatureCompareResult: null },
      artifacts: { attachments: [] },
      analysisSeed: null,
    },
    'mock-decision': {
      session: decisionSession,
      run: {
        runId: 'run-decision',
        phase: 'awaiting_human_decision',
        status: 'paused',
        planVersion: 2,
        selectedDocumentCount: 2,
      },
      conversation: {
        messages: [
          {
            message_id: 'd-user-1',
            role: 'user',
            kind: 'chat',
            content: '继续推进精读和对比。',
            created_at: isoAt(20),
          },
          {
            message_id: 'd-assistant-1',
            role: 'assistant',
            kind: 'chat',
            content: '自动检索已暂停，当前需要你确认是否继续检索，或按现有结果完成。',
            created_at: isoAt(21),
          },
        ],
        pendingAction: {
          actionType: 'human_decision',
          available: true,
          summary: '当前已有 2 篇文献进入已选集合，另有 2 篇 shortlisted 候选可做人工复核。',
          reason: '连续两轮新增有限，建议人工判断是否继续扩大召回范围。',
          roundCount: 3,
          noProgressRoundCount: 2,
          selectedCount: 2,
          createdAt: isoAt(22),
        },
      },
      executionMessageQueue: { items: [] },
      plan: {
        currentPlan: {
          planVersion: 2,
          executionSpec: planExecutionSpec,
        },
      },
      retrieval: {
        todos: [
          { todo_id: 'todo-1', title: '执行首轮语义召回', status: 'completed' },
          { todo_id: 'todo-2', title: '候选粗筛', status: 'completed' },
          { todo_id: 'todo-3', title: '重点精读', status: 'paused' },
        ],
        activeTodo: { todo_id: 'todo-3', title: '重点精读', status: 'paused' },
        documents: {
          selected: [
            {
              documentId: 'doc-1',
              title: 'CN101234567A',
              pn: 'CN101234567A',
              source_type: 'patent',
              publication_date: '2019-05-01',
              claim_ids_json: ['1', '3'],
              reviewReason: '控制器与反馈调节逻辑高度相关',
              evidence_summary: '说明书第 12-15 段公开了采样、判断与执行闭环。',
              key_passages_json: [{ reason: '控制闭环', passage: '采样模块检测目标状态，并驱动控制器调整输出。' }],
              manualAction: 'can_remove',
            },
            {
              documentId: 'doc-2',
              title: 'US2024001234A1',
              pn: 'US2024001234A1',
              source_type: 'patent',
              publication_date: '2024-01-11',
              claim_ids_json: ['2'],
              reviewReason: '包含目标阈值判断与执行单元联动',
              evidence_summary: '权利要求 2 与实施例 3 描述了状态识别和控制输出。',
              manualAction: 'can_remove',
            },
          ],
          candidates: [
            {
              documentId: 'doc-3',
              title: 'CN109876543A',
              pn: 'CN109876543A',
              source_type: 'patent',
              publication_date: '2020-06-08',
              reviewReason: '含有接近的采样判断路径',
              abstract: '公开了一种根据采样结果执行阈值判断并联动控制单元的方案。',
              manualAction: 'can_review',
              evidenceReady: false,
            },
            {
              documentId: 'doc-4',
              title: 'WO2023123456A1',
              pn: 'WO2023123456A1',
              source_type: 'patent',
              publication_date: '2023-07-12',
              reviewReason: '控制器与传感单元耦合明显',
              evidence_summary: '摘要及实施例 2 已可见相似控制链路。',
              manualAction: 'can_review',
              evidenceReady: true,
            },
          ],
        },
      },
      analysis: { activeBatch: null, latestCloseReadResult: null, latestFeatureCompareResult: null },
      artifacts: { attachments: [] },
      analysisSeed: null,
    },
    'mock-completed': {
      session: completedSession,
      run: {
        runId: 'run-completed',
        phase: 'completed',
        status: 'completed',
        planVersion: 2,
        selectedDocumentCount: 3,
      },
      conversation: {
        messages: [
          {
            message_id: 'c-user-1',
            role: 'user',
            kind: 'chat',
            content: '按当前结果完成并输出总结。',
            created_at: isoAt(30),
          },
          {
            message_id: 'c-assistant-1',
            role: 'assistant',
            kind: 'chat',
            content: [
              '已完成本轮 AI 检索，最终保留 3 篇对比文献。',
              '',
              '### 检索报告',
              '',
              '**结论摘要**',
              '',
              '- 已确认 3 篇文献可作为后续精读与特征对比输入。',
              '- 现有结果已经覆盖控制链路、阈值判断、传感反馈三个核心方向。',
              '- 继续扩大召回的边际收益较低，建议先进入人工复核和正式对比。',
              '',
              '**推荐保留文献**',
              '',
              '1. `CN101234567A`：控制器与反馈调节逻辑高度相关，已见完整闭环描述。',
              '2. `US2024001234A1`：包含目标阈值判断与执行单元联动，可补充执行侧证据。',
              '3. `WO2023123456A1`：控制器与传感单元耦合明显，适合作为链路相似文献。',
              '',
              '**下一步建议**',
              '',
              '- 以 `CN101234567A` 为主对比文献开展权利要求映射。',
              '- 对 `US2024001234A1` 补充命中段落定位，避免仅停留在摘要层。',
              '- 下载结果包后，优先查看文献摘要、关键证据和步骤记录。',
            ].join('\n'),
            created_at: isoAt(31),
          },
        ],
        pendingAction: null,
      },
      executionMessageQueue: { items: [] },
      plan: {
        currentPlan: {
          planVersion: 2,
          executionSpec: planExecutionSpec,
        },
      },
      retrieval: {
        todos: [
          { todo_id: 'todo-1', title: '执行首轮语义召回', status: 'completed' },
          { todo_id: 'todo-2', title: '候选粗筛', status: 'completed' },
          { todo_id: 'todo-3', title: '重点精读', status: 'completed' },
          { todo_id: 'todo-4', title: '特征对比', status: 'completed' },
        ],
        activeTodo: null,
        documents: {
          candidates: [],
          selected: [
            { documentId: 'doc-1', title: 'CN101234567A' },
            { documentId: 'doc-2', title: 'US2024001234A1' },
            { documentId: 'doc-4', title: 'WO2023123456A1' },
          ],
        },
      },
      analysis: { activeBatch: null, latestCloseReadResult: null, latestFeatureCompareResult: null },
      artifacts: {
        attachments: [
          {
            attachmentId: 'result_bundle',
            kind: 'result_bundle',
            name: 'AI 检索结果_Mock · 已完成.zip',
            downloadUrl: 'data:application/zip;base64,UEsFBgAAAAAAAAAAAAAAAAAAAAAAAA==',
            mediaType: 'application/zip',
            sizeBytes: 28416,
            createdAt: isoAt(31),
            isPrimary: true,
          },
          {
            attachmentId: 'feature_comparison_csv',
            kind: 'feature_comparison_csv',
            name: 'AI 检索特征对比_Mock · 已完成.csv',
            downloadUrl: 'data:text/csv;base64,Y29sMSxjb2wyCg==',
            mediaType: 'text/csv',
            sizeBytes: 2048,
            createdAt: isoAt(31),
            isPrimary: false,
          },
        ],
      },
      analysisSeed: null,
    },
  }

  const runtimeById: Record<string, MockRuntime> = {
    'mock-question': {
      activeRun: null,
      pendingAssistantMessage: null,
      phaseMarkers: [],
      activeSubagentStatuses: {},
      streaming: false,
      error: '',
    },
    'mock-plan': {
      activeRun: null,
      pendingAssistantMessage: null,
      phaseMarkers: [],
      activeSubagentStatuses: {},
      streaming: false,
      error: '',
    },
    'mock-running': {
      activeRun: { runKey: 'mock-run-1', sessionId: 'mock-running', startedAt: isoAt(12), phase: 'execute_search' },
      pendingAssistantMessage: null,
      phaseMarkers: [
        { id: 'phase-mock-run-1-drafting', runKey: 'mock-run-1', phase: 'drafting_plan', createdAt: isoAt(11), endedAt: isoAt(12) },
        { id: 'phase-mock-run-1-execute', runKey: 'mock-run-1', phase: 'execute_search', createdAt: isoAt(12), endedAt: null },
      ],
      activeSubagentStatuses: {
        'query-executor': {
          name: 'query-executor',
          label: '检索执行',
          statusText: '执行检索步骤',
          startedAt: isoAt(16),
        },
      },
      streaming: false,
      error: '',
    },
    'mock-decision': {
      activeRun: null,
      pendingAssistantMessage: null,
      phaseMarkers: [],
      activeSubagentStatuses: {},
      streaming: false,
      error: '',
    },
    'mock-completed': {
      activeRun: null,
      pendingAssistantMessage: null,
      phaseMarkers: [],
      activeSubagentStatuses: {},
      streaming: false,
      error: '',
    },
  }

  return {
    currentSessionId: 'mock-running',
    sessions,
    snapshotsById,
    runtimeById,
  }
}
