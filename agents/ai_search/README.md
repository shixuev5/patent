# AI Search Agent

`agents/ai_search` 现在采用单主 agent 架构。

## Core Shape

主 agent 定义在 [main_agent.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/main_agent.py)。

它同时负责：
- 维护 todo 和当前任务
- 调 `search-elements` 整理检索要素
- 生成并确认检索计划
- 确认后自行决定何时检索、粗筛、精读、生成对比表
- 通过工具持久化候选文献、精读结果和 feature table

这里不再存在独立的 Python orchestrator。service 也不再硬编码“先检索再筛选再精读”的流程。

## Specialist Agents

主 agent 可以按需调用以下 specialist：

- [search_elements.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/search_elements.py)
- [query_executor.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/query_executor.py)
- [coarse_screener.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/coarse_screener.py)
- [close_reader.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/close_reader.py)
- [feature_comparer.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/subagents/feature_comparer.py)

边界是：
- specialist 只做领域内单一任务
- 主 agent 决定何时调用、调用多少次、何时结束

## Runtime

共享运行时定义在 [runtime.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/runtime.py)。

- 权限控制按 agent 角色区分
- `main-agent` 可调度 specialist，但不能直接用文件系统工具
- `close-reader` 允许只读文件系统工具：`ls`、`read_file`、`glob`、`grep`
- `close-reader` 禁止：`write_file`、`edit_file`、`execute`

## Evidence Flow

证据 helper 定义在 [screening.py](/Users/yanhao/Documents/codes/patent/agents/ai_search/src/screening.py)。

- 精读前会拉取专利详情并将全文落到受控 workspace
- `close-reader` 通过全文文件做关键词定位和证据复核
- 若模型没有给出足够证据，系统会用关键词命中生成 fallback passages
- `feature-comparer` 统一消费 `key_passages_json`

## Service Boundary

[backend/ai_search/service.py](/Users/yanhao/Documents/codes/patent/backend/ai_search/service.py) 现在只负责：

- 会话与权限
- 调用主 agent
- 处理中断/恢复
- 输出 SSE 事件
- 读取 snapshot

它不再承担策略编排职责。
