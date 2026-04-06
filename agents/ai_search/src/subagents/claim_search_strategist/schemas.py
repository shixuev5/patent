"""Response schemas for the claim-search-strategist specialist."""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class ClaimSearchStrategyOutput(BaseModel):
    planning_mode: str = ""
    targeted_gaps: List[Dict[str, Any]] = Field(default_factory=list)
    search_intents: List[Dict[str, Any]] = Field(default_factory=list)
    lane_plan: List[Dict[str, Any]] = Field(default_factory=list)
    pivot_plan: List[Dict[str, Any]] = Field(default_factory=list)
    batch_specs: List[Dict[str, Any]] = Field(default_factory=list)
    continue_search: bool = True
    replan_focus: List[str] = Field(default_factory=list)
    strategy_summary: str = ""
