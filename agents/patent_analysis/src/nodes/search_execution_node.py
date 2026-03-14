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


class SearchExecutionNode(BaseNode):
    node_name = "search_execution"
    progress = 88.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)

        patent_data = item_get(state, "patent_data", None)
        analysis_json = item_get(state, "analysis_json", None)
        search_matrix = item_get(state, "search_matrix", None)

        if patent_data is None and path_objs["patent_json"].exists():
            patent_data = read_json(path_objs["patent_json"])
        if analysis_json is None and path_objs["analysis_json"].exists():
            loaded_payload = read_json(path_objs["analysis_json"])
            if isinstance(loaded_payload, dict) and isinstance(loaded_payload.get("report"), dict):
                analysis_json = loaded_payload.get("report")
            else:
                analysis_json = loaded_payload
        if search_matrix is None and path_objs["search_strategy_json"].exists():
            existing = read_json(path_objs["search_strategy_json"])
            if isinstance(existing, dict):
                search_matrix = existing.get("search_matrix")

        if not patent_data or not analysis_json:
            raise RuntimeError("search_execution 阶段缺少 patent_data 或 analysis_json")
        if not isinstance(search_matrix, list):
            raise RuntimeError("search_execution 阶段缺少 search_matrix")

        if path_objs["search_strategy_json"].exists():
            existing = read_json(path_objs["search_strategy_json"])
            cached_plan = existing.get("execution_plan") if isinstance(existing, dict) else None
            if isinstance(cached_plan, list):
                logger.info("加载已有执行计划")
                return {
                    "paths": paths,
                    "search_execution_plan": cached_plan,
                }

        cache_file = get_node_cache_file(self.config.cache_dir, self.node_name)
        cache = StepCache(cache_file)

        generator = SearchStrategyGenerator(patent_data, analysis_json)
        execution_plan = cache.run_step(
            "execution_plan_v1", lambda: generator.build_execution_plan(search_matrix)
        )

        if not isinstance(execution_plan, list):
            logger.warning("执行计划类型异常，回退为空列表")
            execution_plan = []

        return {
            "paths": paths,
            "search_execution_plan": execution_plan,
        }
