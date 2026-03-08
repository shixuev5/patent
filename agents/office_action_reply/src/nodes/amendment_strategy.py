"""
修改审查策略路由节点
根据新增特征来源决定复用历史评述或进入补充检索任务
"""

from typing import Any, Dict, List, Set

from loguru import logger

from agents.office_action_reply.src.utils import get_node_cache


class AmendmentStrategyNode:
    """修改审查策略路由节点"""

    def __init__(self, config=None):
        self.config = config

    def __call__(self, state):
        logger.info("开始修改审查策略路由")
        updates = {
            "current_node": "amendment_strategy",
            "status": "running",
            "progress": 65.0,
        }

        try:
            cache = get_node_cache(self.config, "amendment_strategy")
            result = cache.run_step(
                "build_amendment_strategy_v2",
                self._build_strategy,
                self._state_get(state, "has_claim_amendment", False),
                self._state_get(state, "added_features", []),
                self._state_get(state, "prepared_materials", {}),
            )

            updates["reuse_oa_tasks"] = result.get("reuse_oa_tasks", [])
            updates["topup_tasks"] = result.get("topup_tasks", [])
            updates["status"] = "completed"
            updates["progress"] = 68.0
            logger.info(
                f"策略路由完成，复用任务: {len(updates['reuse_oa_tasks'])}，补充检索任务: {len(updates['topup_tasks'])}"
            )
        except Exception as e:
            logger.error(f"修改审查策略路由失败: {e}")
            updates["errors"] = [{
                "node_name": "amendment_strategy",
                "error_message": str(e),
                "error_type": "amendment_strategy_error",
            }]
            updates["status"] = "failed"

        return updates

    def _build_strategy(self, has_claim_amendment: bool, added_features, prepared_materials):
        if not has_claim_amendment:
            return {"reuse_oa_tasks": [], "topup_tasks": []}

        features = [self._to_dict(item) for item in (added_features or [])]
        prepared = self._to_dict(prepared_materials)
        office_action = self._to_dict(prepared.get("office_action", {}))
        paragraphs = office_action.get("paragraphs", []) or []

        oa_claim_ids_covered: Set[str] = set()
        for paragraph in paragraphs:
            paragraph_dict = self._to_dict(paragraph)
            for claim_id in paragraph_dict.get("claim_ids", []) or []:
                claim_value = str(claim_id).strip()
                if claim_value:
                    oa_claim_ids_covered.add(claim_value)

        reuse_oa_tasks: List[Dict[str, Any]] = []
        topup_tasks: List[Dict[str, Any]] = []

        for feature in features:
            feature_id = str(feature.get("feature_id", "")).strip()
            feature_text = str(feature.get("feature_text", "")).strip()
            source_type = str(feature.get("source_type", "spec")).strip()
            target_claim_ids = [str(item).strip() for item in feature.get("target_claim_ids", []) if str(item).strip()]
            source_claim_ids = [str(item).strip() for item in feature.get("source_claim_ids", []) if str(item).strip()]

            claim_candidates = []
            for item in (target_claim_ids or source_claim_ids or ["1"]):
                if item and item not in claim_candidates:
                    claim_candidates.append(item)

            task = {
                "task_id": feature_id,
                "claim_ids": claim_candidates,
                "feature_text": feature_text,
                "source_type": source_type,
                "source_claim_ids": source_claim_ids,
                "target_claim_ids": target_claim_ids,
            }

            if source_type == "claim" and any(claim in oa_claim_ids_covered for claim in (source_claim_ids or claim_candidates)):
                reuse_oa_tasks.append(task)
            else:
                topup_tasks.append(task)

        return {
            "reuse_oa_tasks": reuse_oa_tasks,
            "topup_tasks": topup_tasks,
        }

    def _state_get(self, state: Any, key: str, default=None):
        if isinstance(state, dict):
            return state.get(key, default)
        return getattr(state, key, default)

    def _to_dict(self, item: Any) -> Dict[str, Any]:
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump"):
            return item.model_dump()
        if hasattr(item, "dict"):
            return item.dict()
        return {}
