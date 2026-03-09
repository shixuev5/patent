from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from agents.patent_analysis.src.engines.knowledge import KnowledgeExtractor
from agents.patent_analysis.src.nodes.base import BaseNode
from agents.patent_analysis.src.workflow_utils import ensure_pipeline_paths, item_get, read_json, write_json


class ExtractNode(BaseNode):
    node_name = "extract"
    progress = 40.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)

        patent_data = item_get(state, "patent_data", None)
        if not patent_data:
            if not path_objs["patent_json"].exists():
                raise RuntimeError("缺少 patent_data，且 patent.json 不存在")
            patent_data = read_json(path_objs["patent_json"])

        if path_objs["parts_json"].exists():
            logger.info("加载已有部件知识库")
            parts_db = read_json(path_objs["parts_json"])
        else:
            logger.info("提取知识要素")
            extractor = KnowledgeExtractor()
            parts_db = extractor.extract_entities(patent_data)
            write_json(path_objs["parts_json"], parts_db)

        return {
            "paths": paths,
            "patent_data": patent_data,
            "parts_db": parts_db,
        }
