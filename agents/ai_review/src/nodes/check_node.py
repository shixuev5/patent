from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from loguru import logger

from agents.ai_review.src.engines.checker import FormalExaminer
from agents.ai_review.src.nodes.base import BaseNode
from agents.ai_review.src.workflow_utils import ensure_pipeline_paths, item_get, write_json


class CheckNode(BaseNode):
    node_name = "check"
    progress = 65.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)
        parts_db = item_get(state, "parts_db", None)
        image_parts = item_get(state, "image_parts", None)
        if parts_db is None or image_parts is None:
            raise RuntimeError("check 阶段缺少 parts_db 或 image_parts")

        logger.info("执行 AI 审查")
        examiner = FormalExaminer(parts_db=parts_db, image_parts=image_parts)
        check_result = examiner.check()
        write_json(Path(path_objs["root"]) / "check.json", check_result)

        return {
            "paths": paths,
            "check_result": check_result,
        }
