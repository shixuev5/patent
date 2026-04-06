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

- 维护 todo 和当前任务
- 调 `search-elements` 整理检索要素
- 调 `claim-decomposer` / `claim-search-strategist` 走 claim-aware 路径
- 生成并确认检索计划
- 决定何时调 `query-executor`、`coarse-screener`、`close-reader`、`feature-comparer`
- 汇总 specialist 结果并决定继续、重规划、追问或结束
- 在 `close-reader` / `feature-comparer` 之后读取 gap 上下文，判断是否继续补检索直到足以支撑创造性评价

主 agent 遵循固定阶段协议：

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

主 agent 还有两个确定性决策工具：

- `evaluate_gap_progress`
  读取最新 `close_read_result` / `feature_compare_result`，输出 `should_continue_search` 和 `recommended_action`
- `decide_search_transition`
  读取当前搜索轮次摘要和执行规则，输出 `continue_search` / `enter_coarse_screen` / `replan_search`

这里不再存在独立 Python orchestrator。service 不再硬编码“先检索再筛选再精读”的流程。

## Specialist Agents

specialist 全部位于 [subagents](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents)：

- [search_elements](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/search_elements)
  从用户输入和上下文抽取结构化检索要素；`normalize.py` 负责 payload 归一化。
- [claim_decomposer](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/claim_decomposer)
  读取权利要求并拆成 limitation groups。
- [claim_search_strategist](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/claim_search_strategist)
  根据 claim limitation 和 gap 信息规划 claim-aware 检索策略。
- [query_executor](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/query_executor)
  执行当前检索轮次，并输出 round summary。
- [coarse_screener](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/coarse_screener)
  根据标题、摘要、分类号和来源批次做轻量粗筛。
- [close_reader](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/close_reader)
  结合全文证据、claim limitation、权利要求和说明书做精读。
- [feature_comparer](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/feature_comparer)
  基于已选对比文件和关键证据段落生成特征对比表。

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
- 主 agent 与 `claim-search-strategist` 都可以通过 `get_gap_context` 读取这些结果，驱动下一轮检索修正
- `claim-search-strategist` 的 `build_gap_strategy_seed` 会把最新 limitation / coverage gaps 转成下一轮可直接消费的 `targeted_gaps` 和 `seed_batch_specs`
- `query-executor` 也会在执行 directive 中拿到最新 gap 上下文，用于优先围绕 targeted gaps 调整下一轮检索顺序
- `prepare_lane_queries` 会把 `seed_terms`、`pivot_terms`、`gap_type`、`claim_id`、`limitation_id` 编进下一轮 `query_text` / `semantic_text`

## Runtime

共享运行时定义在 [runtime.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/runtime.py)。

- 权限控制按 agent 角色区分
- `main-agent` 可调度 specialist，但不能直接使用文件系统工具
- `close-reader` 允许只读文件系统工具：`ls`、`read_file`、`glob`、`grep`
- `close-reader` 禁止：`write_file`、`edit_file`、`execute`

## Stable Exports

对包外保持稳定的入口仍然是：

- [agents.ai_search](/Users/yanhao/Documents/codes/patent/agents/ai_search/__init__.py)
- [agents.ai_search.main](/Users/yanhao/Documents/codes/patent/agents/ai_search/main.py)

它们继续导出：

- `build_main_agent`
- `build_claim_decomposer_agent`
- `build_claim_search_strategist_agent`
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
