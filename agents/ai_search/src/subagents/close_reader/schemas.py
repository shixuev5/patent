"""精读子代理的响应结构。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class KeyPassageOutput(BaseModel):
    document_id: str
    passage: str
    reason: str = ""
    location: Optional[str] = None


class ClaimAlignmentOutput(BaseModel):
    document_id: str
    claim_id: str
    limitation_id: str = ""
    passage: str
    support: str = ""
    reason: str = ""
    location: Optional[str] = None


class LimitationCoverageOutput(BaseModel):
    claim_id: str
    limitation_id: str = ""
    status: str = ""
    supporting_document_ids: List[str] = Field(default_factory=list)
    reason: str = ""


class LimitationGapOutput(BaseModel):
    claim_id: str
    limitation_id: str = ""
    gap_type: str = ""
    gap_summary: str = ""
    suggested_keywords: List[str] = Field(default_factory=list)
    suggested_pivots: List[str] = Field(default_factory=list)


class DocumentAssessmentOutput(BaseModel):
    document_id: str
    decision: str = ""
    confidence: float = 0.0
    evidence_sufficiency: str = ""
    missing_evidence: List[str] = Field(default_factory=list)


class CloseReaderOutput(BaseModel):
    selected: List[str] = Field(default_factory=list)
    rejected: List[str] = Field(default_factory=list)
    key_passages: List[KeyPassageOutput] = Field(default_factory=list)
    claim_alignments: List[ClaimAlignmentOutput] = Field(default_factory=list)
    limitation_coverage: List[LimitationCoverageOutput] = Field(default_factory=list)
    limitation_gaps: List[LimitationGapOutput] = Field(default_factory=list)
    document_assessments: List[DocumentAssessmentOutput] = Field(default_factory=list)
