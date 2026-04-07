"""
AI search API models and route error codes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from agents.ai_search.src.state import (
    AI_SEARCH_PHASES,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_CANCELLED,
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_COMPLETED,
    PHASE_DRAFTING_PLAN,
    PHASE_FAILED,
)

SEARCH_IN_PROGRESS_CODE = "SEARCH_IN_PROGRESS"
PENDING_QUESTION_EXISTS_CODE = "PENDING_QUESTION_EXISTS"
PLAN_CONFIRMATION_REQUIRED_CODE = "PLAN_CONFIRMATION_REQUIRED"
STALE_PLAN_CONFIRMATION_CODE = "STALE_PLAN_CONFIRMATION"
INVALID_SESSION_PHASE_CODE = "INVALID_SESSION_PHASE"
AI_SEARCH_SESSION_NOT_FOUND_CODE = "AI_SEARCH_SESSION_NOT_FOUND"
RESUME_NOT_AVAILABLE_CODE = "RESUME_NOT_AVAILABLE"


class AiSearchCreateSessionResponse(BaseModel):
    sessionId: str
    taskId: str
    threadId: str


class AiSearchCreateFromAnalysisRequest(BaseModel):
    analysisTaskId: str = Field(..., min_length=1)


class AiSearchSessionSummary(BaseModel):
    sessionId: str
    taskId: str
    title: str
    status: str
    phase: str
    pinned: bool = False
    activePlanVersion: Optional[int] = None
    selectedDocumentCount: int = 0
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class AiSearchSessionListResponse(BaseModel):
    items: List[AiSearchSessionSummary]
    total: int


class AiSearchMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)


class AiSearchSessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None


class AiSearchAnswerRequest(BaseModel):
    questionId: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)


class AiSearchPlanConfirmRequest(BaseModel):
    planVersion: int = Field(..., ge=1)


class AiSearchSelectedDocumentsPatchRequest(BaseModel):
    planVersion: int = Field(..., ge=1)
    addDocumentIds: Optional[List[str]] = None
    removeDocumentIds: Optional[List[str]] = None


class AiSearchFeatureTableRequest(BaseModel):
    planVersion: int = Field(..., ge=1)


class AiSearchSnapshotResponse(BaseModel):
    session: AiSearchSessionSummary
    phase: str
    messages: List[Dict[str, Any]]
    sourceSummary: Optional[Dict[str, Any]] = None
    searchElements: Optional[Dict[str, Any]] = None
    currentPlan: Optional[Dict[str, Any]] = None
    candidateDocuments: List[Dict[str, Any]] = Field(default_factory=list)
    selectedDocuments: List[Dict[str, Any]] = Field(default_factory=list)
    featureTable: Optional[Dict[str, Any]] = None
    pendingQuestion: Optional[Dict[str, Any]] = None
    pendingConfirmation: Optional[Dict[str, Any]] = None
    resumeAction: Optional[Dict[str, Any]] = None
