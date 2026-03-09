from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from agents.patent_analysis.src.nodes.base import BaseNode
from agents.patent_analysis.src.engines.search import SearchStrategyGenerator
from agents.patent_analysis.src.workflow_utils import (
    ensure_pipeline_paths,
    get_node_cache_file,
    item_get,
    read_json,
    write_json,
)


class SearchNode(BaseNode):
    node_name = "search"
    progress = 85.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)

        patent_data = item_get(state, "patent_data", None)
        report_json = item_get(state, "report_json", None)

        if not patent_data or not report_json:
            raise RuntimeError("search 阶段缺少 patent_data 或 report_json")

        if path_objs["search_strategy_json"].exists():
            logger.info("加载已有检索策略")
            search_json = read_json(path_objs["search_strategy_json"])
        else:
            logger.info("生成检索策略 JSON")
            cache_file = get_node_cache_file(self.config.cache_dir, self.node_name)
            search_gen = SearchStrategyGenerator(patent_data, report_json, cache_file)
            search_json = search_gen.generate_strategy()
            write_json(path_objs["search_strategy_json"], search_json)

        return {
            "paths": paths,
            "search_json": search_json,
        }
