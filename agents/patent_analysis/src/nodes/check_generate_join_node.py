from __future__ import annotations

from typing import Any, Dict

from agents.patent_analysis.src.nodes.base import BaseNode
from agents.patent_analysis.src.workflow_utils import ensure_pipeline_paths, item_get, read_json


class CheckGenerateJoinNode(BaseNode):
    node_name = "check_generate_join"
    progress = 75.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)
        check_result = item_get(state, "check_result", None)
        report_json = item_get(state, "report_json", None)

        if check_result is None and path_objs["check_json"].exists():
            check_result = read_json(path_objs["check_json"])
        if report_json is None and path_objs["report_json"].exists():
            report_json = read_json(path_objs["report_json"])

        if check_result is None or report_json is None:
            raise RuntimeError("并行阶段未产出完整结果")

        return {
            "paths": paths,
            "check_result": check_result,
            "report_json": report_json,
        }
