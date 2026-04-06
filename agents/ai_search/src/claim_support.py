"""AI search claim-aware support models and helpers."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field

from agents.common.patent_structuring import extract_structured_claims
from agents.common.patent_structuring.models import PatentClaim


class StructuredClaim(BaseModel):
    claim_id: str
    claim_text: str
    claim_type: str
    parent_claim_ids: List[str] = Field(default_factory=list)


class ExpandedClaim(BaseModel):
    claim_id: str
    claim_type: str
    claim_text: str
    parent_claim_ids: List[str] = Field(default_factory=list)
    lineage_claim_ids: List[str] = Field(default_factory=list)
    combined_claim_text: str = ""
    expanded_limitations: List[str] = Field(default_factory=list)


class ClaimPacket(BaseModel):
    claim_id: str
    claim_type: str
    claim_text: str
    parent_claim_ids: List[str] = Field(default_factory=list)
    lineage_claim_ids: List[str] = Field(default_factory=list)
    combined_claim_text: str = ""
    expanded_limitations: List[str] = Field(default_factory=list)
    search_elements: List[Dict[str, Any]] = Field(default_factory=list)
    candidate_terms: List[str] = Field(default_factory=list)


class ClaimLimitation(BaseModel):
    limitation_id: str
    claim_id: str
    parent_claim_ids: List[str] = Field(default_factory=list)
    text: str
    normalized_text: str = ""
    role: str = ""
    search_terms: List[str] = Field(default_factory=list)


class ClaimSearchPlan(BaseModel):
    search_intents: List[Dict[str, Any]] = Field(default_factory=list)
    lane_plan: List[Dict[str, Any]] = Field(default_factory=list)
    pivot_plan: List[Dict[str, Any]] = Field(default_factory=list)
    batch_specs: List[Dict[str, Any]] = Field(default_factory=list)
    strategy_summary: str = ""


class ClaimEvidenceAlignment(BaseModel):
    document_id: str
    claim_id: str
    limitation_id: str = ""
    passage: str
    support: str = ""
    reason: str = ""
    location: Optional[str] = None


def normalize_structured_claims(raw_claims: Iterable[Any]) -> List[Dict[str, Any]]:
    outputs: List[Dict[str, Any]] = []
    for item in raw_claims:
        try:
            claim = PatentClaim.model_validate(item)
        except Exception:
            continue
        outputs.append(
            StructuredClaim(
                claim_id=str(claim.claim_id or "").strip(),
                claim_text=str(claim.claim_text or "").strip(),
                claim_type=str(claim.claim_type or "").strip(),
                parent_claim_ids=[str(parent).strip() for parent in claim.parent_claim_ids if str(parent).strip()],
            ).model_dump()
        )
    return outputs


def load_structured_claims_from_markdown(markdown_text: str) -> List[Dict[str, Any]]:
    return normalize_structured_claims(extract_structured_claims(str(markdown_text or "")))


def load_structured_claims_from_patent_data(patent_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    claims = patent_data.get("claims") if isinstance(patent_data, dict) else None
    if not isinstance(claims, list):
        return []
    return normalize_structured_claims(claims)


def expand_claim_dependency(raw_claims: Iterable[Any], claim_ids: Optional[Iterable[Any]] = None) -> List[Dict[str, Any]]:
    normalized = normalize_structured_claims(raw_claims)
    if not normalized:
        return []
    claim_map = {str(item.get("claim_id") or "").strip(): item for item in normalized if str(item.get("claim_id") or "").strip()}
    requested = {str(item).strip() for item in (claim_ids or []) if str(item).strip()}
    targets = [claim_id for claim_id in claim_map if not requested or claim_id in requested]
    expanded: List[Dict[str, Any]] = []
    for claim_id in targets:
        lineage: List[str] = []
        limitations: List[str] = []
        stack = [claim_id]
        visited: set[str] = set()
        while stack:
            current = stack.pop(0)
            if current in visited or current not in claim_map:
                continue
            visited.add(current)
            claim = claim_map[current]
            lineage.append(current)
            claim_text = str(claim.get("claim_text") or "").strip()
            if claim_text:
                limitations.append(claim_text)
            for parent_id in claim.get("parent_claim_ids") or []:
                text = str(parent_id or "").strip()
                if text and text not in visited:
                    stack.append(text)
        base = claim_map[claim_id]
        expanded.append(
            ExpandedClaim(
                claim_id=claim_id,
                claim_type=str(base.get("claim_type") or "").strip(),
                claim_text=str(base.get("claim_text") or "").strip(),
                parent_claim_ids=[str(parent).strip() for parent in (base.get("parent_claim_ids") or []) if str(parent).strip()],
                lineage_claim_ids=lineage,
                combined_claim_text="\n".join(limitations),
                expanded_limitations=limitations,
            ).model_dump()
        )
    return expanded


def build_claim_packets(
    expanded_claims: Iterable[Any],
    search_elements: Optional[Iterable[Any]] = None,
) -> List[Dict[str, Any]]:
    elements = [item for item in (search_elements or []) if isinstance(item, dict)]
    packets: List[Dict[str, Any]] = []
    for item in expanded_claims:
        try:
            claim = ExpandedClaim.model_validate(item)
        except Exception:
            continue
        candidate_terms: List[str] = []
        for limitation in claim.expanded_limitations:
            text = str(limitation or "").strip()
            if text and text not in candidate_terms:
                candidate_terms.append(text)
        for element in elements:
            for key in ("keywords_zh", "keywords_en"):
                values = element.get(key) if isinstance(element.get(key), list) else []
                for value in values:
                    text = str(value or "").strip()
                    if text and text not in candidate_terms:
                        candidate_terms.append(text)
        packets.append(
            ClaimPacket(
                claim_id=claim.claim_id,
                claim_type=claim.claim_type,
                claim_text=claim.claim_text,
                parent_claim_ids=claim.parent_claim_ids,
                lineage_claim_ids=claim.lineage_claim_ids,
                combined_claim_text=claim.combined_claim_text,
                expanded_limitations=claim.expanded_limitations,
                search_elements=elements,
                candidate_terms=candidate_terms,
            ).model_dump()
        )
    return packets
