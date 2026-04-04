"""
AI search API models and shared constants.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


PHASE_COLLECTING_REQUIREMENTS = "collecting_requirements"
PHASE_AWAITING_USER_ANSWER = "awaiting_user_answer"
PHASE_DRAFTING_PLAN = "drafting_plan"
PHASE_AWAITING_PLAN_CONFIRMATION = "awaiting_plan_confirmation"
PHASE_SEARCHING = "searching"
PHASE_RESULTS_READY = "results_ready"
PHASE_COMPLETED = "completed"
PHASE_FAILED = "failed"
PHASE_CANCELLED = "cancelled"

AI_SEARCH_PHASES = {
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_DRAFTING_PLAN,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_SEARCHING,
    PHASE_RESULTS_READY,
    PHASE_COMPLETED,
    PHASE_FAILED,
    PHASE_CANCELLED,
}

AI_SEARCH_PROGRESS = {
    PHASE_COLLECTING_REQUIREMENTS: 10,
    PHASE_AWAITING_USER_ANSWER: 20,
    PHASE_DRAFTING_PLAN: 35,
    PHASE_AWAITING_PLAN_CONFIRMATION: 35,
    PHASE_SEARCHING: 50,
    PHASE_RESULTS_READY: 80,
    PHASE_COMPLETED: 100,
    PHASE_FAILED: 100,
    PHASE_CANCELLED: 100,
}

AI_SEARCH_STEP = {
    PHASE_COLLECTING_REQUIREMENTS: "整理检索需求",
    PHASE_AWAITING_USER_ANSWER: "补全检索要素",
    PHASE_DRAFTING_PLAN: "起草检索计划",
    PHASE_AWAITING_PLAN_CONFIRMATION: "等待计划确认",
    PHASE_SEARCHING: "执行专利检索",
    PHASE_RESULTS_READY: "精读候选文献",
    PHASE_COMPLETED: "当前轮完成",
    PHASE_FAILED: "当前轮失败",
    PHASE_CANCELLED: "会话已终止",
}

SEARCH_IN_PROGRESS_CODE = "SEARCH_IN_PROGRESS"
PENDING_QUESTION_EXISTS_CODE = "PENDING_QUESTION_EXISTS"
PLAN_CONFIRMATION_REQUIRED_CODE = "PLAN_CONFIRMATION_REQUIRED"
STALE_PLAN_CONFIRMATION_CODE = "STALE_PLAN_CONFIRMATION"
INVALID_SESSION_PHASE_CODE = "INVALID_SESSION_PHASE"
AI_SEARCH_SESSION_NOT_FOUND_CODE = "AI_SEARCH_SESSION_NOT_FOUND"


class AiSearchCreateSessionResponse(BaseModel):
    sessionId: str
    taskId: str
    threadId: str


class AiSearchSessionSummary(BaseModel):
    sessionId: str
    taskId: str
    title: str
    status: str
    phase: str
    activePlanVersion: Optional[int] = None
    selectedDocumentCount: int = 0
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class AiSearchSessionListResponse(BaseModel):
    items: List[AiSearchSessionSummary]
    total: int


class AiSearchMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)


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
    searchElements: Optional[Dict[str, Any]] = None
    currentPlan: Optional[Dict[str, Any]] = None
    candidateDocuments: List[Dict[str, Any]] = Field(default_factory=list)
    selectedDocuments: List[Dict[str, Any]] = Field(default_factory=list)
    featureTable: Optional[Dict[str, Any]] = None
    pendingQuestion: Optional[Dict[str, Any]] = None
    pendingConfirmation: Optional[Dict[str, Any]] = None
