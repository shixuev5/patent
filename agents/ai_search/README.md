# AI Search Agent

`agents/ai_search` 现在采用单主 agent 架构。

## Core Shape

主 agent 定义在 [main_agent.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/main_agent.py)。

它同时负责：
- 维护 todo 和当前任务
- 调 `search-elements` 整理检索要素
- 调 `claim-decomposer` / `claim-search-strategist` 走 claim-aware 路径
- 生成并确认检索计划
- 确认后决定何时调 `query-executor`、`coarse-screener`、`close-reader`、`feature-comparer`
- 汇总 specialist 结果并决定继续、重规划、追问或结束
- 在 `close-reader` / `feature-comparer` 之后读取 gap 上下文，判断是否继续补检索直到足以支撑创造性评价

主 agent 现在遵循固定阶段协议：
- `collect_requirements`
- `claim_decomposition`
- `search_strategy`
- `draft_plan`
- `await_plan_confirmation`
- `execute_search`
- `coarse_screen`
- `close_read`
- `generate_feature_table`
- `completed`

执行阶段切换使用显式入口工具：
- `start_claim_decomposition`
- `start_search_strategy`
- `start_plan_drafting`
- `begin_execution`
- `start_coarse_screen`
- `start_close_read`
- `start_feature_table_generation`
- `complete_execution`

主 agent 还有一个确定性决策工具：
- `evaluate_gap_progress`
  读取最新 `close_read_result` / `feature_compare_result`，输出 `should_continue_search` 和 `recommended_action`
  用于判断是继续补检索、回到 strategist 重规划，还是可以结束
  当 `recommended_action == replan_search_strategy` 时，`generate_feature_table` 阶段不能直接 `complete_execution`
- `decide_search_transition`
  读取当前搜索轮次摘要和执行规则，输出 `continue_search` / `enter_coarse_screen` / `replan_search`
  用于把 `execute_search` 阶段的转移从 prompt 判断收口成确定性工具判断

这里不再存在独立的 Python orchestrator。service 也不再硬编码“先检索再筛选再精读”的流程。

## Specialist Agents

主 agent 可以按需调用以下 specialist：

- [search_elements.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/search_elements.py)
- [claim_decomposer.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/claim_decomposer.py)
- [claim_search_strategist.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/claim_search_strategist.py)
- [query_executor.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/query_executor.py)
- [coarse_screener.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/coarse_screener.py)
- [close_reader.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/close_reader.py)
- [feature_comparer.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/feature_comparer.py)

边界是：
- specialist 只做领域内单一任务
- specialist 自己持有对应领域工具和结果持久化工具
- 主 agent 只决定何时调用、调用多少次、何时结束

## Runtime

共享运行时定义在 [runtime.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/runtime.py)。

- 权限控制按 agent 角色区分
- `main-agent` 可调度 specialist，但不能直接用文件系统工具
- `close-reader` 允许只读文件系统工具：`ls`、`read_file`、`glob`、`grep`
- `close-reader` 禁止：`write_file`、`edit_file`、`execute`

## Evidence Flow

证据 helper 现在按 specialist 收口：

- [close_reader](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/close_reader)
- [feature_comparer](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/feature_comparer)

- 精读前会拉取专利详情并将全文落到受控 workspace
- `close-reader` 通过全文文件做关键词定位、claim limitation 对齐和证据复核
- `close-reader` 会输出 limitation coverage、limitation gaps 和 follow-up hints
- 若模型没有给出足够证据，系统会用关键词命中生成 fallback passages
- `feature-comparer` 统一消费 `key_passages_json`，并输出 difference highlights、coverage gaps、follow-up search hints 和 creativity readiness
- 主 agent 与 `claim-search-strategist` 都可以通过 `get_gap_context` 读取这些结果，驱动下一轮检索修正
- `claim-search-strategist` 还有 `build_gap_strategy_seed`，把最新 limitation/coverage gaps 转成下一轮可直接消费的 `targeted_gaps` 和 `seed_batch_specs`
- `query-executor` 也会在执行 directive 中拿到最新 gap 上下文，用于优先围绕 targeted gaps 调整下一轮检索顺序
- `prepare_lane_queries` 会把 `seed_terms`、`pivot_terms`、`gap_type`、`claim_id`、`limitation_id` 直接编进下一轮 `query_text` / `semantic_text`

## Service Boundary

[backend/ai_search/service.py](/Users/yanhao/Documents/codes/patent/backend/ai_search/service.py) 现在只负责：

- 会话与权限
- 调用主 agent
- 处理中断/恢复
- 输出 SSE 事件
- 读取 snapshot

它不再承担策略编排职责。
