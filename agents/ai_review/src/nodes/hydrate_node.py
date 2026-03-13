from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from agents.ai_review.src.nodes.base import BaseNode
from agents.ai_review.src.workflow_utils import (
    ensure_pipeline_paths,
    item_get,
    refresh_output_artifact_paths,
)


class HydrateNode(BaseNode):
    node_name = "hydrate"
    progress = 5.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, _ = ensure_pipeline_paths(state)
        cached_analysis = item_get(state, "cached_analysis", None)
        if not isinstance(cached_analysis, dict):
            logger.info("未提供可复用缓存，进入全流程")
            return {
                "paths": paths,
                "reuse_hit": False,
            }

        metadata = cached_analysis.get("metadata", {})
        resolved_pn = str(metadata.get("resolved_pn") or item_get(state, "pn", "")).strip()
        data_parts = cached_analysis.get("parts")
        data_image_parts = cached_analysis.get("image_parts")

        if isinstance(data_parts, dict) and isinstance(data_image_parts, dict):
            logger.info("命中 R2 缓存，复用 parts/image_parts")
            updated_paths = paths
            if resolved_pn:
                updated_paths = refresh_output_artifact_paths(paths, resolved_pn)
            return {
                "paths": updated_paths,
                "resolved_pn": resolved_pn,
                "parts_db": data_parts,
                "image_parts": data_image_parts,
                "reuse_hit": True,
            }

        logger.info("缓存数据不完整，进入全流程")
        return {
            "paths": paths,
            "reuse_hit": False,
        }
