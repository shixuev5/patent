"""
并行分析节点
在单个工作流节点内并行执行“修改分析链”和“争点抽取链”，避免图级并行写状态冲突。
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

from loguru import logger

from agents.ai_reply.src.nodes.amendment_strategy import AmendmentStrategyNode
from agents.ai_reply.src.nodes.amendment_tracking import AmendmentTrackingNode
from agents.ai_reply.src.nodes.dispute_extraction import DisputeExtractionNode
from agents.ai_reply.src.nodes.support_basis_check import SupportBasisCheckNode
from agents.common.utils.concurrency import submit_with_current_context


class AnalysisParallelNode:
    """并行分析节点"""

    _SKIP_KEYS = {"status", "progress", "current_node"}

    def __init__(self, config=None):
        self.config = config

    def __call__(self, state):
        logger.info("开始并行执行修改分析链与争点抽取链")
        updates = {
            "current_node": "analysis_parallel",
            "status": "running",
            "progress": 50.0,
        }

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                amendment_future = submit_with_current_context(
                    executor,
                    self._run_amendment_branch,
                    state,
                )
                dispute_future = submit_with_current_context(
                    executor,
                    self._run_dispute_branch,
                    state,
                )
                amendment_updates = amendment_future.result()
                dispute_updates = dispute_future.result()

            merged = self._merge_branch_updates(amendment_updates, dispute_updates)
            updates.update(merged)
            if merged.get("errors"):
                updates["status"] = "failed"
            else:
                updates["status"] = "completed"
                updates["progress"] = 68.0
            logger.info("并行分析节点执行完成")
        except Exception as exc:
            logger.error(f"并行分析节点执行失败: {exc}")
            updates["errors"] = [{
                "node_name": "analysis_parallel",
                "error_message": str(exc),
                "error_type": "analysis_parallel_error",
            }]
            updates["status"] = "failed"

        return updates

    def _run_amendment_branch(self, state: Any) -> Dict[str, Any]:
        local_state = self._to_state_dict(state)
        branch_updates: Dict[str, Any] = {}
        for node_cls in (AmendmentTrackingNode, SupportBasisCheckNode, AmendmentStrategyNode):
            node = node_cls(self.config)
            updates = node(local_state)
            branch_updates.update(updates)
            local_state.update(updates)
            if str(updates.get("status", "")).strip() == "failed":
                break
        return branch_updates

    def _run_dispute_branch(self, state: Any) -> Dict[str, Any]:
        node = DisputeExtractionNode(self.config)
        return node(self._to_state_dict(state))

    def _merge_branch_updates(
        self,
        amendment_updates: Dict[str, Any],
        dispute_updates: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        errors = []
        for branch_updates in (amendment_updates, dispute_updates):
            for key, value in branch_updates.items():
                if key in self._SKIP_KEYS:
                    continue
                if key == "errors":
                    errors.extend(value or [])
                    continue
                merged[key] = value
        if errors:
            merged["errors"] = errors
        return merged

    def _to_state_dict(self, state: Any) -> Dict[str, Any]:
        if isinstance(state, dict):
            return dict(state)
        if hasattr(state, "model_dump"):
            return state.model_dump()
        if hasattr(state, "dict"):
            return state.dict()
        return {}
