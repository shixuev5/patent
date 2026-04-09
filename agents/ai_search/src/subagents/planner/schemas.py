"""Planner subagent schemas."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from agents.ai_search.src.main_agent.schemas import SearchPlanExecutionSpecInput


class PlannerDraftOutput(BaseModel):
    review_markdown: str = Field(..., min_length=1)
    execution_spec: SearchPlanExecutionSpecInput
    probe_findings: Optional[Dict[str, Any]] = None
    planner_summary: str = ""
