"""
LangGraph边条件判断
定义工作流中节点间的跳转逻辑和条件判断
"""

from loguru import logger
from agents.common.retrieval import drop_retrieval_session
from agents.office_action_reply.src.state import WorkflowState

def handle_error(state: WorkflowState) -> WorkflowState:
    """
    错误处理跳转条件

    Args:
        state: 当前工作流状态

    Returns:
        更新后的状态
    """
    logger.error(f"工作流执行过程中出现错误，共 {len(state.errors)} 个错误")
    for error in state.errors:
        logger.error(f"节点 {error.node_name}: {error.error_message}")

    session_id = str(getattr(state, "retrieval_session_id", "") or "").strip()
    if session_id:
        try:
            drop_retrieval_session(session_id=session_id)
            logger.info(f"已清理检索会话: {session_id}")
        except Exception as ex:
            logger.warning(f"清理检索会话失败 session_id={session_id}: {ex}")

    state.status = "failed"
    return state
