"""
查询执行子 agent 定义。
"""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend

from agents.ai_search.src.execution_state import ExecutionRoundSummary
from agents.ai_search.src.runtime import build_guard_middleware, default_model
from agents.ai_search.src.tools.search_tools import build_search_tools


QUERY_EXECUTOR_SYSTEM_PROMPT = """
你是 `query-executor` 子 agent。

唯一职责：根据主 agent 提供的 ExecutionDirective 执行本轮查询，并输出结构化摘要。

必须遵守：
1. 只允许通过当前提供的 search tools 访问检索系统。
2. 不要把具体候选专利明细写进最终输出，最终输出只能是本轮摘要。
3. 你可以根据 directive 和上轮摘要做批次内微调，包括：
   - 调整 lane 执行顺序
   - 提前停止某个 lane
   - 在同一 batch 内优先 semantic 或 trace，再决定是否补 boolean
4. 你不能修改检索目标、核心 search elements、plan_version。
5. 执行时优先读取 `prepare_lane_queries` 的结果，再选择对应 search tool。
6. 若某个 lane 没有可执行输入（例如 trace 缺少 seed_pn），应跳过并在 lane_results 中注明。
7. 最终输出必须为结构化对象：
   - round_id
   - lane_results
   - new_unique_candidates
   - deduped_hits
   - candidate_pool_size
   - needs_replan
   - recommended_adjustments
   - stop_signal
""".strip()


def build_query_executor_agent(storage: object, task_id: str):
    return create_deep_agent(
        model=default_model(),
        tools=build_search_tools(storage, task_id),
        system_prompt=QUERY_EXECUTOR_SYSTEM_PROMPT,
        middleware=[build_guard_middleware("query-executor")],
        response_format=ExecutionRoundSummary,
        backend=StateBackend,
        name=f"ai-search-query-executor-{task_id}",
    )


def build_query_executor_subagent(storage: object, task_id: str) -> dict:
    return {
        "name": "query-executor",
        "description": "根据执行指令动态执行 trace/semantic/boolean 检索，并只返回摘要状态。",
        "system_prompt": QUERY_EXECUTOR_SYSTEM_PROMPT,
        "model": default_model(),
        "tools": build_search_tools(storage, task_id),
        "middleware": [build_guard_middleware("query-executor")],
    }
