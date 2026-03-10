from __future__ import annotations

from typing import Any, Dict

from agents.patent_analysis.src.nodes.base import BaseNode
from agents.patent_analysis.src.workflow_utils import (
    ensure_pipeline_paths,
    item_get,
    read_json,
    write_json,
)


class SearchJoinNode(BaseNode):
    node_name = "search_join"
    progress = 89.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)

        search_matrix = item_get(state, "search_matrix", None)
        semantic_strategy = item_get(state, "search_semantic_strategy", None)

        if path_objs["search_strategy_json"].exists():
            existing = read_json(path_objs["search_strategy_json"])
            if isinstance(existing, dict):
                if search_matrix is None:
                    search_matrix = existing.get("search_matrix")
                if semantic_strategy is None:
                    semantic_strategy = existing.get("semantic_strategy")

        if not isinstance(search_matrix, list):
            raise RuntimeError("search_join 阶段缺少 search_matrix")
        if not isinstance(semantic_strategy, dict):
            raise RuntimeError("search_join 阶段缺少 semantic_strategy")

        search_json = {
            "search_matrix": search_matrix,
            "semantic_strategy": semantic_strategy,
        }

        write_json(path_objs["search_strategy_json"], search_json)

        return {
            "paths": paths,
            "search_matrix": search_matrix,
            "search_semantic_strategy": semantic_strategy,
            "search_json": search_json,
        }
