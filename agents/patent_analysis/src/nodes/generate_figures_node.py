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


class GenerateFiguresNode(BaseNode):
    node_name = "generate_figures"
    progress = 78.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)

        patent_data = item_get(state, "patent_data", None)
        parts_db = item_get(state, "parts_db", None)
        image_parts = item_get(state, "image_parts", None)

        if not patent_data or not parts_db or image_parts is None:
            raise RuntimeError("generate_figures 阶段缺少 patent_data/parts_db/image_parts")

        report_core_json = item_get(state, "report_core_json", None)
        if report_core_json is None and path_objs["report_core_json"].exists():
            report_core_json = read_json(path_objs["report_core_json"])

        if not report_core_json:
            raise RuntimeError("generate_figures 阶段缺少 report_core_json")

        if path_objs["report_json"].exists():
            logger.info("加载已有完整报告 JSON")
            report_json = read_json(path_objs["report_json"])
        else:
            logger.info("生成附图讲解并合并报告 JSON")
            cache_file = get_node_cache_file(self.config.cache_dir, self.node_name)
            generator = ContentGenerator(
                patent_data=patent_data,
                parts_db=parts_db,
                image_parts=image_parts,
                annotated_dir=path_objs["annotated_dir"],
                cache_file=cache_file,
            )
            figure_explanations = generator.generate_figure_explanations(report_core_json)
            report_json = dict(report_core_json)
            report_json["figure_explanations"] = figure_explanations
            write_json(path_objs["report_json"], report_json)

        return {
            "paths": paths,
            "report_core_json": report_core_json,
            "report_json": report_json,
        }
