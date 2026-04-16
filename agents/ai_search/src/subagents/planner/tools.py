"""检索规划子代理工具。"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.main_agent.schemas import SubPlanInput


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
    def save_plan_review_markdown(
        review_markdown: str,
        runtime: ToolRuntime = None,
    ) -> str:
        """保存面向用户展示的 Markdown 计划正文。"""
        draft = context.save_planner_review_markdown(review_markdown, runtime=runtime)
        return json.dumps(
            {
                "draft_id": draft.get("draft_id"),
                "draft_version": int(draft.get("draft_version") or 0),
            },
            ensure_ascii=False,
        )

    def save_plan_execution_overview(
        search_scope: Dict[str, Any],
        constraints: Dict[str, Any],
        execution_policy: Dict[str, Any],
        probe_findings: Dict[str, Any] | str | None = None,
        runtime: ToolRuntime = None,
    ) -> str:
        """保存检索计划顶层结构与可选预检信号。"""
        draft = context.save_planner_execution_overview(
            search_scope=search_scope if isinstance(search_scope, dict) else {},
            constraints=constraints if isinstance(constraints, dict) else {},
            execution_policy=execution_policy if isinstance(execution_policy, dict) else {},
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

    def append_plan_sub_plan(
        sub_plan: SubPlanInput,
        runtime: ToolRuntime = None,
    ) -> str:
        """追加或替换一个子计划片段。"""
        draft = context.append_planner_sub_plan(
            sub_plan.model_dump(mode="python"),
            runtime=runtime,
        )
        return json.dumps(
            {
                "draft_id": draft.get("draft_id"),
                "draft_version": int(draft.get("draft_version") or 0),
                "sub_plan_id": str(sub_plan.sub_plan_id or "").strip(),
                "sub_plan_count": len(((draft.get("execution_spec") or {}).get("sub_plans") or [])),
            },
            ensure_ascii=False,
        )

    def finalize_plan_draft(runtime: ToolRuntime = None) -> str:
        """校验并封口当前计划草案。"""
        draft = context.finalize_planner_draft(runtime=runtime)
        return json.dumps(
            {
                "draft_id": draft.get("draft_id"),
                "draft_version": int(draft.get("draft_version") or 0),
                "plan_version": int(draft.get("plan_version") or 0),
                "draft_status": str(draft.get("draft_status") or "").strip(),
            },
            ensure_ascii=False,
        )

    return [
        save_plan_review_markdown,
        save_plan_execution_overview,
        append_plan_sub_plan,
        finalize_plan_draft,
    ]
