from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from agents.common.utils.cache import StepCache
from agents.patent_analysis.src.engines.search import SearchStrategyGenerator
from agents.patent_analysis.src.nodes.base import BaseNode
from agents.patent_analysis.src.workflow_utils import (
    ensure_pipeline_paths,
    get_node_cache_file,
    item_get,
    read_json,
)


class SearchSemanticNode(BaseNode):
    node_name = "search_semantic"
    progress = 86.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)

        patent_data = item_get(state, "patent_data", None)
        analysis_json = item_get(state, "analysis_json", None)

        if patent_data is None and path_objs["patent_json"].exists():
            patent_data = read_json(path_objs["patent_json"])
        if analysis_json is None and path_objs["analysis_json"].exists():
            loaded_payload = read_json(path_objs["analysis_json"])
            if isinstance(loaded_payload, dict) and isinstance(loaded_payload.get("report"), dict):
                analysis_json = loaded_payload.get("report")
            else:
                analysis_json = loaded_payload

        if not patent_data or not analysis_json:
            raise RuntimeError("search_semantic 阶段缺少 patent_data 或 analysis_json")

        if path_objs["search_strategy_json"].exists():
            existing = read_json(path_objs["search_strategy_json"])
            cached_semantic = existing.get("semantic_strategy") if isinstance(existing, dict) else None
            if isinstance(cached_semantic, dict):
                logger.info("加载已有语义检索策略")
                return {
                    "paths": paths,
                    "search_semantic_strategy": cached_semantic,
                }

        cache_file = get_node_cache_file(self.config.cache_dir, self.node_name)
        cache = StepCache(cache_file)

        generator = SearchStrategyGenerator(patent_data, analysis_json)
        semantic_strategy = cache.run_step(
            "semantic_strategy_v1", generator.build_semantic_strategy
        )

        if not isinstance(semantic_strategy, dict):
            logger.warning("语义检索策略类型异常，回退为空对象")
            semantic_strategy = {}

        return {
            "paths": paths,
            "search_semantic_strategy": semantic_strategy,
        }
