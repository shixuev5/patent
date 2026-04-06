"""Response schemas for the claim-decomposer specialist."""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class ClaimLimitationGroup(BaseModel):
    limitation_id: str
    claim_id: str
    parent_claim_ids: List[str] = Field(default_factory=list)
    text: str
    normalized_text: str = ""
    role: str = ""
    search_terms: List[str] = Field(default_factory=list)


class ClaimDecompositionOutput(BaseModel):
    claim_packets: List[Dict[str, Any]] = Field(default_factory=list)
    limitation_groups: List[ClaimLimitationGroup] = Field(default_factory=list)
    decomposition_summary: str = ""
