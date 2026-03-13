from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from agents.ai_review.src.workflow_utils import item_get


def handle_error(state: Any) -> Dict[str, Any]:
    errors = item_get(state, "errors", []) or []
    logger.error(f"工作流执行过程中出现错误，共 {len(errors)} 个错误")
    for error in errors:
        node_name = item_get(error, "node_name", "unknown")
        error_message = item_get(error, "error_message", str(error))
        logger.error(f"节点 {node_name}: {error_message}")

    status = str(item_get(state, "status", "failed") or "failed").lower()
    if status not in {"failed", "cancelled"}:
        status = "failed"

    return {
        "current_node": "handle_error",
        "status": status,
    }
