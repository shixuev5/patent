"""Response schemas for the coarse-screener specialist."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class CoarseScreenOutput(BaseModel):
    keep: List[str] = Field(default_factory=list)
    discard: List[str] = Field(default_factory=list)
    reasoning_summary: str = ""
