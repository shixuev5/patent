import type {
  AiSearchMessageSegment,
  AiSearchPhaseMarker,
  AiSearchSessionSummary,
  AiSearchSnapshot,
  AiSearchSubagentStatus,
} from '~/types/aiSearch'

type MockRuntime = {
  activeRun: { runKey: string, sessionId: string, startedAt: string, phase: string } | null
  messageSegments: AiSearchMessageSegment[]
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

const replayPlanExecutionSpec = JSON.parse(String.raw`{
  "search_scope": {
    "objective": "检索可能构成对比文件的现有技术，针对专利 CN115541268A《基于全状态协同感知的轨道交通综合试验数据融合与评估系统》",
    "applicants": ["中车成都机车车辆有限公司"],
    "filing_date": "2022-09-22",
    "priority_date": null,
    "languages": [],
    "databases": ["zhihuiya"],
    "excluded_items": [],
    "source": {}
  },
  "constraints": {
    "time_range": "2010-2025",
    "language": ["zh", "en"],
    "document_type": "patent"
  },
  "execution_policy": {
    "dynamic_replanning": true,
    "planner_visibility": "step_summary_only",
    "max_step_attempts": 3,
    "max_rounds": 3,
    "max_no_progress_rounds": 2,
    "max_selected_documents": 5,
    "decision_on_exhaustion": true
  },
  "sub_plans": [
    {
      "sub_plan_id": "SP001",
      "title": "轨道交通试验数据融合与评估系统核心检索",
      "goal": "检索与轨道交通综合试验数据融合、全状态协同感知、数据同步采集与融合评估相关的现有技术",
      "semantic_query_text": "轨道交通 试验系统 数据融合 协同感知 同步采集 轨旁车载 数据传输 存储中心 远程仿真",
      "search_elements": [
        {
          "element_name": "轨道交通试验系统",
          "keywords_zh": ["轨道交通", "铁路", "地铁", "试验系统", "测试系统", "综合试验"],
          "keywords_en": ["rail transit", "railway", "subway", "test system", "testing system", "comprehensive test"],
          "block_id": "",
          "notes": "Block A - 应用场景与系统类型"
        },
        {
          "element_name": "同时域数据同步采集",
          "keywords_zh": ["同步采集", "同时域", "时间同步", "数据采集", "实时采集", "多源同步"],
          "keywords_en": ["synchronous acquisition", "simultaneous", "time synchronization", "data acquisition", "real-time acquisition", "multi-source synchronization"],
          "block_id": "",
          "notes": "Block B1 - 核心技术特征1"
        },
        {
          "element_name": "轨旁与车载数据融合",
          "keywords_zh": ["轨旁", "车载", "数据融合", "路侧", "车辆端", "信息融合"],
          "keywords_en": ["wayside", "onboard", "data fusion", "trackside", "vehicle-side", "information fusion"],
          "block_id": "",
          "notes": "Block B2 - 核心技术特征2"
        },
        {
          "element_name": "高速数据传输通道",
          "keywords_zh": ["高速传输", "数据传输", "传输通道", "通信链路", "宽带传输"],
          "keywords_en": ["high-speed transmission", "data transmission", "transmission channel", "communication link", "broadband transmission"],
          "block_id": "",
          "notes": "Block C - 技术支撑特征"
        },
        {
          "element_name": "统一数据存储中心",
          "keywords_zh": ["数据存储", "存储中心", "统一存储", "数据中心", "集中存储"],
          "keywords_en": ["data storage", "storage center", "unified storage", "data center", "centralized storage"],
          "block_id": "",
          "notes": "Block E - 系统架构特征"
        },
        {
          "element_name": "远程仿真控制",
          "keywords_zh": ["远程仿真", "仿真控制", "远程测试", "虚拟仿真", "远程控制"],
          "keywords_en": ["remote simulation", "simulation control", "remote testing", "virtual simulation", "remote control"],
          "block_id": "",
          "notes": "Block E - 功能特征"
        },
        {
          "element_name": "打破信息孤岛与时空离散",
          "keywords_zh": ["信息孤岛", "时空离散", "数据整合", "信息集成", "系统整合"],
          "keywords_en": ["information silo", "spatiotemporal discrete", "data integration", "information integration", "system integration"],
          "block_id": "",
          "notes": "技术问题与效果"
        }
      ],
      "retrieval_steps": [
        {
          "step_id": "RS001",
          "title": "核心特征组合检索（Block A + B1 + B2）",
          "purpose": "优先检索最核心的技术组合：轨道交通试验系统中的同步采集与轨旁车载数据融合",
          "feature_combination": "A + B1 + B2",
          "language_strategy": "中英文混合检索，优先中文",
          "ipc_cpc_mode": "hybrid",
          "ipc_cpc_codes": ["G01D", "G06F16", "B61L27", "G08G1", "H04L67"],
          "expected_recall": "50-200篇",
          "fallback_action": "若召回过低，放宽至A+B1或A+B2组合",
          "query_blueprint_refs": ["QB001_A_B1_B2"],
          "phase_key": "execute_search",
          "activation_mode": "immediate",
          "depends_on_step_ids": [],
          "activation_conditions": { "any_of": [] },
          "activation_summary": "立即执行，作为首轮核心检索",
          "probe_summary": {}
        },
        {
          "step_id": "RS002",
          "title": "支撑技术检索（Block C + E）",
          "purpose": "检索高速数据传输通道与统一数据存储中心、远程仿真相关技术",
          "feature_combination": "C + E",
          "language_strategy": "中英文混合检索",
          "ipc_cpc_mode": "hybrid",
          "ipc_cpc_codes": ["H04L29", "G06F3", "G06F16", "G05B17"],
          "expected_recall": "100-300篇",
          "fallback_action": "若召回过高，增加轨道交通领域限定",
          "query_blueprint_refs": ["QB002_C_E"],
          "phase_key": "execute_search",
          "activation_mode": "immediate",
          "depends_on_step_ids": [],
          "activation_conditions": { "any_of": [] },
          "activation_summary": "立即执行，作为第二轮支撑技术检索",
          "probe_summary": {}
        },
        {
          "step_id": "RS003",
          "title": "完整系统检索",
          "purpose": "检索包含完整系统架构的现有技术，覆盖全部核心要素",
          "feature_combination": "A + B1 + B2 + C + E",
          "language_strategy": "中文优先，辅以英文",
          "ipc_cpc_mode": "hybrid",
          "ipc_cpc_codes": ["G01D", "G06F16", "B61L27", "H04L67", "G05B17"],
          "expected_recall": "20-80篇",
          "fallback_action": "若召回过低，简化为A+B1+B2+E组合",
          "query_blueprint_refs": ["QB003_full_system"],
          "phase_key": "execute_search",
          "activation_mode": "immediate",
          "depends_on_step_ids": ["RS001", "RS002"],
          "activation_conditions": {
            "any_of": [
              { "signal": "RS001_completed", "equals": true },
              { "signal": "RS002_completed", "equals": true }
            ]
          },
          "activation_summary": "在RS001和RS002完成后执行，用于检索完整系统方案",
          "probe_summary": {}
        },
        {
          "step_id": "RS004",
          "title": "技术问题与效果扩展检索",
          "purpose": "针对打破信息孤岛与时空离散的技术问题进行扩展检索",
          "feature_combination": "A + 信息孤岛/时空离散",
          "language_strategy": "中文为主",
          "ipc_cpc_mode": "hybrid",
          "ipc_cpc_codes": ["G06F16", "G01D", "B61L27"],
          "expected_recall": "30-100篇",
          "fallback_action": "若召回过低，仅使用信息孤岛或数据整合关键词",
          "query_blueprint_refs": ["QB004_expanded"],
          "phase_key": "execute_search",
          "activation_mode": "conditional",
          "depends_on_step_ids": ["RS003"],
          "activation_conditions": {
            "any_of": [
              { "signal": "recall_quality", "equals": "too_narrow" },
              { "signal": "gap_identified", "equals": "technical_problem_coverage" }
            ]
          },
          "activation_summary": "当核心检索召回不足或发现技术效果覆盖缺口时激活",
          "probe_summary": {}
        }
      ],
      "query_blueprints": [
        { "batch_id": "QB001_A_B1_B2", "goal": "检索轨道交通试验系统中同时域同步采集与轨旁车载数据融合的核心技术", "sub_plan_id": "SP001" },
        { "batch_id": "QB002_C_E", "goal": "检索高速数据传输与统一存储中心及远程仿真相关技术", "sub_plan_id": "SP001" },
        { "batch_id": "QB003_full_system", "goal": "检索完整的轨道交通试验数据融合与评估系统", "sub_plan_id": "SP001" },
        { "batch_id": "QB004_expanded", "goal": "扩展检索：信息孤岛打破与时空离散问题解决的相关技术", "sub_plan_id": "SP001" }
      ],
      "classification_hints": [
        { "hint": "优先关注轨道交通、铁路、地铁领域的试验测试系统", "relevance": "high" },
        { "hint": "重点关注数据同步、时间同步、多源数据融合相关专利", "relevance": "high" },
        { "hint": "关注轨旁设备与车载设备之间的数据交互与融合技术", "relevance": "high" },
        { "hint": "注意区分单纯的监控系统与综合试验数据融合评估系统", "relevance": "medium" },
        { "hint": "远程仿真与虚拟测试技术可能分布在多个技术领域", "relevance": "medium" }
      ]
    }
  ]
}`)

const replayPlannerMessages = [
  {
    message_id: 'replay-planner-chat',
    role: 'assistant',
    kind: 'chat',
    stream_status: 'completed',
    created_at: '2026-04-17T13:41:29.997104Z',
    metadata: {
      source_agent: 'planner',
      source_role: 'subagent',
      segment_id: 'a9aaf82085cb471691a422c789c47ae6',
      content_type: 'markdown',
    },
    content: String.raw`# AI 检索计划草案

## 【检索目标】

本检索计划旨在针对专利 CN115541268A《基于全状态协同感知的轨道交通综合试验数据融合与评估系统》检索可能构成对比文件的现有技术。核心目标是系统性地查找在轨道交通试验领域中，涉及全状态协同感知、同时域数据同步采集、轨旁与车载数据融合、高速数据传输、统一数据存储及远程仿真控制等关键技术特征的已有专利文献，为专利性分析提供充分的对比文件支撑。

---

## 【检索边界】

| 约束维度 | 具体限制 |
|---------|---------|
| **时间范围** | 2010 年 -2025 年（覆盖目标专利申请日前的完整技术周期） |
| **地域范围** | 全球专利（CN、US、EP、JP、KR、WO 等主要专利局） |
| **文献类型** | 发明专利、实用新型（优先发明专利） |
| **语言范围** | 中文、英文（辅以日文、韩文摘要） |
| **数据库范围** | 智慧芽专利数据库（zhihuiya） |
| **技术领域** | 轨道交通试验测试、数据融合、同步采集、远程仿真相关技术 |

---

## 【检索要素】

| 要素编号 | 要素名称 | 中文关键词 | 英文关键词 | 所属 Block | 技术定位 |
|---------|---------|-----------|-----------|-----------|---------|
| SE001 | 轨道交通试验系统 | 轨道交通、铁路、地铁、试验系统、测试系统、综合试验 | rail transit, railway, subway, test system, testing system, comprehensive test | Block A | 应用场景与系统类型 |
| SE002 | 同时域数据同步采集 | 同步采集、同时域、时间同步、数据采集、实时采集、多源同步 | synchronous acquisition, simultaneous, time synchronization, data acquisition, real-time acquisition, multi-source synchronization | Block B1 | 核心技术特征 1 |
| SE003 | 轨旁与车载数据融合 | 轨旁、车载、数据融合、路侧、车辆端、信息融合 | wayside, onboard, data fusion, trackside, vehicle-side, information fusion | Block B2 | 核心技术特征 2 |
| SE004 | 高速数据传输通道 | 高速传输、数据传输、传输通道、通信链路、宽带传输 | high-speed transmission, data transmission, transmission channel, communication link, broadband transmission | Block C | 技术支撑特征 |
| SE005 | 统一数据存储中心 | 数据存储、存储中心、统一存储、数据中心、集中存储 | data storage, storage center, unified storage, data center, centralized storage | Block E | 系统架构特征 |
| SE006 | 远程仿真控制 | 远程仿真、仿真控制、远程测试、虚拟仿真、远程控制 | remote simulation, simulation control, remote testing, virtual simulation, remote control | Block E | 功能特征 |
| SE007 | 打破信息孤岛与时空离散 | 信息孤岛、时空离散、数据整合、信息集成、系统整合 | information silo, spatiotemporal discrete, data integration, information integration, system integration | Block E | 技术问题与效果 |

---

## 【分步检索方案】

本检索计划采用**分步递进、先核心后扩展**的策略，共设计 4 个检索步骤：

### 第一步：核心特征组合检索（RS001）
- **检索组合**：Block A（轨道交通试验系统）+ Block B1（同时域数据同步采集）+ Block B2（轨旁与车载数据融合）
- **检索目的**：优先锁定最核心的技术方案，即轨道交通试验场景下实现同步采集与轨旁 - 车载数据融合的现有技术
- **预期召回**：50-200 篇
- **IPC/CPC 分类**：G01D（测量）、G06F16（数据融合）、B61L27（轨道交通控制）、G08G1（交通控制）、H04L67（网络通信）
- **调整策略**：若召回量过低，放宽至 A+B1 或 A+B2 的双要素组合

### 第二步：支撑技术检索（RS002）
- **检索组合**：Block C（高速数据传输通道）+ Block E（统一数据存储中心 + 远程仿真控制）
- **检索目的**：检索系统架构层面的支撑技术，包括数据传输、存储及远程仿真功能
- **预期召回**：100-300 篇
- **IPC/CPC 分类**：H04L29（数据传输）、G06F3（存储接口）、G06F16（数据管理）、G05B17（仿真控制）
- **调整策略**：若召回量过高，增加轨道交通领域限定词进行收窄

### 第三步：完整系统检索（RS003）
- **检索组合**：Block A + B1 + B2 + C + E（全要素组合）
- **检索目的**：直接检索与目标专利整体技术方案高度接近的完整系统方案
- **预期召回**：20-80 篇
- **IPC/CPC 分类**：G01D、G06F16、B61L27、H04L67、G05B17 组合使用
- **调整策略**：若召回量过低，简化为 A+B1+B2+E 组合，暂时放宽高速传输限定

### 第四步：技术问题与效果扩展检索（RS004）
- **检索组合**：Block A + 信息孤岛/时空离散（Block E 的技术问题维度）
- **检索目的**：从技术问题角度扩展检索，查找解决信息孤岛与时空离散问题的相关技术
- **预期召回**：30-100 篇
- **激活条件**：当核心检索召回不足或发现技术效果覆盖缺口时条件性激活
- **调整策略**：若召回量过低，仅使用"信息孤岛"或"数据整合"单一关键词

---

## 【调整策略】

| 场景 | 调整方案 |
|-----|---------|
| **召回量过低（<30 篇）** | 1. 放宽关键词同义扩展（如"同步"扩展为"同时、实时、并发"）<br>2. 减少要素组合数量，从三要素降至双要素<br>3. 放宽 IPC/CPC 分类限制，采用更宽泛的大类 |
| **召回量过高（>500 篇）** | 1. 增加轨道交通领域限定词<br>2. 增加时间同步、数据融合等核心功能限定<br>3. 限制标题/摘要中必须出现关键术语 |
| **相关性不足** | 1. 增加否定词排除无关领域（如排除"医疗、电力"等）<br>2. 调整关键词权重，提升核心要素权重<br>3. 增加 IPC 分类号的精准度 |
| **发现技术空白** | 1. 激活条件性检索步骤（RS004）<br>2. 从技术问题角度重新构建查询<br>3. 扩展至非专利文献（如学术论文）补充检索 |

---

## 【待确认】

1. **目标专利申请日**：请确认 CN115541268A 的确切申请日/优先权日，以便精确设定检索时间截止点
2. **重点对比方向**：请确认是否需要重点关注特定技术特征（如"全状态协同感知"的具体实现方式）
3. **地域优先级**：请确认是否需要优先检索特定国家/地区的专利（如中国、美国、欧洲等）
4. **补充检索需求**：在完成首轮专利检索后，是否需要扩展至非专利文献（学术论文、技术标准等）以补充技术背景

---

*本计划草案版本：v2 | 草案 ID：7abcfd2a2ee3 | 生成时间：2026-01-XX*`,
  },
] as Array<Record<string, any>>

const replayPlanSummary = replayPlannerMessages[0].content

const replayProcessEvents = [
  {
    name: 'planner',
    label: '检索规划',
    eventId: 'planner:completed',
    processType: 'subagent',
    status: 'completed',
    statusText: '检索规划已完成',
    summary: '检索规划',
    subagentName: 'planner',
    subagentLabel: '检索规划',
    displayKind: 'group_status',
    displayGroupKey: 'planner',
    dedupeKey: 'planner',
    type: 'process.completed',
    seq: 528,
    createdAt: '2026-04-17T13:41:29.996226Z',
    runId: null,
  },
] as Array<Record<string, any>>

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

  const replaySession: AiSearchSessionSummary = {
    sessionId: 'mock-replay-5e53e613',
    taskId: 'mock-replay-5e53e613',
    title: 'AI 检索计划 - CN115541268A',
    status: 'paused',
    phase: 'awaiting_plan_confirmation',
    activityState: 'paused',
    sourceTaskId: 'e8f6f776',
    sourceType: 'analysis',
    pinned: false,
    activePlanVersion: 1,
    selectedDocumentCount: 0,
    createdAt: '2026-04-17T13:38:10.785003+00:00',
    updatedAt: '2026-04-17T13:41:36.466779+00:00',
  }

  const sessions = [replaySession, completedSession, decisionSession, runningSession, planSession, questionSession]

  const snapshotsById: Record<string, AiSearchSnapshot> = {
    'mock-replay-5e53e613': {
      session: replaySession,
      run: {
        runId: null,
        phase: 'awaiting_plan_confirmation',
        status: 'paused',
        planVersion: 1,
        selectedDocumentCount: 0,
      },
      conversation: {
        messages: [
          {
            message_id: 'replay-user-seed',
            role: 'user',
            kind: 'chat',
            content: '以下是从 AI 分析带入的检索上下文，请基于这些信息生成一份可审核的检索计划。\n\n## 来源\n- AI 分析任务：e8f6f776\n- 专利号：CN115541268A\n- 标题：基于全状态协同感知的轨道交通综合试验数据融合与评估系统\n\n## 本轮检索目标\n- 围绕专利 CN115541268A《基于全状态协同感知的轨道交通综合试验数据融合与评估系统》检索可能构成对比文件的现有技术。\n\n## 可用检索要素\n- [Block A] 轨道交通试验系统\n- [Block B1] 同时域数据同步采集\n- [Block B2] 轨旁与车载数据融合\n- [Block C] 高速数据传输通道\n- [Block C] 统一数据存储中心\n- [Block C] 远程仿真控制\n- [Block E] 打破信息孤岛与时空离散\n\n请基于以上信息生成一份可审核的检索计划。',
            created_at: '2026-04-15T16:06:59.594403Z',
          },
          ...replayPlannerMessages,
          {
            message_id: 'replay-plan-confirmation',
            role: 'assistant',
            kind: 'plan_confirmation',
            plan_version: 1,
            content: replayPlanSummary,
            created_at: '2026-04-17T13:41:36.419188Z',
            metadata: {
              plan_version: 1,
              plan_summary: replayPlanSummary,
              confirmation_label: '实施此计划',
            },
          },
        ],
        pendingAction: {
          actionId: '740011d3efad',
          actionType: 'plan_confirmation',
          source: 'plan_gate',
          status: 'pending',
          plan_version: 1,
          plan_summary: replayPlanSummary,
          confirmation_label: '实施此计划',
          runId: null,
          payload: {
            plan_version: 1,
            plan_summary: replayPlanSummary,
            confirmation_label: '实施此计划',
          },
          createdAt: '2026-04-17T13:41:36.420719Z',
          updatedAt: '2026-04-17T13:41:36.420719Z',
          resolvedAt: null,
        },
        processEvents: replayProcessEvents,
      },
      stream: {
        lastEventSeq: 538,
      },
      executionMessageQueue: { items: [] },
      plan: {
        currentPlan: {
          planVersion: 1,
          status: 'awaiting_confirmation',
          reviewMarkdown: replayPlanSummary,
          executionSpec: replayPlanExecutionSpec,
        },
      },
      retrieval: {
        todos: [],
        activeTodo: null,
        documents: { candidates: [], selected: [] },
      },
      analysis: {
        activeBatch: null,
        latestCloseReadResult: null,
        latestFeatureCompareResult: null,
      },
      artifacts: { attachments: [] },
      analysisSeed: {
        status: 'completed',
        sourceTaskId: 'e8f6f776',
      },
    },
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
    'mock-replay-5e53e613': {
      activeRun: null,
      messageSegments: [],
      phaseMarkers: [],
      activeSubagentStatuses: {},
      streaming: false,
      error: '',
    },
    'mock-question': {
      activeRun: null,
      messageSegments: [],
      phaseMarkers: [],
      activeSubagentStatuses: {},
      streaming: false,
      error: '',
    },
    'mock-plan': {
      activeRun: null,
      messageSegments: [],
      phaseMarkers: [],
      activeSubagentStatuses: {},
      streaming: false,
      error: '',
    },
    'mock-running': {
      activeRun: { runKey: 'mock-run-1', sessionId: 'mock-running', startedAt: isoAt(12), phase: 'execute_search' },
      messageSegments: [],
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
      messageSegments: [],
      phaseMarkers: [],
      activeSubagentStatuses: {},
      streaming: false,
      error: '',
    },
    'mock-completed': {
      activeRun: null,
      messageSegments: [],
      phaseMarkers: [],
      activeSubagentStatuses: {},
      streaming: false,
      error: '',
    },
  }

  return {
    currentSessionId: 'mock-replay-5e53e613',
    sessions,
    snapshotsById,
    runtimeById,
  }
}
