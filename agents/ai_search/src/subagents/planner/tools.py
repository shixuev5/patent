"""检索规划子代理工具。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.main_agent.schemas import SearchPlanExecutionSpecInput


def _normalize_probe_findings(value: Any) -> Dict[str, Any] | None:
    if isinstance(value, dict):
        return value or None
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "undefined"}:
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    if isinstance(parsed, dict):
        return parsed or None
    return None


def build_planner_tools(context: Any) -> List[Any]:
    def commit_plan_draft(
        review_markdown: str,
        execution_spec: SearchPlanExecutionSpecInput,
        probe_findings: Dict[str, Any] | str | None = None,
        runtime: ToolRuntime = None,
    ) -> str:
        """提交正式检索计划草案到中间状态。"""
        draft = context.commit_planner_draft(
            review_markdown=review_markdown,
            execution_spec=execution_spec.model_dump(mode="python"),
            probe_findings=_normalize_probe_findings(probe_findings),
            runtime=runtime,
        )
        return json.dumps(
            {
                "draft_id": draft.get("draft_id"),
                "draft_version": int(draft.get("draft_version") or 0),
            },
            ensure_ascii=False,
        )

    return [commit_plan_draft]
