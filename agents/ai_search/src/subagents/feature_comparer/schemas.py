"""特征对比子代理的响应结构。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field


AiSearchDocumentRole = Literal["X", "Y", "A"]
AiSearchCreativityReadiness = Literal["ready", "needs_more_evidence"]


class DocumentRoleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    role: AiSearchDocumentRole
    rationale: str = ""
    document_type_hint: str = ""


class CoverageGapOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = ""
    limitation_id: str = ""
    gap_type: str = ""
    gap_summary: str = ""
    suggested_keywords: List[str] = Field(default_factory=list)
    suggested_pivots: List[str] = Field(default_factory=list)


class FeatureCompareOutput(BaseModel):
    table_rows: List[Dict[str, Any]] = Field(default_factory=list)
    document_roles: List[DocumentRoleOutput] = Field(default_factory=list)
    coverage_gaps: List[CoverageGapOutput] = Field(default_factory=list)
    follow_up_search_hints: List[str] = Field(default_factory=list)
    creativity_readiness: AiSearchCreativityReadiness
