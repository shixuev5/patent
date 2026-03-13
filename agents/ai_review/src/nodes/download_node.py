from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict

from loguru import logger

from agents.common.search_clients.factory import SearchClientFactory
from agents.ai_review.src.nodes.base import BaseNode
from agents.ai_review.src.workflow_utils import ensure_pipeline_paths, item_get, safe_artifact_name


class DownloadNode(BaseNode):
    node_name = "download"
    progress = 10.0

    def run(self, state: Any) -> Dict[str, Any]:
        paths, path_objs = ensure_pipeline_paths(state)
        raw_pdf_path = path_objs["raw_pdf"]

        if raw_pdf_path.exists():
            logger.info("已存在 raw.pdf，跳过下载")
            return {
                "paths": paths,
                "resolved_pn": safe_artifact_name(item_get(state, "pn", "")),
            }

        upload_file_path = str(item_get(state, "upload_file_path", "") or "").strip()
        if upload_file_path and Path(upload_file_path).exists():
            logger.info(f"使用上传文件: {upload_file_path}")
            shutil.copy2(upload_file_path, raw_pdf_path)
            return {
                "paths": paths,
                "resolved_pn": safe_artifact_name(item_get(state, "pn", "")),
            }

        pn = str(item_get(state, "pn", "") or "").strip()
        if not pn:
            raise ValueError("未提供专利号且未上传 PDF，无法下载专利文档")

        logger.info("下载专利原文")
        client = SearchClientFactory.get_client("zhihuiya")
        success = client.download_patent_document(pn, str(raw_pdf_path))
        if not success:
            raise RuntimeError(f"下载失败: {pn}（API 返回异常或文件为空）")

        return {
            "paths": paths,
            "resolved_pn": safe_artifact_name(pn),
        }
