"""检索规划子代理工具。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.runtime_context import resolve_agent_context
from agents.ai_search.src.subagents.planner.schemas import PlannerDraftOutput


def build_planner_tools() -> List[Any]:
    def save_planner_draft(
        review_markdown: str,
        execution_spec: Dict[str, Any],
        probe_findings: Dict[str, Any] | None = None,
        runtime: ToolRuntime = None,
    ) -> str:
        """保存 planner draft。"""
        resolved_context = resolve_agent_context(runtime)
        payload = PlannerDraftOutput.model_validate(
            {
                "review_markdown": review_markdown,
                "execution_spec": execution_spec,
                "probe_findings": probe_findings,
            }
        )
        draft = resolved_context.save_planner_draft_payload(
            review_markdown=payload.review_markdown,
            execution_spec=payload.execution_spec.model_dump(mode="python"),
            probe_findings=payload.probe_findings,
            runtime=runtime.context if runtime else None,
        )
        target_plan_version = int(draft.get("target_plan_version") or draft.get("plan_version") or 0)
        execution_payload = draft.get("execution_spec") if isinstance(draft.get("execution_spec"), dict) else {}
        return json.dumps(
            {
                "target_plan_version": target_plan_version,
                "sub_plan_count": len(execution_payload.get("sub_plans") or []),
                "draft_status": str(draft.get("draft_status") or "").strip() or "drafting",
            },
            ensure_ascii=False,
        )

    return [save_planner_draft]
