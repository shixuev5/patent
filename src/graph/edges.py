# src/graph/edges.py

from typing import Literal
from loguru import logger

from src.graph.state import AgentState
from src.graph.consts import NODE_PLANNER, NODE_REPORTER, PHASE_DONE


def should_continue(state: AgentState) -> Literal["strategy_planner", "final_reporter"]:
    """
    [Conditional Edge] 路由判定函数

    根据 state['current_phase'] 决定流程走向：
    1. 如果是 DONE -> 进入报告生成 (NODE_REPORTER)
    2. 其他情况 -> 回到策略规划 (NODE_PLANNER) 进行下一轮迭代
    """
    current_phase = state.get("current_phase")
    iteration = state.get("iteration_count", 0)

    logger.info(f">> [Edge Decision] Phase: {current_phase}, Iteration: {iteration}")

    if current_phase == PHASE_DONE:
        return NODE_REPORTER

    return NODE_PLANNER
