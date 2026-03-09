from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from agents.patent_analysis.src.nodes.base import BaseNode
from agents.patent_analysis.src.engines.vision import VisualProcessor
from agents.patent_analysis.src.workflow_utils import ensure_pipeline_paths, item_get, read_json, write_json


class VisionNode(BaseNode):
    node_name = "vision"
    progress = 55.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)

        patent_data = item_get(state, "patent_data", None)
        parts_db = item_get(state, "parts_db", None)
        if not patent_data or not parts_db:
            raise RuntimeError("vision 阶段缺少 patent_data 或 parts_db")

        if path_objs["image_parts_json"].exists():
            logger.info("加载已有图像部件映射")
            image_parts = read_json(path_objs["image_parts_json"])
        else:
            logger.info("执行图像处理与 OCR")
            processor = VisualProcessor(
                patent_data=patent_data,
                parts_db=parts_db,
                raw_img_dir=path_objs["raw_images_dir"],
                out_dir=path_objs["annotated_dir"],
            )
            image_parts = processor.process_patent_images()
            write_json(path_objs["image_parts_json"], image_parts)

        return {
            "paths": paths,
            "image_parts": image_parts,
        }
