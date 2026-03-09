from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from agents.patent_analysis.src.state import WorkflowConfig
from agents.patent_analysis.src.workflow_utils import PipelineCancelled, item_get


class BaseNode:
    node_name: str = "base"
    progress: Optional[float] = None

    def __init__(self, config: WorkflowConfig | None = None):
        self.config = config or WorkflowConfig()

    def ensure_not_cancelled(self) -> None:
        cancel_event = getattr(self.config, "cancel_event", None)
        if cancel_event is not None and cancel_event.is_set():
            raise PipelineCancelled("任务已取消")

    def run(self, state: Any) -> Dict[str, Any]:
        raise NotImplementedError

    def __call__(self, state: Any) -> Dict[str, Any]:
        current_status = str(item_get(state, "status", "pending") or "pending").lower()
        if current_status in {"failed", "cancelled"}:
            return {"status": current_status}

        updates: Dict[str, Any] = {
            "current_node": self.node_name,
            "status": "running",
        }
        if self.progress is not None:
            updates["progress"] = float(self.progress)

        try:
            self.ensure_not_cancelled()
            run_updates = self.run(state) or {}
            updates.update(run_updates)
        except PipelineCancelled as exc:
            updates["status"] = "cancelled"
            updates["errors"] = [
                {
                    "node_name": self.node_name,
                    "error_message": str(exc),
                    "error_type": "cancelled",
                }
            ]
        except Exception as exc:  # pragma: no cover - branch kept for runtime resilience
            logger.exception(f"节点 {self.node_name} 执行失败: {exc}")
            updates["status"] = "failed"
            updates["errors"] = [
                {
                    "node_name": self.node_name,
                    "error_message": str(exc),
                    "error_type": self.node_name,
                }
            ]

        return updates
