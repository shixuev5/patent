from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from agents.common.patent_structuring import extract_structured_data
from agents.ai_review.src.nodes.base import BaseNode
from agents.ai_review.src.workflow_utils import (
    ensure_pipeline_paths,
    item_get,
    read_json,
    refresh_output_artifact_paths,
    resolve_pn,
    write_json,
)


class TransformNode(BaseNode):
    node_name = "transform"
    progress = 30.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)

        if path_objs["patent_json"].exists():
            logger.info("加载已有结构化专利数据")
            patent_data = read_json(path_objs["patent_json"])
        else:
            logger.info("将 Markdown 转换为结构化 JSON")
            md_content = path_objs["raw_md"].read_text(encoding="utf-8")
            patent_data = extract_structured_data(md_content, method="hybrid")
            write_json(path_objs["patent_json"], patent_data)

        fallback = str(item_get(state, "task_id", "") or item_get(state, "pn", "") or "task")
        resolved_pn = resolve_pn(item_get(state, "pn", ""), patent_data, fallback=fallback)
        updated_paths = refresh_output_artifact_paths(paths, resolved_pn)

        return {
            "paths": updated_paths,
            "patent_data": patent_data,
            "pn": resolved_pn,
            "resolved_pn": resolved_pn,
        }
