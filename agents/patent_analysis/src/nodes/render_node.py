from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from agents.patent_analysis.src.nodes.base import BaseNode
from agents.patent_analysis.src.engines.renderer import ReportRenderer
from agents.patent_analysis.src.workflow_utils import ensure_pipeline_paths, item_get


class RenderNode(BaseNode):
    node_name = "render"
    progress = 95.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)

        patent_data = item_get(state, "patent_data", None)
        check_result = item_get(state, "check_result", None)
        report_json = item_get(state, "report_json", None)
        search_json = item_get(state, "search_json", None)

        if not patent_data or check_result is None or not report_json or not search_json:
            raise RuntimeError("render 阶段缺少必要输入")

        logger.info("渲染 Markdown/PDF 报告")
        renderer = ReportRenderer(patent_data)
        renderer.render(
            report_data=report_json,
            check_result=check_result,
            search_data=search_json,
            md_path=path_objs["final_md"],
            pdf_path=path_objs["final_pdf"],
        )

        return {
            "paths": paths,
            "final_output_pdf": str(path_objs["final_pdf"]),
            "final_output_md": str(path_objs["final_md"]),
            "status": "completed",
            "progress": 95.0,
        }
