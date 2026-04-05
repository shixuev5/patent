"""
LangGraph边条件判断
定义工作流中节点间的跳转逻辑和条件判断
"""

from loguru import logger
from agents.ai_reply.src.state import WorkflowState

def handle_error(state: WorkflowState) -> WorkflowState:
    """
    错误处理跳转条件

    Args:
        state: 当前工作流状态

    Returns:
        更新后的状态
    """
    if str(state.status or "").strip().lower() == "cancelled":
        logger.warning("工作流已取消")
        return state

    logger.error(f"工作流执行过程中出现错误，共 {len(state.errors)} 个错误")
    for error in state.errors:
        logger.error(f"节点 {error.node_name}: {error.error_message}")

    state.status = "failed"
    return state
