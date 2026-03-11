# Patent Analysis Agent

## 1. 模块定位

`patent_analysis` 是单篇专利分析流水线，按固定阶段执行“下载/解析/结构化/知识提取/视觉处理/形式检查/报告生成/检索策略/渲染”。

入口文件：`agents/patent_analysis/main.py`

---

## 2. 核心能力

1. 支持按专利号自动下载 PDF，或直接使用上传 PDF。
2. 自动结构化专利内容（著录项、权利要求、说明书、附图）。
3. 抽取部件知识库并做附图 OCR 识别与标注。
4. 做附图标记一致性形式检查。
5. 生成“技术分析报告 + 检索策略建议书”。
6. 渲染 Markdown 和 PDF 最终报告。

---

## 3. 执行流程（LangGraph）

`create_workflow()` 定义的主流程如下：

1. `download` 下载专利文档
2. `parse` 解析 PDF（生成 `raw.md` 与图像）
3. `transform` 将 Markdown 转为结构化 `patent.json`
4. `extract` 知识提取（部件库 `parts.json`）
5. `vision` 视觉处理（OCR + 标注图 + `image_parts.json`）
6. 并行分支：`check` 与 `generate`
7. `check_generate_join` 汇聚并行结果
8. 并行分支：`search_matrix` 与 `search_semantic`
9. `search_join` 汇聚检索策略（`search_strategy.json`）
10. `render` 渲染 Markdown/PDF（`<pn>.md`, `<pn>.pdf`）

说明：`check/generate` 为并行节点；任一节点失败会路由到 `handle_error` 后结束。
默认执行路径已接入 LangGraph checkpoint（按 `task_id` 作为 `thread_id`）。

---

## 4. 各阶段输入/输出逻辑

## 4.1 download

- 若 `output/<pn>/raw.pdf` 已存在：跳过下载。
- 若有上传文件：复制到 `raw.pdf`。
- 否则调用智慧芽下载。

## 4.2 parse

- 输入：`raw.pdf`
- 输出：`mineru_raw/raw.md`、`mineru_raw/images/*`
- 若 `raw.md` 已存在则跳过。

## 4.3 transform

- 输入：`raw.md`
- 调用 `extract_structured_data(..., method="hybrid")`
- 输出：`patent.json`

## 4.4 extract

- 调用 `KnowledgeExtractor.extract_entities()`
- 从摘要、附图说明、具体实施方式抽取带附图标号的部件多维知识图谱
- 输出：`parts.json`
- `parts.json` 结构（key 为归一化后部件标号，仅字母数字小写）：
  - `name`: 部件名称
  - `function`: 功能作用
  - `hierarchy`: 层级归属（缺失时为 `null`）
  - `spatial_connections`: 空间方位与连接关系
  - `motion_state`: 运动/物理状态
  - `attributes`: 材质/形状等显著属性
- 说明：`parts.json` 已升级为新结构，本模块不再兼容旧版仅 `name/function` 的历史产物。

## 4.5 vision

- 调用 `VisualProcessor.process_patent_images()`
- 只处理摘要附图与 drawings 中目标图，其余图片直接拷贝。
- OCR 引擎支持：
  - `OCR_ENGINE=local`（PaddleOCR）
  - `OCR_ENGINE=online`（在线 OCR API）
  - 其他值自动回退 `local`
- 对 `local/online` OCR 初筛结果执行 VLM 混合纠错（结合 `parts.json` 上下文）：
  - 清洗错认/噪点
  - 补全漏检标号
  - 产出纠错后的标注图与 `image_parts.json`
- 输出：
  - 标注图片目录：`annotated_images/`
  - 图-部件映射：`image_parts.json`

## 4.6 check

- 调用 `FormalExaminer.check()`
- 检查说明书部件标号与附图识别标号的一致性：
  - 说明书有、附图无
  - 附图有、说明书无
- 该阶段为纯规则检查，不再进行二次视觉复核。
- `check.json` 仅包含：
  - `consistency`：面向用户的最终一致性结论
- 输出：`check.json`

## 4.7 generate

- 调用 `ContentGenerator.generate_report_json()`
- 关键生成链路：
  1. 领域与技术问题分析
  2. 标题/摘要/技术方案综合
  3. 背景知识百科生成
  4. 技术特征抽取
  5. 技术效果验证（含 TCS 打分）
  6. 图解说明生成
- 图解说明阶段仅依赖图像视觉识别结果与全局部件库，不再依赖说明书段落滑窗上下文。
- 输出：`report.json`

## 4.8 search_matrix + search_semantic + search_join

- `search_matrix` 调用 `SearchStrategyGenerator.build_search_matrix()`
- `search_semantic` 调用 `SearchStrategyGenerator.build_semantic_strategy()`
- `search_join` 汇总两路结果并写入 `search_strategy.json`
- 生成两部分：
  - `search_matrix`：检索要素表（Search Element、关键词扩展、IPC/CPC）
  - `semantic_strategy`：语义检索高密度 Query
- `search_matrix` 字段（v2）：
  - `element_name`：检索要素名称
  - `element_role`：检索角色（`Subject` / `KeyFeature` / `Functional`）
  - `element_type`：要素属性（仅限以下 5 类）
    - `Product_Structure`：实体结构/装置件
    - `Method_Process`：方法/动作/工艺步骤
    - `Algorithm_Logic`：算法/数据处理逻辑
    - `Material_Composition`：材料/化学组分
    - `Parameter_Condition`：参数/数值/关系限定
  - `keywords_zh`：中文关键词扩展（同义词/上下位概念/俗称）
  - `keywords_en`：英文关键词扩展（Patentese/截词符）
  - `ipc_cpc_ref`：关联分类号
- 说明：`search_matrix` 仅支持以上新字段，不提供旧字段向后兼容。
- 输出：`search_strategy.json`

## 4.9 render

- 调用 `ReportRenderer.render(...)`
- 合并三块内容：
  - 分析报告
  - 形式缺陷报告
  - 检索策略建议
- 检索要素表中的 `element_type` 以小标签形式展示在 `Search Element` 名称下方，不单独占列。
- 生成：
  - `output/<task_id>/<pn>.md`
  - `output/<task_id>/<pn>.pdf`

---

## 5. 缓存与复用机制

本模块在多个阶段采用“文件存在即复用”策略：

1. `raw.md`、`patent.json`、`parts.json`、`image_parts.json`、`report.json`、`search_strategy.json` 若已存在，优先读取而非重算。
2. `generate`、`search_matrix`、`search_semantic` 节点使用 `StepCache` 保存节点缓存，统一落盘到 `output/<task_id>/.cache/*.json`（例如 `generate_core_cache.json`、`search_matrix_cache.json`、`search_semantic_cache.json`）。

说明：
- `report.json`、`search_strategy.json` 仍作为中间产物复用文件保留。
- 旧版 `*_intermediate.json` 不再写入。

效果：同一 PN 重跑时显著减少 LLM/OCR 计算成本。

---

## 6. 目录结构与产物

项目路径由 `config.Settings.get_project_paths(workspace_id, artifact_name)` 统一管理：

`output/<task_id>/`

- 输入与中间文件
  - `raw.pdf`
  - `mineru_raw/raw.md`
  - `mineru_raw/images/*`
- 结构化数据
  - `patent.json`
  - `parts.json`
  - `image_parts.json`
  - `check.json`
  - `report.json`
  - `search_strategy.json`
- 最终产物
  - `<pn>.md`
  - `<pn>.pdf`
  - `annotated_images/*`

---

## 7. 任务进度与状态同步

当通过后端任务系统运行时，流水线会在每步开始/结束调用 `task_manager.update_progress(...)`，步骤名称来自 `DEFAULT_PIPELINE_STEPS`：

- 下载专利文档
- 解析 PDF 文件
- 专利结构化转换
- 知识提取
- 视觉处理
- 形式缺陷检查
- 报告内容生成
- 检索策略生成
- 渲染报告

---

## PN 命名规则

- 若任务创建时提供了 `pn`，最终产物命名直接使用该 `pn`。
- 若未提供 `pn`（上传 PDF 场景），在结构化后从 `bibliographic_data.publication_number` 提取用于产物命名。
- 输出目录始终按 `task_id` 隔离：`output/<task_id>/`。

---

## 8. 取消与异常处理

1. 每个节点执行前检查取消信号，若收到取消则返回 `status=cancelled`。
2. 任一节点异常会记录到 `errors`，并通过 `handle_error` 结束工作流，状态为 `failed`。
3. 渲染后会检查 PDF 是否存在且非空，避免“假成功”。

---

## 9. 运行方式

## 9.1 单个专利号

```bash
python -m agents.patent_analysis.main --pn CN116745575A
```

## 9.2 上传 PDF

```bash
python -m agents.patent_analysis.main --upload-file /path/to/raw.pdf
```

---

## 10. 关键环境变量

- `ZHIHUIYA_USERNAME` / `ZHIHUIYA_PASSWORD`：智慧芽下载
- `OCR_ENGINE`：`local` / `online`（其他值自动回退 `local`）
- `OCR_BASE_URL` / `OCR_API_KEY`：在线 OCR 模式
- `VISION_MAX_WORKERS`：统一视觉并发（在线 OCR + 附图视觉分析），默认 `4`
- `LLM_*`：报告与检索策略生成
- `APP_OUTPUT_DIR`：输出根目录

---

## 11. 与 office_action_reply 的关系

两者职责不同：

1. `patent_analysis`：单篇专利技术分析与检索策略生成。
2. `office_action_reply`：围绕审查意见答复的争议核查与裁决报告生成。

共享能力主要体现在通用模块：

- 文档解析器
- 专利结构化提取
- 检索客户端
- 报告渲染工具
