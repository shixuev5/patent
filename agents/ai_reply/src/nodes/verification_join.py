"""
验证汇合节点
用于汇总并行验证分支结果并统一设置流程状态
"""

from typing import Any
from loguru import logger


class VerificationJoinNode:
    """验证汇合节点"""

    def __init__(self, config=None):
        self.config = config

    def __call__(self, state):
        logger.info("进入验证汇合节点")

        updates = {
            "current_node": "verification_join"
        }

        if self._state_get(state, "status") == "failed":
            updates["status"] = "failed"
            return updates

        verification_error_nodes = {
            "evidence_verification",
            "common_knowledge_verification",
            "topup_search_verification",
        }
        errors = self._state_get(state, "errors", []) or []

        has_verification_error = any(
            self._item_get(error, "node_name", "") in verification_error_nodes
            for error in errors
        )

        if has_verification_error:
            logger.error("验证分支存在错误，流程标记为失败")
            updates["status"] = "failed"
            return updates

        updates["status"] = "completed"
        updates["progress"] = 90.0
        logger.info("验证分支汇总完成")
        return updates

    def _state_get(self, state: Any, key: str, default=None):
        if isinstance(state, dict):
            return state.get(key, default)
        return getattr(state, key, default)

    def _item_get(self, item: Any, key: str, default=None):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
