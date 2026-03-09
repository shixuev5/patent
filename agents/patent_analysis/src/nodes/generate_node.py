from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from agents.patent_analysis.src.engines.generator import ContentGenerator
from agents.patent_analysis.src.nodes.base import BaseNode
from agents.patent_analysis.src.workflow_utils import (
    ensure_pipeline_paths,
    get_node_cache_file,
    item_get,
    read_json,
    write_json,
)


class GenerateNode(BaseNode):
    node_name = "generate"
    progress = 70.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)

        patent_data = item_get(state, "patent_data", None)
        parts_db = item_get(state, "parts_db", None)
        image_parts = item_get(state, "image_parts", None)
        if not patent_data or not parts_db or image_parts is None:
            raise RuntimeError("generate 阶段缺少 patent_data/parts_db/image_parts")

        if path_objs["report_json"].exists():
            logger.info("加载已有报告 JSON")
            report_json = read_json(path_objs["report_json"])
        else:
            logger.info("生成报告 JSON")
            cache_file = get_node_cache_file(self.config.cache_dir, self.node_name)
            generator = ContentGenerator(
                patent_data=patent_data,
                parts_db=parts_db,
                image_parts=image_parts,
                annotated_dir=path_objs["annotated_dir"],
                cache_file=cache_file,
            )
            report_json = generator.generate_report_json()
            write_json(path_objs["report_json"], report_json)

        return {
            "paths": paths,
            "report_json": report_json,
        }
