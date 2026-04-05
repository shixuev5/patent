# AI 检索 Agent

`agents/ai_search` 保存 AI 检索的 agent 核心能力，不包含 FastAPI 路由、会话服务包装、配额控制或任务快照拼装。

## 目录

- `main.py`: 对外稳定入口，供 backend service 导入构建器与结果提取函数。
- `src/planning_agent.py`: 主 agent 定义，负责检索要素整理、检索计划生成与确认前编排。
- `src/subagents/`: 子 agent 定义目录。
- `src/runtime.py`: 共享运行时工具，包括模型构建、middleware、结构化结果提取。
- `src/state.py`: AI 检索阶段常量与 task metadata 辅助函数。
- `src/checkpointer.py`: LangGraph checkpoint 持久化适配。

## 运行流程

AI 检索按两段执行：

1. 计划阶段
   `backend/ai_search/service.py` 调用 `build_planning_agent()`。
   主 agent 先调用 `search-elements` 子 agent 产出检索要素。
   如果信息不足，主 agent 通过 `ask_user_question` 触发追问并暂停。
   如果信息完整，主 agent 通过 `save_search_plan` 保存计划，再通过 `request_plan_confirmation` 等待用户确认。

2. 检索执行阶段
   用户确认计划后，service 进入 `_run_search_pipeline()`。
   先按 `query_batches` 调搜索客户端生成 `candidate` 文献。
   再调用 `coarse-screener` 生成 `shortlisted`。
   再调用 `close-reader` 生成 `selected` 与 `rejected`，并沉淀 `key_passages_json`。
   最后调用 `feature-comparer` 生成特征对比表，写入 `feature_table`，阶段转为 `completed`。

阶段流转如下：

`collecting_requirements`
-> `drafting_plan`
-> `awaiting_user_answer` 或 `awaiting_plan_confirmation`
-> `searching`
-> `results_ready`
-> `completed`

## Agent 定义

### 主 agent: `src/planning_agent.py`

职责：
- 编排检索要素整理、追问、计划生成与计划确认。
- 不直接执行专利检索、不做粗筛、不做精读、不做特征对比。

直接输出：
- 主 agent 自身可以输出一段 assistant chat 文本，供前端展示。
- 更重要的是它通过工具调用写入结构化产物，供 service 和 snapshot 使用。

模型、工具、skill：
- 模型：`large_model()`，即 `settings.LLM_MODEL_LARGE`，未配置时回退到 `settings.LLM_MODEL_DEFAULT`
- Deep Agents tools：
  - `update_search_elements(payload_json: str)`
  - `save_search_plan(payload_json: str)`
  - `ask_user_question(prompt: str, reason: str, expected_answer_shape: str)`
  - `request_plan_confirmation(plan_version: int, plan_summary: str, confirmation_label: str = "确认检索计划")`
- 子 agent：
  - `search-elements`
- skill：
  - 当前没有接入 Codex skill 或 MCP skill
  - 依赖的是 Deep Agents 自带 `task` 子 agent 机制，不是 `.codex/skills` 体系

结构化产物：

1. 检索要素更新 `search_elements_update`

写入到消息 `metadata`，格式为：

```json
{
  "status": "complete | needs_answer",
  "objective": "检索目标",
  "search_elements": [
    {
      "feature": "待检索技术特征",
      "keywords_zh": ["中文关键词"],
      "keywords_en": ["英文关键词"],
      "synonyms": ["同义表达"],
      "notes": "补充说明"
    }
  ],
  "missing_items": ["仍需补充的信息"],
  "clarification_summary": "当前要素摘要"
}
```

2. 追问请求 `question`

写入到消息 `metadata`，格式为：

```json
{
  "question_id": "q-123456",
  "prompt": "请补充时间范围",
  "reason": "当前检索范围过宽",
  "expected_answer_shape": "一句自然语言或短列表"
}
```

3. 检索计划 `ai_search_plan`

写入到 plan 的 `plan_json` 与 `search_elements_json`，核心格式为：

```json
{
  "plan_version": 2,
  "status": "draft | awaiting_confirmation | confirmed | superseded",
  "objective": "检索目标",
  "search_elements_snapshot": {
    "status": "complete",
    "search_elements": []
  },
  "query_batches": [
    {
      "batch_id": "b1",
      "goal": "首轮覆盖检索",
      "must_terms_zh": ["术语A"],
      "must_terms_en": ["term A"],
      "should_terms_zh": ["术语B"],
      "should_terms_en": ["term B"],
      "negative_terms": ["排除词"],
      "result_limit": 50
    }
  ],
  "selection_criteria": ["保留标准"],
  "negative_constraints": ["排除标准"],
  "execution_notes": ["执行说明"],
  "requires_confirmation": true
}
```

4. 计划确认请求 `plan_confirmation`

写入到消息 `metadata`，格式为：

```json
{
  "plan_version": 2,
  "plan_summary": "计划摘要",
  "confirmation_label": "确认检索计划"
}
```

### 子 agent: `src/subagents/search_elements.py`

职责：
- 从当前用户输入与上下文中提取检索要素。
- 判断信息是否足够生成检索计划。

模型、工具、skill：
- 模型：`large_model()`，即 `settings.LLM_MODEL_LARGE`，未配置时回退到 `settings.LLM_MODEL_DEFAULT`
- Deep Agents tools：无
- Codex skill：无

输出格式：

```json
{
  "status": "complete | needs_answer",
  "objective": "检索目标",
  "search_elements": [
    {
      "feature": "技术特征",
      "keywords_zh": ["中文关键词"],
      "keywords_en": ["英文关键词"],
      "synonyms": ["同义表达"],
      "notes": "说明"
    }
  ],
  "missing_items": ["待补充项"],
  "clarification_summary": "当前理解摘要"
}
```

当前缺口：
- 这个输出足够支撑追问和首版检索计划生成，但相比 `AI 分析` 的检索报告，检索要素维度还不够完整。
- `AI 分析` 的 `search_matrix` 还包含以下字段：
  - `element_name`
  - `element_role`
  - `block_id`
  - `effect_cluster_ids`
  - `is_hub_feature`
  - `term_frequency`
  - `priority_tier`
  - `element_type`
  - `keywords_zh`
  - `keywords_en`
  - `ipc_cpc_ref`
- 其中当前 `search-elements` 已覆盖或可映射的只有：
  - `objective`
  - `keywords_zh`
  - `keywords_en`
  - 部分自由描述 `feature/notes/synonyms`
- 相对缺失的重要信息：
  - 逻辑分块：`block_id`
  - 要素角色：`element_role`
  - 效果簇绑定：`effect_cluster_ids`
  - 要素优先级：`priority_tier`
  - 要素类型：`element_type`
  - 分类号建议：`ipc_cpc_ref`
  - 是否跨效果复用：`is_hub_feature`
  - 词频/召回倾向：`term_frequency`
- 如果要让 AI 检索输出可直接对齐 AI 分析报告，建议将 `search_elements` 单项结构补齐为：

```json
{
  "element_name": "技术特征名称",
  "element_role": "Subject | KeyFeature | Functional | Effect",
  "block_id": "A | B1..Bn | C | E",
  "effect_cluster_ids": ["E1"],
  "is_hub_feature": false,
  "term_frequency": "low | high",
  "priority_tier": "core | assist | filter",
  "element_type": "Product_Structure | Method_Process | Algorithm_Logic | Material_Composition | Parameter_Condition",
  "keywords_zh": ["中文关键词"],
  "keywords_en": ["英文关键词"],
  "ipc_cpc_ref": ["G06F 17/00"],
  "synonyms": ["同义表达"],
  "notes": "补充说明"
}
```

建议用途：
- 若短期目标只是做会话式检索，现有字段可继续使用。
- 若中期目标是和 `AI 分析` 的检索报告共用检索要素资产，建议补齐上述字段，并让 `query_batches` 显式引用这些要素。

### 子 agent: `src/subagents/coarse_screener.py`

职责：
- 只基于标题、摘要、分类号、来源批次做候选粗筛。
- 不读取全文，不决定最终特征对比表。

模型、工具、skill：
- 模型：`default_model()`，即 `settings.LLM_MODEL_DEFAULT`
- Deep Agents tools：无
- Codex skill：无

输出格式：

```json
{
  "keep": ["document_id_1", "document_id_2"],
  "discard": ["document_id_3"],
  "reasoning_summary": "粗筛原因摘要"
}
```

### 子 agent: `src/subagents/close_reader.py`

职责：
- 基于 `search_elements`、全文摘要、权利要求片段、证据段落判断 shortlist 是否纳入对比文件。

模型、工具、skill：
- 模型：`large_model()`，即 `settings.LLM_MODEL_LARGE`，未配置时回退到 `settings.LLM_MODEL_DEFAULT`
- Deep Agents tools：无
- Codex skill：无

输出格式：

```json
{
  "selected": ["document_id_1"],
  "rejected": ["document_id_2"],
  "key_passages": [
    {
      "document_id": "document_id_1",
      "passage": "命中的关键段落",
      "reason": "为何支持纳入",
      "location": "paragraph_3"
    }
  ],
  "selection_summary": "精读结论摘要"
}
```

### 子 agent: `src/subagents/feature_comparer.py`

职责：
- 基于已选文献和关键证据段落输出特征对比表。
- 不新增或删除对比文件。

模型、工具、skill：
- 模型：`large_model()`，即 `settings.LLM_MODEL_LARGE`，未配置时回退到 `settings.LLM_MODEL_DEFAULT`
- Deep Agents tools：无
- Codex skill：无

输出格式：

```json
{
  "table_rows": [
    {
      "feature": "技术特征",
      "documents": [
        {
          "document_id": "document_id_1",
          "pn": "CNxxxx",
          "match": "explicit | partial | absent",
          "evidence": "对应证据"
        }
      ],
      "summary": "该特征对比结论"
    }
  ],
  "summary_markdown": "最终对比表总结",
  "overall_findings": "整体检索发现"
}
```

## Service 侧落库产物

主 agent 和子 agent 的输出不会直接暴露为 Python 类实例，而是被 service 转换为以下持久化产物：

- `ai_search_messages`
  - `chat`
  - `question`
  - `answer`
  - `search_elements_update`
  - `plan_confirmation`
- `ai_search_plans`
  - `search_elements_json`
  - `plan_json`
- `ai_search_documents`
  - `stage`: `candidate | shortlisted | selected | rejected`
  - `key_passages_json`
  - `agent_reason`
- `ai_search_feature_tables`
  - `table_json`
  - `summary_markdown`

## 关于 `llm.py`

结论：
- `llm.py` 不应该承载 AI 检索各个 agent 的请求定义。

原因：
- `agents/common/utils/llm.py` 的职责是通用 LLM 客户端、模型选择、重试、日志与用量统计。
- AI 检索的请求结构属于具体业务协议，应与 agent 本身放在一起，便于维护 prompt、输出格式和调用方契约。
- 当前 AI 检索是基于 Deep Agents 运行，不是直接通过 `llm.py` 拼请求报文。

建议边界：
- 通用模型调用策略放在 `llm.py`。
- agent 专属的 prompt、structured output、工具输入输出定义放在 `agents/ai_search/src/` 下。
- 如果后续字段变多，可以新增 `src/schemas.py` 或按 agent 就近定义，不要塞回 `llm.py`。

补充说明：
- 当前 `agents/ai_search` 也没有直接调用 [`agents/common/utils/llm.py`](/Users/yanhao/Documents/codes/patent/agents/common/utils/llm.py)。
- 现实现使用的是 `deepagents + langchain_openai.ChatOpenAI` 组合。
- 如果未来要统一模型网关、重试和用量采集，可以考虑让 `runtime.py` 复用 `llm.py` 的策略层，但不要把 agent 请求 schema 迁过去。

## 分层约束

- `agents/ai_search` 只放 agent runtime 与编排逻辑。
- `backend/ai_search` 只放 API 模型与会话服务包装。
- 新增子 agent 时，优先在 `src/subagents/` 下单独建文件，不要继续回收到聚合导出层。
