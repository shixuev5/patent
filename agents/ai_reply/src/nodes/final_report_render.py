"""
最终报告渲染节点
将 final_report.json 渲染为便于阅读的 Markdown / PDF 报告。
"""

import re
from pathlib import Path
from typing import Dict
from loguru import logger

from agents.common.rendering.report_render import (
    write_markdown,
    render_markdown_to_pdf,
)
from agents.common.utils.serialization import item_get
from agents.ai_reply.src.report_markdown import build_final_report_markdown
from agents.ai_reply.src.report_styles import OAR_REPORT_CSS
from agents.ai_reply.src.utils import PipelineCancelled, ensure_not_cancelled, get_node_cache

_MATHJAX_PATTERNS = (
    re.compile(r"\$\$.*?\$\$", re.DOTALL),
    re.compile(r"\\\(.+?\\\)", re.DOTALL),
    re.compile(r"\\\[.+?\\\]", re.DOTALL),
    re.compile(r"(?<!\$)\$[^\n$]+\$(?!\$)"),
)


def _markdown_needs_mathjax(markdown_text: str) -> bool:
    text = str(markdown_text or "")
    if not text:
        return False
    return any(pattern.search(text) for pattern in _MATHJAX_PATTERNS)


class FinalReportRenderNode:
    """最终报告渲染节点"""

    def __init__(self, config=None):
        self.config = config

    def __call__(self, state):
        logger.info("开始渲染最终报告（Markdown/PDF）")

        updates = {
            "current_node": "final_report_render",
            "status": "running",
            "progress": 98.0,
        }

        try:
            ensure_not_cancelled(self.config)
            cache = get_node_cache(self.config, "final_report_render")
            artifacts = cache.run_step("render_final_report_v11", self._render_report, state)

            updates["final_report_artifacts"] = artifacts
            updates["status"] = "completed"
            updates["progress"] = 100.0
            logger.info(f"最终报告渲染完成: {artifacts}")
        except PipelineCancelled as ex:
            logger.warning(f"最终报告渲染节点已取消: {ex}")
            updates["errors"] = [{
                "node_name": "final_report_render",
                "error_message": str(ex),
                "error_type": "cancelled",
            }]
            updates["status"] = "cancelled"
        except Exception as ex:
            logger.error(f"最终报告渲染失败: {ex}")
            updates["errors"] = [{
                "node_name": "final_report_render",
                "error_message": str(ex),
                "error_type": "final_report_render_error",
            }]
            updates["status"] = "failed"

        return updates

    def _render_report(self, state) -> Dict[str, str]:
        report = item_get(state, "final_report", None)
        if not report:
            raise ValueError("缺少 final_report，无法进行最终报告渲染")

        output_dir = Path(str(item_get(state, "output_dir", "")).strip() or ".")
        output_dir.mkdir(parents=True, exist_ok=True)

        markdown_path = output_dir / "final_report.md"
        pdf_path = output_dir / "final_report.pdf"

        markdown_text = build_final_report_markdown(report)
        enable_mathjax = _markdown_needs_mathjax(markdown_text)
        write_markdown(markdown_text, markdown_path)
        render_markdown_to_pdf(
            md_text=markdown_text,
            output_path=pdf_path,
            title="Office Action Reply Final Report",
            css_text=OAR_REPORT_CSS,
            enable_mathjax=enable_mathjax,
            enable_echarts=False,
        )

        return {
            "markdown_path": str(markdown_path),
            "pdf_path": str(pdf_path),
        }
