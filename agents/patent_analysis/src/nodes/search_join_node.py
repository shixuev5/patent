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
        execution_plan = item_get(state, "search_execution_plan", None)

        if path_objs["search_strategy_json"].exists():
            existing = read_json(path_objs["search_strategy_json"])
            if isinstance(existing, dict):
                if search_matrix is None:
                    search_matrix = existing.get("search_matrix")
                if semantic_strategy is None:
                    semantic_strategy = existing.get("semantic_strategy")
                if execution_plan is None:
                    execution_plan = existing.get("execution_plan")

        if not isinstance(search_matrix, list):
            raise RuntimeError("search_join 阶段缺少 search_matrix")
        if not isinstance(semantic_strategy, dict):
            raise RuntimeError("search_join 阶段缺少 semantic_strategy")
        if not isinstance(execution_plan, list):
            raise RuntimeError("search_join 阶段缺少 execution_plan")

        search_json = {
            "search_matrix": search_matrix,
            "semantic_strategy": semantic_strategy,
            "execution_plan": execution_plan,
        }

        write_json(path_objs["search_strategy_json"], search_json)

        return {
            "paths": paths,
            "search_matrix": search_matrix,
            "search_semantic_strategy": semantic_strategy,
            "search_execution_plan": execution_plan,
            "search_json": search_json,
        }
