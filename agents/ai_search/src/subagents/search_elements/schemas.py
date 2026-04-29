"""Structured output schema for the search-elements specialist."""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

from agents.ai_search.src.main_agent.schemas import SearchElementInput


class SearchElementsOutput(BaseModel):
    status: Literal["complete", "needs_answer"] = "complete"
    objective: str = ""
    applicants: List[str] = Field(default_factory=list)
    filing_date: str | None = None
    priority_date: str | None = None
    missing_items: List[str] = Field(default_factory=list)
    search_elements: List[SearchElementInput] = Field(default_factory=list)
