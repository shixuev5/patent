from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from agents.common.patent_engines.vision import VisualProcessor
from agents.patent_analysis.src.nodes.base import BaseNode
from agents.patent_analysis.src.workflow_utils import ensure_pipeline_paths, item_get, read_json


class VisionAnnotateNode(BaseNode):
    node_name = "vision_annotate"
    progress = 58.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)

        image_labels = item_get(state, "image_labels", None)
        if image_labels is None and path_objs["image_labels_json"].exists():
            image_labels = read_json(path_objs["image_labels_json"])

        if image_labels is None:
            raise RuntimeError("vision_annotate 阶段缺少 image_labels")

        logger.info("执行图像标注渲染")
        processor = VisualProcessor(
            patent_data={},
            parts_db={},
            raw_img_dir=path_objs["raw_images_dir"],
            out_dir=path_objs["annotated_dir"],
            init_ocr=False,
        )
        processor.annotate_from_image_labels(image_labels)

        return {
            "paths": paths,
            "image_labels": image_labels,
        }
