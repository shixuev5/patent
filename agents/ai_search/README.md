# AI 检索 Agent

`agents/ai_search` 保存 AI 检索的 agent 核心能力与执行侧业务逻辑，不包含 FastAPI 路由、会话服务包装、SSE 格式化或数据库实现。

当前边界如下：
- `agents/ai_search`：检索要素整理、动态执行规划、query executor、筛选/精读/特征对比的 agent 与业务编排
- `backend/ai_search/service.py`：会话/phase/SSE/snapshot/数据库协调
- `backend/storage/*`：持久化实现

## 目录

- `main.py`: 对外稳定入口，供 backend service 导入 agent 构建器与结果提取函数
- `src/planning_agent.py`: 主 agent，负责检索要素整理、检索计划生成、确认前后方向性决策
- `src/execution.py`: 查询执行轮次编排，负责驱动 `query-executor` 并生成每轮摘要
- `src/execution_state.py`: 执行计划、轮次摘要、停止规则与进入筛选规则
- `src/query_constraints.py`: 查询约束、日期归一化、布尔表达式与语义文本构造
- `src/screening.py`: 候选粗筛、精读、证据构造、特征对比输入构造
- `src/tools/search_tools.py`: 智慧芽能力的窄工具封装，仅供 `query-executor` 使用
- `src/subagents/search_elements.py`: 检索要素提取子 agent
- `src/subagents/query_executor.py`: 查询执行子 agent
- `src/subagents/coarse_screener.py`: 粗筛子 agent
- `src/subagents/close_reader.py`: 精读子 agent
- `src/subagents/feature_comparer.py`: 特征对比子 agent
- `src/runtime.py`: 共享运行时工具，包括模型构建、middleware、结构化结果提取
- `src/state.py`: AI 检索阶段常量与 task metadata 辅助函数
- `src/checkpointer.py`: LangGraph checkpoint 持久化适配

## 当前运行流程

AI 检索按 4 层协作：

1. 主 agent 规划层
   `backend/ai_search/service.py` 调用 `build_planning_agent()`。
   主 agent 先调用 `search-elements` 子 agent 生成结构化检索要素。
   如果信息不足，主 agent 通过 `ask_user_question` 触发追问并暂停。
   如果信息完整，主 agent 通过 `save_search_plan` 保存带执行策略的检索计划，再通过 `request_plan_confirmation` 等待用户确认。

2. 查询执行层
   用户确认后，service 调用 `run_query_execution_rounds()`。
   `execution.py` 会根据 `plan_json` 生成本轮 `ExecutionDirective`，再调用 `query-executor` 子 agent。
   `query-executor` 只能通过 `search_tools` 访问智慧芽，并只返回 `ExecutionRoundSummary`，不会把完整候选文献列表回传给主 agent。
   每轮结果写入 `ai_search_messages(kind=execution_summary)`，供下一轮动态调整与后续审计使用。

3. 筛选层
   查询阶段沉淀出的候选文献进入 `screening.py`。
   先由 `coarse-screener` 对未粗筛的 candidate 做粗筛。
   再由 `close-reader` 对未精读的 shortlist 做精读，并沉淀 `selected/rejected`、`key_passages_json`、详情指纹等结果。

4. 对比表层
   当已有 selected 文献后，service 调用 `feature-comparer`。
   `screening.py` 负责构造特征对比输入，`feature-comparer` 输出结构化 `table_rows`、`summary_markdown` 和 `overall_findings`。

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
- 编排检索要素整理、追问、计划生成与计划确认
- 在确认后定义动态执行方向、lane 优先级与停止规则
- 不直接调用智慧芽检索工具
- 不读取候选专利全文或完整候选列表

模型、工具、子 agent：
- 模型：`large_model()`
- Deep Agents tools：
  - `update_search_elements(payload_json: str)`
  - `save_search_plan(payload_json: str)`
  - `ask_user_question(prompt: str, reason: str, expected_answer_shape: str)`
  - `request_plan_confirmation(plan_version: int, plan_summary: str, confirmation_label: str = "确认检索计划")`
- 子 agent：
  - `search-elements`
  - `query-executor`

输出重点：
- `search_elements_update`
- `ai_search_plan`
- `plan_confirmation`

### 子 agent: `src/subagents/search_elements.py`

职责：
- 从用户输入与上下文中提取检索要素
- 判断是否需要追问

输出字段：
- `status`
- `objective`
- `applicants`
- `filing_date`
- `priority_date`
- `search_elements`
- `missing_items`
- `clarification_summary`

### 子 agent: `src/subagents/query_executor.py`

职责：
- 根据主 agent 提供的 `ExecutionDirective` 动态执行本轮查询
- 在批次内做微调，例如 lane 顺序调整、提前停止、局部扩缩
- 只返回摘要，不返回完整候选专利列表

约束：
- 只能通过 `search_tools` 调智慧芽能力
- 不能修改检索目标、核心 search elements、plan_version
- 不能把候选文献明细带回主 agent 上下文

输出格式：

```json
{
  "round_id": "round-1",
  "lane_results": [
    {
      "lane_type": "semantic",
      "batch_id": "b1",
      "executed_tool": "search_semantic",
      "new_unique_candidates": 6,
      "deduped_hits": 2,
      "candidate_pool_size": 8,
      "stop_signal": "",
      "reasoning": "语义召回已覆盖首轮候选。"
    }
  ],
  "new_unique_candidates": 6,
  "deduped_hits": 2,
  "candidate_pool_size": 8,
  "needs_replan": true,
  "recommended_adjustments": ["下一轮可补 boolean 收窄"],
  "stop_signal": ""
}
```

### 子 agent: `src/subagents/coarse_screener.py`

职责：
- 仅基于标题、摘要、分类号、来源批次做候选粗筛
- 只处理 `coarse_status = pending` 的 candidate

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
- 基于 `search_elements`、详情摘要、权利要求片段、证据段落判断 shortlist 是否纳入对比文件
- 只处理 `close_read_status = pending` 的 shortlist

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
- 基于 selected 文献与关键证据段落输出特征对比表
- 不新增或删除对比文件

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

## 查询工具

`src/tools/search_tools.py` 把智慧芽能力包装成窄工具，仅暴露给 `query-executor`：

- `search_trace(plan_version, batch_id, seed_pn, limit, cutoff_date, applicant_terms)`
- `search_semantic(plan_version, batch_id, query_text, limit, cutoff_date, applicant_terms)`
- `search_boolean(plan_version, batch_id, query_text, limit)`
- `count_boolean(query_text)`
- `fetch_patent_details(pn)`
- `prepare_lane_queries(plan_version, batch_payload_json, search_elements_json, lane_type)`

设计要求：
- tool 输入输出固定 JSON 结构
- tool 内部调用智慧芽 client
- tool 会直接协调候选文献池的落库更新
- 主 agent 不直接持有这些 tool

## 计划结构

`save_search_plan` 保存的 `plan_json` 现在是“可迭代执行计划”，除了旧版字段，还包含以下执行字段：

- `execution_policy`
- `lanes`
- `round_stop_rules`
- `screening_entry_rules`
- `replan_rules`

核心形状示例：

```json
{
  "plan_version": 2,
  "status": "draft",
  "objective": "检索目标",
  "search_elements_snapshot": {
    "status": "complete",
    "objective": "检索目标"
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
  "execution_policy": {
    "dynamic_replanning": true,
    "planner_visibility": "summary_only",
    "max_rounds": 3
  },
  "lanes": [
    {
      "lane_type": "semantic",
      "goal": "语义召回",
      "priority": 20,
      "enabled_when": "always",
      "batch_specs": [
        {
          "batch_id": "b1",
          "goal": "首轮覆盖检索"
        }
      ]
    },
    {
      "lane_type": "boolean",
      "goal": "布尔补召回",
      "priority": 30,
      "enabled_when": "always",
      "batch_specs": [
        {
          "batch_id": "b1",
          "goal": "首轮覆盖检索"
        }
      ]
    }
  ],
  "round_stop_rules": [
    {
      "type": "no_new_candidates_round_limit",
      "limit": 1
    }
  ],
  "screening_entry_rules": [
    {
      "type": "candidate_pool_size",
      "min_count": 8
    }
  ],
  "replan_rules": [
    {
      "type": "summary_after_each_round"
    }
  ]
}
```

说明：
- 主 agent 决定 lane 方向、优先级和规则
- `query-executor` 负责在这些约束下做一轮动态执行
- service 和 execution 层只读取这些结构化字段，不再依赖硬编码的固定查询顺序

## 持久化产物

service 和 storage 会把 agent 结果落到以下产物中：

### `ai_search_messages`

主要 `kind`：
- `chat`
- `question`
- `answer`
- `search_elements_update`
- `plan_confirmation`
- `execution_summary`

其中 `execution_summary.metadata` 对应每轮 `ExecutionRoundSummary`。

### `ai_search_plans`

保存：
- `search_elements_json`
- `plan_json`
- `status`
- `confirmed_at`
- `superseded_at`

### `ai_search_documents`

除了原有字段，现在还包含筛选去重相关字段：
- `source_batches_json`
- `source_lanes_json`
- `stage`
- `coarse_status`
- `coarse_reason`
- `coarse_screened_at`
- `close_read_status`
- `close_read_reason`
- `close_read_at`
- `detail_fingerprint`

处理约束：
- 同一 `task_id + plan_version + pn` 只保留一条文献记录
- 不同 lane/batch 命中同一 PN 时，只更新来源，不重复粗筛
- 已精读文献不会重复精读

### `ai_search_feature_tables`

保存：
- `table_json`
- `summary_markdown`
- `status`

## 当前边界注意事项

- 数据库读写仍在 backend/service + storage 层，不下沉到 agent 层实现
- `agents/ai_search` 内部模块可以协调业务流程，但不负责 FastAPI、SSE、owner 校验和 session 管理
- `service.py` 现在应被视为编排外壳，而不是查询策略实现文件
