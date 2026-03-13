from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from agents.common.parsers.pdf_parser import PDFParser
from agents.ai_review.src.nodes.base import BaseNode
from agents.ai_review.src.workflow_utils import ensure_pipeline_paths


class ParseNode(BaseNode):
    node_name = "parse"
    progress = 20.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)

        if not path_objs["raw_md"].exists():
            logger.info("开始解析 PDF")
            PDFParser.parse(path_objs["raw_pdf"], path_objs["mineru_dir"])
        else:
            logger.info("已存在解析结果，跳过 PDF 解析")

        if not path_objs["raw_md"].exists():
            raise RuntimeError("PDF 解析未产出 raw.md")

        return {"paths": paths}
