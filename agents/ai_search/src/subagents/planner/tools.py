"""检索规划子代理工具。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.main_agent.schemas import SearchPlanExecutionSpecInput


def build_planner_tools(context: Any) -> List[Any]:
    def commit_plan_draft(
        review_markdown: str,
        execution_spec: SearchPlanExecutionSpecInput,
        probe_findings: Dict[str, Any] | None = None,
        runtime: ToolRuntime = None,
    ) -> str:
        """提交正式检索计划草案到中间状态。"""
        draft = context.commit_planner_draft(
            review_markdown=review_markdown,
            execution_spec=execution_spec.model_dump(mode="python"),
            probe_findings=probe_findings,
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
