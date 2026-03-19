# AI Reply 重构实施方案

- 状态: Draft
- 适用范围: `agents/ai_reply`
- 实施策略: 分阶段落地
- 产物: `final_report.json` / `final_report.md` / `final_report.pdf`

## 1. 背景与当前问题

当前 `AI reply` 已具备基础工作流能力：解析审查意见通知书、意见陈述书、权利要求书与对比文件，抽取争议点，完成证据核查、公知常识核查和补充检索，并最终生成报告。但现有实现更接近“一维争议核查器”，与目标中的“审查辅助攻防沙盘”仍有明显差距。

当前代码主链路位于 `agents/ai_reply/main.py`，现有节点顺序为：

`document_processing -> patent_retrieval -> data_preparation -> amendment_tracking -> support_basis_check -> amendment_strategy -> dispute_extraction -> evidence_verification/common_knowledge_verification/topup_search_verification -> verification_join -> report_generation -> final_report_render`

当前问题主要体现在以下几个方面：

### 1.1 报告是一维争议列表，缺少全景 Claims Chart

- 当前最终报告主结构由 `agents/ai_reply/src/nodes/report_generation.py` 组装，核心内容仍是 `summary + disputes + second_office_action_notice`。
- 报告视角围绕“争议项”展开，而不是围绕“权利要求技术特征”展开。
- 审查员和质检员无法在报告开头直接看到全部独立权利要求的完整特征拆解、D1/D2 对应公开情况、区别特征和证据位置。
- 因此，当前报告无法承担标准 `Claim Chart` 或技术特征对比矩阵的作用。

### 1.2 AI 判断、驳回话术、风险提示彼此割裂

- 现有核查节点会给出 `verdict / reasoning / examiner_rejection_reason`，但这些结果没有被组织成统一的案件策略层。
- `examiner_rejection_reason` 只是单段驳回说理，无法承载完整的 `Problem-Solution Approach` 三步法论证。
- 当前没有独立的高风险预警层，无法在“仍可勉强驳回”与“复审/诉讼风险高”之间同时表达。
- 也没有单独的申请人反制策略推演层，导致攻防信息不对称。

### 1.3 `report_markdown.py` 存在硬截断和长文本丢失

- 当前 [agents/ai_reply/src/report_markdown.py](/Users/yanhao/Documents/codes/patent/agents/ai_reply/src/report_markdown.py) 使用 `_truncate(...)` 对特征、理由、证据、二通意见等多处文本直接截断。
- 这会在专利场景中造成信息丢失，因为技术特征、引用原文和说理文本通常不能任意省略。
- 当前 PDF 虽然启用了表格自动换行，但上游在 Markdown 拼装阶段已经主动截断，最终 PDF 无法恢复原文。

### 1.4 证据定位不稳定，存在 `None` 污染和引用粒度不足

- 当前证据引用主要依赖 LLM 返回的 `quote / location / analysis`，但缺少统一的定位规范与清洗层。
- 现有输入链路会接收 OCR、PDF 解析或外部检索结果中的脏文本，若无统一清洗，容易把 `None`、空字符串、脏前缀直接拼进最终报告。
- 当前引用粒度通常停留在“文档名 + 一段文字”，缺少稳定的 `段落号 / 图号 / 附图标记 / chunk_id` 级定位。

### 1.5 当前没有足够的答复报告结构测试护栏

- 现有测试更多覆盖并行执行、claim id 规范化、R2 上传、本地检索等点状能力。
- 针对 `AI reply` 最终报告结构的测试非常少，无法为大规模重构提供足够回归保护。
- 如果直接从模板层改版，容易在无结构测试的情况下引入字段漂移、报告断裂或链路回归。

### 1.6 当前争议点生成是单节点大 Prompt，耗时和 token 成本过高

- 当前 `dispute_extraction` 会一次性向模型发送完整 `office_action.paragraphs` 和整份 `response.content`，属于单节点全文扫描模式。
- 这一步同时承担候选召回、争点对齐、claim 归属、文献归属和 JSON 结构化，职责过重。
- 单次上下文过大导致耗时高、token 消耗大，并且难以通过并行扩展改善性能。
- 后续必须把争议点生成改成“规则预切分 + 分片并行 + 结果归并”的多阶段流程。

## 2. 重构目标

本次重构目标不是单纯提升文案质量，而是把当前 `AI reply` 从争议核查报告升级为可服务审查员、质检员和复核人员的结构化攻防报告。固定产出以下五类核心能力：

### 2.1 特征对比矩阵总览

- 将全部独立权利要求拆解为标准化技术特征。
- 对每个特征建立 D1、D2 及必要时 D3 的逐项映射。
- 标出完全公开、部分公开、未公开、争议性公开和区别特征。
- 让读者在报告开头快速识别“案件命门”。

补充说明：

- 这里的“独立权利要求”不能默认等于权利要求 1。
- 当前系统在专利结构化解析结果中已保留权利要求类型字段，可直接利用：
  - `agents/common/patent_structuring/models.py` 中 `PatentClaim.claim_type`
  - `agents/ai_reply/src/state.py` 中 `StructuredClaim.claim_type`
- 后续矩阵构建必须基于 `claim_type == independent` 自动识别全部独立权利要求，并分别建矩阵。

### 2.2 客观诊断与风险预警

- 当证据链弱、公知常识用法激进、场景迁移风险高、存在结合障碍时，显式输出高风险预警。
- 风险预警与最终驳回路径并行存在，不互相覆盖。
- 即使系统给出可用驳回说理，也必须同时提示该说理的脆弱点和后续风险。

### 2.3 三步法最佳驳回路径

- 对每个核心争议点或组合路线，以 `Problem-Solution Approach` 结构输出：
  - 最接近的现有技术
  - 区别特征
  - 实际解决的技术问题
  - 动机与结合启示
  - 显而易见性论证
- 生成可直接进入二次审查意见的正式驳回表述，而不是泛泛“简单组合”。

### 2.4 补充检索指引

- 当现有 D1/D2 证据链不足时，主动给出检索补强方向。
- 输出 IPC/CPC 建议、布尔检索式、推荐证据类型和优先查找渠道。
- 将“继续找 D3”从隐性建议转成结构化交付。

### 2.5 申请人反制策略预判

- 站在代理师视角预测答辩方向和可能修案方式。
- 明确最可能被攻击的薄弱点。
- 给审查员提供“提前预判、提前核查”的复核清单。

## 3. 目标架构与数据流

本次重构保持现有 LangGraph 主流程骨架不变，但在中间增加两个新的语义层：`claim_feature_matrix_build` 与 `strategic_synthesis`。它们分别解决“全景结构化输入不足”和“攻防策略输出缺位”的问题。

### 3.1 当前架构保留部分

- `document_processing`: 继续负责 PDF/DOCX 解析与审查意见结构化。
- `patent_retrieval`: 继续负责原申请和专利对比文件下载、解析和结构化。
- `data_preparation`: 继续负责统一 `prepared_materials` 和本地检索索引。
- `amendment_tracking` / `support_basis_check` / `amendment_strategy`: 继续承担修改分析与超范围判断。
- `dispute_extraction` 与三个验证节点：继续提供争议级核查结论和证据基础。

复用原则补充：

- `AI reply` 主链路不能强依赖既有 `AI 分析` 结果，避免出现“未先做分析就无法答复”的耦合。
- 但如果同一专利已存在 `AI 分析` 产物，应优先复用，以减少重复解析、重复结构化和重复检索。
- 复用命中时应作为“可选加速路径”，而不是主流程前置条件。

### 3.2 新增核心层一：`claim_feature_matrix_build`

建议位置：`data_preparation` 之后、`amendment_tracking` 之前或与争议提取并列的中间层，最终以工作流形式接入主链路。

目标：

- 从原专利结构化数据中抽取全部独立权利要求，不默认只有权利要求 1。
- 按专利审查场景所需颗粒度拆分技术特征。
- 对每个独立权利要求单独生成矩阵视图，并在总览层输出案件级摘要。
- 将技术特征映射到：
  - 原权利要求全文
  - 审查意见原文段落
  - 对比文件 D1/D2 的公开内容
  - 已有 `disputes` 与 `evidence_assessments`
- 输出标准化的 claims chart / feature matrix。

它主要消费以下上游数据：

- `prepared_materials.original_patent.data.claims`
- `prepared_materials.comparison_documents`
- `prepared_materials.office_action.paragraphs`
- `claims_new_structured`
- `disputes`
- `evidence_assessments`

它主要产出以下结构化字段：

- `feature_inventory`
- `claim_feature_matrix`
- `difference_features`
- `feature_evidence_index`

实现要求：

- `prepared_materials.original_patent.data.claims` 中的 `PatentClaim.claim_type` 是独权识别主依据。
- 若存在新权利要求文件，则 `claims_new_structured` 中的 `StructuredClaim.claim_type` 作为修案后的辅助依据。
- 当一个专利存在多个独立权利要求时，矩阵层必须全部覆盖，不能仅展示权利要求 1。

### 3.3 新增核心层二：`strategic_synthesis`

建议位置：`verification_join` 之后、`report_generation` 之前。

目标：

- 基于 claims chart、争议核查结果、修改风险和补充检索信息，生成统一策略层。
- 对“高风险预警、最佳驳回路径、补充检索指引、申请人反制预判”进行结构化收敛。
- 避免把策略逻辑散落在渲染层或 `report_generation` 中临时拼接。

它主要消费以下上游数据：

- `claim_feature_matrix_build` 输出
- `evidence_assessments`
- `support_findings`
- `added_matter_risk`
- `topup_tasks`
- `reuse_oa_tasks`
- `disputes`

它主要产出以下结构化字段：

- `case_overview`
- `risk_alerts`
- `rejection_paths`
- `search_guidance`
- `applicant_countermoves`
- `dispute_reviews`

### 3.4 目标工作流形态

重构后的目标工作流形态如下：

`document_processing -> patent_retrieval -> data_preparation -> claim_feature_matrix_build -> amendment_tracking -> support_basis_check -> amendment_strategy -> dispute_bundle_build -> dispute_extraction_parallel -> dispute_merge -> evidence_verification/common_knowledge_verification/topup_search_verification -> verification_join -> strategic_synthesis -> report_generation -> final_report_render`

设计原则：

- 保留现有节点的职责边界，避免无关模块返工。
- 先补结构化中间层，再重做最终报告契约。
- 报告渲染只消费最终结构化结果，不承担推理逻辑。
- 争议点生成不再维持单节点全文大 Prompt 模式。

### 3.5 争议点生成提速方案

目标：

- 显著降低 `dispute_extraction` 的单次 token 消耗和端到端耗时。
- 把“全文扫描”改成“候选召回 + 分片抽取 + 结果归并”的分层流程。

建议拆分为三个节点：

- `dispute_bundle_build`
  - 先用规则和轻量逻辑对 OA 与答复文本做预切分。
  - 提取 `claim_ids`、`D1/D2/...`、编号论点和关键词命中的候选段。
  - 生成 `claim bundle` 或 `argument bundle`。
- `dispute_extraction_parallel`
  - 对每个 bundle 单独调用模型。
  - 每次只输入局部 OA 段落和对应答复段落，避免整份文档全量喂入。
  - 允许并行执行。
- `dispute_merge`
  - 对并行结果做去重、claim 规范化、文献归并和空结果清理。

实现原则：

- 第一阶段优先使用规则做候选召回，不把“找候选段”交给大模型。
- 第一轮模型调用可用更轻量模型做粗抽取，第二轮再做结构规范化。
- 输入格式从大 JSON 改为更紧凑的上下文块，减少 token 冗余。
- 缓存粒度从“整次争议提取结果”收细到“bundle 级结果”。

### 3.6 `AI 分析` 结果复用策略

目标：

- 在不增加主链路耦合的前提下，优先复用已有 `AI 分析` 产物，加快 `AI reply` 前处理和中间推理过程。

复用策略：

- 默认策略：无分析缓存时，`AI reply` 仍可独立完成全流程。
- 优先策略：命中分析缓存时，优先加载已有结构化产物，减少重复计算。

建议优先复用的数据：

- `patent.json`
  - 用于复用原专利结构化数据。
  - 直接服务 `prepared_materials.original_patent.data`。
- `claims` 结构化结果
  - 用于复用原始权利要求及 `claim_type`、`parent_claim_ids` 等字段。
  - 直接服务多独权识别与矩阵层。
- `search_matrix`
  - 用于复用技术特征拆分、效果簇和检索要素组织结果。
  - 可作为 `claim_feature_matrix_build` 的初始骨架或候选特征来源。
- `search_semantic_strategy`
  - 用于复用语义检索策略和效果导向查询。
  - 可作为 `search_guidance` 的初稿或补充检索候选输入。
- `analysis.json`
  - 用于复用技术领域、技术方案、技术效果等案件级摘要。
  - 可服务 `case_overview` 和技术问题推导，但不直接替代答复报告主体。

接入原则：

- 复用命中必须经过字段校验和版本兼容检查，不能直接盲信历史产物。
- 复用层只减少重复计算，不改变 `AI reply` 最终输出结构。
- 若复用数据缺失、过旧或结构不完整，应自动回退到 `AI reply` 自身链路。

## 4. 目标数据契约

本次重构直接重做 `final_report` 契约，不以当前 `summary + disputes + second_office_action_notice` 作为主结构。新版 `final_report.json` 顶层字段定义如下：

### 4.1 `case_overview`

用途：

- 提供案件级概览信息。
- 概括申请号、独立权利要求、主要对比文件、案件结论摘要、总体风险等级。

建议字段：

- `application_number`
- `publication_number`
- `independent_claim_ids`
- `comparison_documents`
- `overall_conclusion`
- `overall_risk_level`
- `executive_summary`

### 4.2 `claim_feature_matrix`

用途：

- 作为报告第一屏的标准化 claims chart。

建议字段：

- `independent_claims[]`

其中 `independent_claims[]` 每项建议包含：

- `claim_id`
- `claim_text`
- `claim_type`
- `matrix_summary`
- `features[]`

其中 `features[]` 每项建议包含：

- `feature_id`
- `feature_text`
- `feature_type`
- `doc_matches[]`
- `difference_flag`
- `dispute_link_ids`
- `source_trace[]`

其中 `doc_matches[]` 每项建议包含：

- `doc_id`
- `status`
- `matched_text`
- `location`
- `analysis`

其中 `status` 仅允许：

- `disclosed`
- `partial`
- `absent`
- `disputed`
- `unknown`

### 4.3 `risk_alerts`

用途：

- 显式输出对当前驳回链路的稳健性预警。

建议字段：

- `alert_id`
- `level`
- `title`
- `reason`
- `evidence_refs[]`
- `recommended_action`

其中 `level` 建议固定为：

- `high`
- `medium`
- `low`

### 4.4 `rejection_paths`

用途：

- 以三步法组织最佳驳回路径。

建议字段：

- `path_id`
- `linked_feature_ids`
- `closest_prior_art`
- `distinguishing_features`
- `technical_problem`
- `motivation_to_combine`
- `obviousness_reasoning`
- `official_notice_text`
- `weak_points`

### 4.5 `search_guidance`

用途：

- 输出补充检索指引。

建议字段：

- `guidance_id`
- `trigger_reason`
- `target_feature`
- `suggested_ipc_cpc`
- `boolean_queries`
- `recommended_sources`
- `expected_evidence_type`

### 4.6 `applicant_countermoves`

用途：

- 输出申请人可能的答辩与修案动作。

建议字段：

- `countermove_id`
- `linked_path_id`
- `likely_argument`
- `likely_amendment`
- `examiner_preemption_checklist`

### 4.7 `amendment_review`

用途：

- 保留现有修改分析和超范围风险信息，但作为附属分析层而不是全报告中心。

建议字段延续并规范化：

- `has_claim_amendment`
- `added_matter_risk`
- `early_rejection_reason`
- `added_features`
- `support_findings`
- `reuse_oa_tasks`
- `topup_tasks`

### 4.8 `dispute_reviews`

用途：

- 保留争议级核查审计信息，供附录展示与追溯。

建议字段：

- `dispute_id`
- `claim_ids`
- `feature_text`
- `examiner_opinion`
- `applicant_opinion`
- `assessment`
- `evidence`
- `trace`

### 4.9 `appendix`

用途：

- 承载长证据、完整引用、附图标记、未入正文的原始上下文。

建议字段：

- `evidence_cards`
- `full_quotes`
- `source_index`
- `generation_notes`

## 5. 分阶段实施计划

本项目按 phase 顺序推进，不采用“先改报告外观、再补数据层”的路线。

### Phase 1: 测试基线和 Schema 重构

目标：

- 为 `AI reply` 新版 `final_report` 建立结构测试护栏。
- 引入新版 schema 与中间结构，避免后续多轮返工。
- 明确争议点生成多阶段提速方案及其接口契约。
- 明确 `AI 分析` 结果可选复用的接口契约。

重点内容：

- 梳理当前 `WorkflowState` 和 `final_report` 契约。
- 为新版 `final_report`、`claim_feature_matrix`、`risk_alerts`、`rejection_paths` 定义稳定 schema。
- 为 `dispute_bundle_build / dispute_extraction_parallel / dispute_merge` 定义输入输出契约。
- 为分析结果复用层定义输入字段、版本校验和回退策略。
- 补齐 `report_generation`、渲染层和关键节点的单元测试/快照测试。

### Phase 2: Claims Chart 与证据定位层

目标：

- 实现 `claim_feature_matrix_build`。
- 把全部独立权利要求拆成标准技术特征矩阵，并稳定定位证据引用。
- 完成争议点生成从单节点大 Prompt 到分片并行模式的迁移。
- 接入 `AI 分析` 结果的可选复用能力。

重点内容：

- 技术特征拆分。
- D1/D2 映射。
- 区别特征识别。
- 证据位置规范化。
- `None` 和脏文本清洗。
- 规则预切分、bundle 构建、并行抽取与结果归并。
- `patent.json / claims / search_matrix / search_semantic_strategy` 的可选加载与校验。

### Phase 3: 风险预警与三步法策略层

目标：

- 实现 `strategic_synthesis`。
- 让系统从“出核查结论”升级到“出攻防策略”。

重点内容：

- 高风险预警规则。
- 公知常识防滥用。
- 结合障碍识别。
- 三步法驳回路径生成。
- 补充检索指引与申请人反制预判。

### Phase 4: Markdown/PDF 报告重构

目标：

- 重写最终 Markdown/PDF 模板。
- 用新版 `final_report` 结构替代当前一维争议报告。

重点内容：

- 五步流正文结构。
- 长文本自动换行。
- 移除硬截断。
- 二通说理分段化。
- 附录证据卡与定位信息。

### Phase 5: 样本回归与上线门禁

目标：

- 用真实案件样本验证报告的业务可用性和稳定性。
- 建立上线前门禁。

重点内容：

- 强证据链案件回归。
- 弱证据链案件回归。
- 申请人明显占优案件回归。
- JSON 合法性、PDF 可读性、策略一致性联合验收。

## 6. 每阶段交付物与完成判定

本节用于后续分阶段执行时逐项核对。当前默认各阶段状态均为 `Not Started`。

### 6.1 Phase 1: 测试基线和 Schema 重构

- 阶段状态: Not Started
- 目标:
  - 建立 `AI reply` 报告重构的结构化测试基线。
  - 明确新版 `final_report` schema 和中间结构。
- 代码范围:
  - `agents/ai_reply/src/state.py`
  - `agents/ai_reply/src/nodes/report_generation.py`
  - `agents/ai_reply/src/nodes/dispute_extraction.py`
  - `AI reply` 未来新增的分析结果复用入口
  - `tests/` 下新增或扩展 `AI reply` 报告相关测试
- 产出物:
  - 新版 `final_report` schema
  - 争议点提速相关中间 schema
  - 分析结果复用契约
  - 新测试样例与快照
  - workflow 所需新增状态字段定义
- 验收标准:
  - 测试能覆盖 `case_overview / claim_feature_matrix / risk_alerts / rejection_paths`
  - 争议点生成的 bundle 契约和 merge 契约稳定可测
  - 分析结果复用命中与回退逻辑都可测
  - 在不实现最终策略层前，schema 不再频繁变动
  - `report_generation` 不再依赖旧结构作为唯一输出模型
- 不在本阶段处理的内容:
  - 不实现最终 claims chart 逻辑
  - 不重写 PDF 版式
  - 不完成三步法策略生成

### 6.2 Phase 2: Claims Chart 与证据定位层

- 阶段状态: Not Started
- 目标:
  - 构建权利要求特征矩阵与稳定证据定位层。
- 代码范围:
  - 新增 `claim_feature_matrix_build` 节点
  - 新增 `dispute_bundle_build / dispute_extraction_parallel / dispute_merge` 节点
  - 新增或扩展分析结果复用节点/入口
  - `agents/ai_reply/src/nodes/data_preparation.py`
  - `agents/common/office_action_structuring/*`
  - `agents/common/patent_structuring/*`
- 产出物:
  - `claim_feature_matrix`
  - `feature_evidence_index`
  - 文本清洗与证据位置标准化逻辑
  - 新版争议点提取并行链路
  - 分析结果可选复用链路
- 验收标准:
  - 能基于 `claim_type` 稳定识别全部独立权利要求
  - 每个独立权利要求都能产出对应技术特征矩阵
  - 争议点提取不再依赖一次性全文大 Prompt
  - 在同样输入规模下，争议点生成耗时和 token 成本明显下降
  - 命中分析缓存时能减少重复解析/重复结构化
  - 未命中分析缓存时仍可独立完成 `AI reply` 全流程
  - 每条引用至少具备文档编号和位置说明
  - `None`、空值和 OCR 脏前缀不会进入最终报告结构
- 不在本阶段处理的内容:
  - 不完成完整风险预警逻辑
  - 不重写最终 PDF 模板

### 6.3 Phase 3: 风险预警与三步法策略层

- 阶段状态: Not Started
- 目标:
  - 输出统一策略层，而非分散的核查碎片。
- 代码范围:
  - 新增 `strategic_synthesis` 节点
  - `evidence_verification`
  - `common_knowledge_verification`
  - `topup_search_verification`
  - `verification_join`
- 产出物:
  - `risk_alerts`
  - `rejection_paths`
  - `search_guidance`
  - `applicant_countermoves`
- 验收标准:
  - 弱证据链场景会触发显式高风险预警
  - 三步法字段完整且结构合法
  - 存在结合障碍时能进入 `weak_points` 或风险预警
  - 能输出至少一条可执行的检索补强建议
- 不在本阶段处理的内容:
  - 不处理最终 PDF 版式细节
  - 不做样本上线门禁

### 6.4 Phase 4: Markdown/PDF 报告重构

- 阶段状态: Not Started
- 目标:
  - 将新版结构化数据渲染为可直接交付的正式报告。
- 代码范围:
  - `agents/ai_reply/src/report_markdown.py`
  - `agents/ai_reply/src/report_styles.py`
  - `agents/ai_reply/src/nodes/final_report_render.py`
  - `agents/common/rendering/*`
- 产出物:
  - 新版 Markdown 模板
  - 新版 PDF 样式
  - 五步流正文与附录结构
- 验收标准:
  - 正文按“矩阵总览 -> 风险预警 -> 驳回路径 -> 检索指引 -> 反制预判”输出
  - 无主动硬截断
  - 长文本自动换行
  - 驳回意见不再形成大块文字墙
- 不在本阶段处理的内容:
  - 不新增新的策略推理能力
  - 不做上线审批

### 6.5 Phase 5: 样本回归与上线门禁

- 阶段状态: Not Started
- 目标:
  - 用真实样本验证新链路的业务可用性。
- 代码范围:
  - 测试样本
  - 回归脚本
  - 上线前验收清单
- 产出物:
  - 样本回归记录
  - 上线门禁清单
  - 风险回归结论
- 验收标准:
  - 三类典型案件都能稳定产出 JSON/MD/PDF
  - 高风险案件会同时呈现风险提示与可用驳回路径
  - 样本中不出现 `None` 污染、字段缺失、严重排版错乱
- 不在本阶段处理的内容:
  - 不再新增新的主要功能范围

## 7. 关键风险与依赖

### 7.1 OCR/Markdown 脏文本影响引用质量

- 现有 PDF/DOCX 解析结果可能带来换行噪声、乱码、空值占位和错误段落切分。
- 如果不先建立统一清洗与定位标准，后续 claims chart 和证据引用会持续不稳定。

### 7.2 OA 结构化粒度不足导致矩阵不稳

- 当前审查意见结构化模型仅保留 `paragraph_id / claim_ids / content`。
- 若不能进一步细化文献引用、段落映射和特征对应关系，则 claims chart 会更多依赖 LLM 推断，稳定性不足。

### 7.3 LLM 生成字段需要 Schema 校验与规则补丁

- 风险预警、三步法、动机推导、公知常识判断都包含较强推理成分。
- 必须通过 schema 校验、枚举限制和规则补丁约束输出，不能直接把自由文本当结构化结果。

### 7.4 报告重构缺少真实样本时容易偏离实务

- 如果没有典型案件样本，报告结构可能在形式上更漂亮，但不一定符合审查员的实际使用方式。
- 样本回归必须覆盖“证据强、证据弱、申请人占优”三类典型场景。

## 8. 实施原则

### 8.1 数据层先于模板层

- 先补中间结构，再改最终报告。
- 不允许只靠 Prompt 调整去硬做新版报告目录。

### 8.2 JSON 契约先于 PDF 排版

- 先把 `final_report.json` 设计稳定，再做 Markdown/PDF。
- 渲染层只负责展示，不负责策略拼接。

### 8.3 风险预警优先于驳回说理

- 只要证据链薄弱、结合障碍明显或公知常识使用激进，就必须先亮风险。
- “能找出一条驳回路径”不等于“该路径稳健可用”。

### 8.4 不以 Prompt 堆叠替代结构化建模

- 复杂报告能力必须落到中间 schema、节点职责和规则校验上。
- Prompt 只作为生成手段，不作为系统结构的替代品。

## 9. 实施拆分与文档管理

### 9.1 母计划定位

- 本文档是 `AI reply` 重构的唯一母计划。
- 后续所有 phase 的实施都以本文档为总索引。
- 每次进入新 phase 前，先更新本文档中的阶段状态、已完成项、待处理项和风险变化。

### 9.2 当前轮次范围

- 当前轮次只生成本母计划文档。
- 不修改 `AI reply` 业务代码。
- 不提前进入任一 phase 的实现。

### 9.3 后续配套子文档命名

如后续需要拆出配套实施文档，命名固定如下：

- `docs/ai-reply-phase-1-schema-and-tests.md`
- `docs/ai-reply-phase-2-claim-matrix.md`
- `docs/ai-reply-phase-3-strategy-synthesis.md`
- `docs/ai-reply-phase-4-report-rendering.md`

这些子文档的角色是阶段实施细化，不替代本母计划。

## 10. 验收标准

- 文档读者不需要回看聊天记录，也能理解为什么要重构、重构到什么程度、先做什么后做什么。
- 文档能够直接指导研发拆期，而不是只停留在产品目标层。
- 文档明确了当前代码落点、未来新增节点名称和新版数据契约，避免后续实现时再次做架构决策。
- 文档以中文为主，保留必要英文术语，如 `Claim Chart`、`Problem-Solution Approach`。

## 11. 范围说明

- 本文档只覆盖 `AI reply` 链路，不同步扩展 `ai_review` 和 `patent_analysis`。
- 实施顺序默认严格按 phase 推进。
- 如后续出现新的业务输入或评审要求，应优先更新本母计划，再进入实现阶段。
