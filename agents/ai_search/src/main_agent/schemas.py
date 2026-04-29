"""Main-agent tool schemas for structured plan saving."""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field


AiSearchDatabaseName = Literal["zhihuiya", "openalex", "semanticscholar", "crossref"]
AiSearchActivationMode = Literal["immediate", "conditional"]
AiSearchStepPhaseKey = Literal["execute_search", "coarse_screen", "close_read", "feature_comparison"]


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


class SearchScopeInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    objective: str = ""
    applicants: List[str] = Field(default_factory=list)
    filing_date: Any = None
    priority_date: Any = None
    languages: List[str] = Field(default_factory=list)
    databases: List[AiSearchDatabaseName] = Field(default_factory=lambda: ["zhihuiya"])
    excluded_items: List[str] = Field(default_factory=list)
    source: Dict[str, Any] = Field(default_factory=dict)


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
    phase_key: AiSearchStepPhaseKey = "execute_search"
    activation_mode: AiSearchActivationMode = "immediate"
    depends_on_step_ids: List[str] = Field(default_factory=list)
    activation_conditions: Dict[str, Any] = Field(default_factory=dict)
    activation_summary: str = ""
    probe_summary: Dict[str, Any] = Field(default_factory=dict)


class SubPlanInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    sub_plan_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    goal: str = Field(..., min_length=1)
    semantic_query_text: str = ""
    retrieval_steps: List[RetrievalStepInput] = Field(..., min_length=1)
    query_blueprints: List[QueryBlueprintInput] = Field(..., min_length=1)


class SearchPlanExecutionSpecInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    search_scope: SearchScopeInput = Field(default_factory=SearchScopeInput)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    execution_policy: Dict[str, Any] = Field(default_factory=dict)
    sub_plans: List[SubPlanInput] = Field(..., min_length=1)
    search_elements_snapshot: Dict[str, Any] = Field(default_factory=dict)
