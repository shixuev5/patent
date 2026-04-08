"""特征对比子代理的响应结构。"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class FeatureCompareOutput(BaseModel):
    table_rows: List[Dict[str, Any]] = Field(default_factory=list)
    summary_markdown: str = ""
    overall_findings: str = ""
    document_roles: List[Dict[str, Any]] = Field(default_factory=list)
    difference_highlights: List[Dict[str, Any]] = Field(default_factory=list)
    coverage_gaps: List[Dict[str, Any]] = Field(default_factory=list)
    follow_up_search_hints: List[str] = Field(default_factory=list)
    creativity_readiness: str = ""
    readiness_rationale: str = ""
