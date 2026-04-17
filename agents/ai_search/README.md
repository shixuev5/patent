# AI Search Agent

`agents/ai_search` 现在采用单主 agent + specialist 子 agent 的结构，并按“角色职责”组织目录。

## Directory Shape

核心源码位于 [src](/Users/yanhao/Documents/codes/patent/agents/ai_search/src)：

- [agents.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/agents.py)
  稳定导出层，供 [agents.ai_search.main](/Users/yanhao/Documents/codes/patent/agents/ai_search/main.py) 和 [agents.ai_search](/Users/yanhao/Documents/codes/patent/agents/ai_search/__init__.py) 使用。
- [context.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/context.py)
  共享任务上下文、todo 读写、计划/消息读取、gap 评估辅助。
- [runtime.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/runtime.py)
  模型构建、权限 guard、中间件和响应抽取。
- [stage_limits.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/stage_limits.py)
  各阶段共享的 shortlist / selected / passage 数量上限。
- [main_agent](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/main_agent)
  主 agent 目录。
- [subagents](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents)
  各 specialist 目录。

当前目录原则是：

- 主 agent 有自己的包目录，不再把 prompt 和 tools 混在单文件里。
- 每个 specialist 各自拥有 `agent.py`、`prompt.py`、`tools.py`，需要结构化输出时再加 `schemas.py`。
- 接近某个 specialist 的 helper 直接放进该 specialist 目录，不再保留独立的 `toolkits`、`tools`、`evidence` 顶层目录。

## Main Agent

主 agent 定义在 [main_agent/agent.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/main_agent/agent.py)，prompt 在 [main_agent/prompt.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/main_agent/prompt.py)，orchestration tools 在 [main_agent/tools.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/main_agent/tools.py)。

它负责：

- 读取会话 / 规划 / 执行三个聚合上下文 read model
- 调 `search-elements` 整理检索要素
- 必要时调 `plan-prober` 做轻量预检
- 生成并确认检索计划
- 决定何时调 `query-executor`、`coarse-screener`、`close-reader`、`feature-comparer`
- 汇总 specialist 结果并决定继续、重规划、追问或结束
- 在 `close-reader` / `feature-comparer` 之后读取 gap 上下文，判断是否继续补检索直到足以支撑创造性评价

主 agent 遵循固定阶段协议：

- `collect_requirements`
- `draft_plan`
- `await_plan_confirmation`
- `execute_search`
- `coarse_screen`
- `close_read`
- `feature_comparison`
- `completed`

主 agent 工具现在分成两类：

- 读模型工具：
  - `get_session_context`
  - `get_planning_context`
  - `get_execution_context`
- 高层命令工具：
  - `start_plan_drafting`
  - `publish_planner_draft`
  - `request_user_question`
  - `request_plan_confirmation`
  - `advance_workflow`
  - `complete_session`

执行阶段切换不再依赖多个细粒度写工具，而是统一由 `advance_workflow` 承接：

- `begin_execution`
- `step_completed`
- `request_replan`
- `enter_coarse_screen`
- `enter_close_read`
- `enter_feature_comparison`
- `enter_drafting_plan`

确定性编排逻辑下沉到 [orchestration](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/orchestration)：

- `phase_machine`
- `planning_runtime`
- `execution_runtime`
- `session_views`

这些模块负责 phase 合法迁移、planner draft 发布、todo 物化与条件激活、round / exhaustion 判定，以及 session / planning / execution read model 聚合。

用户态执行进度使用业务自定义的 `executionTodos`：

- 数据保存在 task metadata 的 `ai_search.todos`
- 每条 todo 直接对应 `executionSpec.sub_plans[*].retrieval_steps[*]`
- 前端 [search.vue](/Users/yanhao/Documents/codes/patent/frontend/pages/search.vue) 渲染的是这份业务数据，而不是 deepagents 内部 todo 视图
- todo 采用步骤级字段：`todo_id`、`sub_plan_id`、`step_id`、`phase_key`、`title`、`description`、`status`

## Specialist Agents

specialist 全部位于 [subagents](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents)：

- [search_elements](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/search_elements)
  从用户输入和上下文抽取结构化检索要素；`normalize.py` 负责 payload 归一化。
- [plan_prober](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/plan_prober)
  在 `draft_plan` 阶段做低成本、非持久化 probe，只返回规划信号。
- [query_executor](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/query_executor)
  执行当前 retrieval step，并输出 `execution_step_summary`。
- [coarse_screener](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/coarse_screener)
  根据标题、摘要、分类号和来源批次做轻量粗筛。
- [close_reader](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/close_reader)
  结合全文证据、claim limitation、权利要求和说明书做精读。
- [feature_comparer](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/feature_comparer)
  基于已选对比文件和关键证据段落生成特征对比分析结果。

边界原则：

- specialist 只做自己领域内的单一任务
- specialist 自己持有对应领域工具和结果持久化工具
- 主 agent 只负责调度和阶段推进

## Evidence Flow

证据流现在围绕 [close_reader](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/close_reader) 和 [feature_comparer](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/feature_comparer) 收口：

- 精读前会拉取专利详情并将全文落到受控 workspace
- `close-reader` 通过全文文件做关键词定位、claim limitation 对齐和证据复核
- `close-reader` 的全文与 workspace helper 位于 [workspace.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/close_reader/workspace.py)
- `close-reader` 的关键词抽取与 fallback passages 位于 [passages.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/close_reader/passages.py)
- `close-reader` 会输出 `limitation_coverage`、`limitation_gaps` 和 `follow_up_hints`
- 若模型没有给出足够证据，系统会用关键词命中生成 fallback passages
- `feature-comparer` 统一消费 `key_passages_json`，并输出 `difference_highlights`、`coverage_gaps`、`follow_up_search_hints` 和 `creativity_readiness`
- 主 agent 通过 `get_planning_context` / `get_execution_context` 中的 `gap_context` 和 `gap_progress` 驱动下一轮检索修正
- `build_gap_strategy_seed` 会把最新 limitation / coverage gaps 转成下一轮可直接消费的 `targeted_gaps` 和 `seed_batch_specs`
- `query-executor` 会在当前 step directive 中拿到最新 gap 上下文，用于优先围绕 targeted gaps 调整当前步骤
- `prepare_lane_queries` 会把 `seed_terms`、`pivot_terms`、`gap_type`、`claim_id`、`limitation_id` 编进当前执行查询
- 正式检索命中的文献会写入 `ai_search_documents`，并记录 `source_sub_plans_json` 与 `source_steps_json`

## Runtime

共享运行时定义在 [runtime.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/runtime.py)。

- 权限控制按 agent 角色区分
- `main-agent` 可调度 specialist，但不能直接使用文件系统工具
- `close-reader` 允许只读文件系统工具：`ls`、`read_file`、`glob`、`grep`
- `close-reader` 禁止：`write_file`、`edit_file`、`execute`
- `plan-prober` 只允许 `probe_count_boolean`、`probe_search_boolean`、`probe_search_semantic`

当前流式约定也在这套运行时里配合后端消费层落地：

- deepagents / langgraph 统一使用 `astream(..., stream_mode=["updates", "messages", "custom"], version="v2", subgraphs=True)`
- `updates` 是唯一的 process lifecycle 来源：主 agent 的 `task` tool call 映射为 specialist 的 `process.started` / `process.completed` / `process.failed`，普通工具调用也从 `updates` 推导
- `messages` 只负责主 agent / specialist 的 Markdown 增量输出
- `custom` 只保留业务事件，例如 `snapshot.changed`，不再承载 subagent / tool lifecycle
- specialist 侧的 streaming middleware 现在只保留扩展点，不再自己写 `subagent.*` / `tool.*` 事件

## Stable Exports

对包外保持稳定的入口仍然是：

- [agents.ai_search](/Users/yanhao/Documents/codes/patent/agents/ai_search/__init__.py)
- [agents.ai_search.main](/Users/yanhao/Documents/codes/patent/agents/ai_search/main.py)

它们继续导出：

- `build_main_agent`
- `build_plan_prober_agent`
- `build_query_executor_agent`
- `build_coarse_screener_agent`
- `build_close_reader_agent`
- `build_feature_comparer_agent`
- `extract_latest_ai_message`
- `extract_structured_response`

内部目录可以继续演进，但这些顶层接口不应轻易改动。

## Service Boundary

[backend/ai_search/service.py](/Users/yanhao/Documents/codes/patent/backend/ai_search/service.py) 负责：

- 会话与权限
- 调用主 agent
- 处理中断/恢复
- 输出 SSE 事件
- 读取 snapshot

它不承担策略编排职责。

当前 backend 协作者拆分为：

- [session_service.py](/Users/yanhao/Documents/codes/patent/backend/ai_search/session_service.py)
  处理 create/list/update/delete 与 ownership 校验。
- [analysis_seed_service.py](/Users/yanhao/Documents/codes/patent/backend/ai_search/analysis_seed_service.py)
  处理 analysis artifact 加载、seeded prompt 构建、seeded session 初始化与补全。
- [agent_run_service.py](/Users/yanhao/Documents/codes/patent/backend/ai_search/agent_run_service.py)
  处理 main-agent / feature-comparer 调用、checkpoint 恢复、SSE 流事件。
- [snapshot_service.py](/Users/yanhao/Documents/codes/patent/backend/ai_search/snapshot_service.py)
  聚合 snapshot、当前 plan/run/messages/documents 的 read model。
- [artifacts_service.py](/Users/yanhao/Documents/codes/patent/backend/ai_search/artifacts_service.py)
  处理 terminal report / bundle / download url。

其中 SSE 流事件的职责边界是：

- `agent_run_service` 负责把 deepagents 原生 `updates/messages/custom` 归一成前端消费的 `process.*`、`message.segment.*`、`run.updated` 等业务事件
- `snapshot_service` 不解析 deepagents 原生流，只消费已持久化的业务流事件和当前存储状态
