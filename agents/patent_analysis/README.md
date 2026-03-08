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

## 3. 执行流程（0~8）

`PatentPipeline.run()` 中的固定顺序如下：

1. `download` 下载专利文档
2. `parse` 解析 PDF（生成 `raw.md` 与图像）
3. `transform` 将 Markdown 转为结构化 `patent.json`
4. `extract` 知识提取（部件库 `parts.json`）
5. `vision` 视觉处理（OCR + 标注图 + `image_parts.json`）
6. `check` 形式缺陷检查（`check.json`）
7. `generate` 生成分析报告 JSON（`report.json`）
8. `search` 生成检索策略 JSON（`search_strategy.json`）
9. `render` 渲染 Markdown/PDF（`<pn>.md`, `<pn>.pdf`）

说明：代码中步骤命名为 0~8（共 9 步），任务系统中也用同名步骤追踪进度。

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
- 从摘要、附图说明、具体实施方式抽取带附图标号的部件及功能
- 输出：`parts.json`

## 4.5 vision

- 调用 `VisualProcessor.process_patent_images()`
- 只处理摘要附图与 drawings 中目标图，其余图片直接拷贝。
- OCR 引擎支持：
  - `OCR_ENGINE=local`（PaddleOCR）
  - `OCR_ENGINE=online`（在线 OCR API）
  - 其他值（VLM OCR）
- 输出：
  - 标注图片目录：`annotated_images/`
  - 图-部件映射：`image_parts.json`

## 4.6 check

- 调用 `FormalExaminer.check()`
- 检查说明书部件标号与附图识别标号的一致性：
  - 说明书有、附图无
  - 附图有、说明书无
- 当存在疑点且配置 `VLM_MODEL_MINI` 时，自动触发图像复核模型二次复核（用于识别 OCR 误报/漏报）。
- 二次复核仅使用官方附图范围（`drawings` + `abstract_figure`），不使用其他非附图图片。
- 二次复核按“附图集合”分组批量调用，减少请求次数并提升对话缓存命中率。
- `check.json` 中会包含：
  - `consistency`：规则检查结论
  - `secondary_review`：图像复核状态、摘要与问题级判断明细
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
- 输出：`report.json`

## 4.8 search

- 调用 `SearchStrategyGenerator.generate_strategy()`
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
2. `ContentGenerator` 与 `SearchStrategyGenerator` 内部还使用 `StepCache` 保存中间步骤结果（例如 `report_intermediate.json`、`search_strategy_intermediate.json`）。

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

1. 每个步骤之间执行 `_check_cancelled()`，若收到取消信号抛出 `PipelineCancelled` 并返回 `status=cancelled`。
2. 任一阶段异常会记录日志并返回 `status=failed`，包含错误信息。
3. 渲染后会检查 PDF 是否存在且非空，避免“假成功”。

---

## 9. 运行方式

## 9.1 单个专利号

```bash
python -m agents.patent_analysis.main --pn CN116745575A
```

## 9.2 多个专利号（逗号分隔）

```bash
python -m agents.patent_analysis.main --pn CN116745575A,CN123456789A
```

## 9.3 从文本文件读取（每行一个 PN）

```bash
python -m agents.patent_analysis.main --file /path/to/pn_list.txt
```

---

## 10. 关键环境变量

- `ZHIHUIYA_USERNAME` / `ZHIHUIYA_PASSWORD`：智慧芽下载
- `OCR_ENGINE`：`local` / `online` / 其他（VLM）
- `OCR_BASE_URL` / `OCR_API_KEY`：在线 OCR 模式
- `LLM_*`：报告与检索策略生成
- `VLM_MODEL_MINI`：形式缺陷检查二次复核图像复核模型（复用 `VLM_API_KEY` / `VLM_BASE_URL`）
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
