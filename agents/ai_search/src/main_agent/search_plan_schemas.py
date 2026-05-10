"""Search plan draft schemas."""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.ai_search.src.main_agent.schemas import (
    AiSearchActivationMode,
    QueryBlueprintInput,
    SearchScopeInput,
)


class SearchPlanRetrievalStepInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    step_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    purpose: str = ""
    feature_combination: str = ""
    language_strategy: str = ""
    ipc_cpc_mode: str = ""
    ipc_cpc_codes: list[str] = Field(default_factory=list)
    expected_recall: str = ""
    fallback_action: str = ""
    query_blueprint_refs: list[str] = Field(..., min_length=1)
    activation_mode: AiSearchActivationMode = "immediate"
    depends_on_step_ids: list[str] = Field(default_factory=list)
    activation_conditions: Dict[str, Any] = Field(default_factory=dict)
    activation_summary: str = ""
    probe_summary: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _reject_internal_fields(cls, value: Any) -> Any:
        if isinstance(value, dict) and "phase_key" in value:
            raise ValueError("检索计划草案中不允许包含 retrieval_steps[*].phase_key。")
        return value


class SearchPlanSubPlanInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    sub_plan_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    goal: str = Field(..., min_length=1)
    semantic_query_text: str = ""
    retrieval_steps: list[SearchPlanRetrievalStepInput] = Field(..., min_length=1)
    query_blueprints: list[QueryBlueprintInput] = Field(..., min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_derived_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        forbidden = [field for field in ("search_elements", "classification_hints") if field in value]
        if forbidden:
            raise ValueError(f"检索计划草案中不允许包含 {', '.join(forbidden)}。")
        return value


class SearchPlanExecutionSpecDraftInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    search_scope: SearchScopeInput = Field(default_factory=SearchScopeInput)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    execution_policy: Dict[str, Any] = Field(default_factory=dict)
    sub_plans: list[SearchPlanSubPlanInput] = Field(..., min_length=1)


class SearchPlanDraftOutput(BaseModel):
    review_markdown: str = Field(..., min_length=1)
    execution_spec: SearchPlanExecutionSpecDraftInput
