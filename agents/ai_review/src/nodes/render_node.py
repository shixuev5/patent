from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from loguru import logger

from agents.common.rendering.report_render import render_markdown_to_pdf, write_markdown
from agents.patent_analysis.src.nodes.base import BaseNode
from agents.patent_analysis.src.workflow_utils import ensure_pipeline_paths, item_get, safe_artifact_name


_LEGAL_BASIS = (
    "发明或者实用新型说明书文字部分中未提及的附图标记不得在附图中出现，"
    "附图中未出现的附图标记不得在说明书文字部分中提及。"
    "申请文件中表示同一组成部分的附图标记应当一致。"
    "附图中除必需的词语外，不应当含有其他注释。"
)


class RenderNode(BaseNode):
    node_name = "render"
    progress = 95.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)
        check_result = item_get(state, "check_result", None)
        if not isinstance(check_result, dict):
            raise RuntimeError("render 阶段缺少 check_result")

        resolved_pn = str(item_get(state, "resolved_pn", "") or item_get(state, "pn", "")).strip()
        safe_pn = safe_artifact_name(resolved_pn or "task") or "task"
        md_path = Path(path_objs["root"]) / f"{safe_pn}_ai_review.md"
        pdf_path = Path(path_objs["root"]) / f"{safe_pn}_ai_review.pdf"

        consistency_text = str(check_result.get("consistency", "")).strip() or "暂无检查结果。"
        markdown_text = "\n".join(
            [
                "# AI 审查报告",
                "",
                "## 1. 审查依据",
                "**《中华人民共和国专利法实施细则》第二十一条：**",
                f"> {_LEGAL_BASIS}",
                "",
                "## 2. 最终结论",
                consistency_text,
                "",
            ]
        )

        logger.info("渲染 AI 审查 Markdown/PDF")
        write_markdown(markdown_text, md_path)
        render_markdown_to_pdf(
            md_text=markdown_text,
            output_path=pdf_path,
            title="AI Review Report",
            enable_mathjax=False,
        )

        return {
            "paths": paths,
            "resolved_pn": safe_pn,
            "final_output_pdf": str(pdf_path),
            "final_output_md": str(md_path),
            "status": "completed",
            "progress": 95.0,
        }
