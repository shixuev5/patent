"""Main-agent tool schemas for structured plan saving."""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class SearchElementInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    element_name: str = Field(..., min_length=1)
    keywords_zh: List[str] = Field(default_factory=list)
    keywords_en: List[str] = Field(default_factory=list)
    block_id: str = ""
    notes: str = ""


class QueryBlueprintInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    batch_id: str = Field(..., min_length=1)
    goal: str = ""
    sub_plan_id: str = ""


class RetrievalStepInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    step_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    purpose: str = ""
    feature_combination: str = ""
    language_strategy: str = ""
    ipc_cpc_mode: str = ""
    ipc_cpc_codes: List[str] = Field(default_factory=list)
    expected_recall: str = ""
    fallback_action: str = ""
    query_blueprint_refs: List[str] = Field(..., min_length=1)
    phase_key: str = "execute_search"
    probe_summary: Dict[str, Any] = Field(default_factory=dict)


class SubPlanInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    sub_plan_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    goal: str = Field(..., min_length=1)
    semantic_query_text: str = ""
    search_elements: List[SearchElementInput] = Field(default_factory=list)
    retrieval_steps: List[RetrievalStepInput] = Field(..., min_length=1)
    query_blueprints: List[QueryBlueprintInput] = Field(..., min_length=1)
    classification_hints: List[Dict[str, Any]] = Field(default_factory=list)


class SearchPlanExecutionSpecInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    search_scope: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    execution_policy: Dict[str, Any] = Field(default_factory=dict)
    sub_plans: List[SubPlanInput] = Field(..., min_length=1)
