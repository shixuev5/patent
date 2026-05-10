"""
AI search API models and route error codes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

SEARCH_IN_PROGRESS_CODE = "SEARCH_IN_PROGRESS"
PENDING_QUESTION_EXISTS_CODE = "PENDING_QUESTION_EXISTS"
PLAN_CONFIRMATION_REQUIRED_CODE = "PLAN_CONFIRMATION_REQUIRED"
STALE_PLAN_CONFIRMATION_CODE = "STALE_PLAN_CONFIRMATION"
INVALID_SESSION_PHASE_CODE = "INVALID_SESSION_PHASE"
AI_SEARCH_SESSION_NOT_FOUND_CODE = "AI_SEARCH_SESSION_NOT_FOUND"
RESUME_NOT_AVAILABLE_CODE = "RESUME_NOT_AVAILABLE"
HUMAN_DECISION_REQUIRED_CODE = "HUMAN_DECISION_REQUIRED"
ACTIVE_PLAN_REQUIRED_CODE = "ACTIVE_PLAN_REQUIRED"
NO_SELECTED_DOCUMENTS_CODE = "NO_SELECTED_DOCUMENTS"
SESSION_TITLE_REQUIRED_CODE = "SESSION_TITLE_REQUIRED"
SESSION_DELETE_BLOCKED_CODE = "SESSION_DELETE_BLOCKED"
EXECUTION_QUEUE_APPEND_BLOCKED_CODE = "EXECUTION_QUEUE_APPEND_BLOCKED"
EXECUTION_QUEUE_MESSAGE_NOT_FOUND_CODE = "EXECUTION_QUEUE_MESSAGE_NOT_FOUND"
EXECUTION_QUEUE_DELETE_BLOCKED_CODE = "EXECUTION_QUEUE_DELETE_BLOCKED"
EXECUTION_QUEUE_DELETE_FAILED_CODE = "EXECUTION_QUEUE_DELETE_FAILED"
ANALYSIS_SEED_REQUIRED_CODE = "ANALYSIS_SEED_REQUIRED"
ANALYSIS_SEED_ALREADY_INITIALIZED_CODE = "ANALYSIS_SEED_ALREADY_INITIALIZED"
ANALYSIS_SEED_CONTEXT_MISSING_CODE = "ANALYSIS_SEED_CONTEXT_MISSING"
MANUAL_REVIEW_RUN_REQUIRED_CODE = "MANUAL_REVIEW_RUN_REQUIRED"
DOCUMENT_REVIEW_SELECTION_REQUIRED_CODE = "DOCUMENT_REVIEW_SELECTION_REQUIRED"
DOCUMENT_REVIEW_CONFLICT_CODE = "DOCUMENT_REVIEW_CONFLICT"
DOCUMENT_REVIEW_INVALID_SHORTLISTED_CODE = "DOCUMENT_REVIEW_INVALID_SHORTLISTED"
DOCUMENT_REVIEW_INVALID_SELECTED_CODE = "DOCUMENT_REVIEW_INVALID_SELECTED"


class AiSearchCreateSessionResponse(BaseModel):
    sessionId: str
    taskId: str
    threadId: str
    reused: bool = False
    sourceTaskId: Optional[str] = None


class AiSearchCreateFromAnalysisRequest(BaseModel):
    analysisTaskId: str = Field(..., min_length=1)


class AiSearchCreateFromReplyRequest(BaseModel):
    replyTaskId: str = Field(..., min_length=1)


class AiSearchSessionSummary(BaseModel):
    sessionId: str
    taskId: str
    title: str
    status: str
    phase: str
    activityState: Literal["running", "paused", "none"] = "none"
    sourceTaskId: Optional[str] = None
    sourceType: Optional[str] = None
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


class AiSearchExecutionQueueMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)


class AiSearchSessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None


class AiSearchAnswerRequest(BaseModel):
    questionId: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)


class AiSearchDocumentReviewRequest(BaseModel):
    planVersion: int = Field(..., ge=1)
    reviewDocumentIds: Optional[List[str]] = None
    removeDocumentIds: Optional[List[str]] = None


class AiSearchFeatureComparisonRequest(BaseModel):
    planVersion: int = Field(..., ge=1)


class AiSearchExecutionQueueMessage(BaseModel):
    queueMessageId: str
    runId: str
    content: str
    ordinal: int
    createdAt: str


class AiSearchExecutionQueueResponse(BaseModel):
    items: List[AiSearchExecutionQueueMessage]


class AiSearchArtifactAttachment(BaseModel):
    attachmentId: str
    kind: str
    name: str
    downloadUrl: str
    mediaType: str
    sizeBytes: int
    createdAt: str
    isPrimary: bool = False


class AiSearchArtifactsPayload(BaseModel):
    attachments: List[AiSearchArtifactAttachment] = Field(default_factory=list)


class AiSearchSnapshotResponse(BaseModel):
    session: AiSearchSessionSummary
    run: Dict[str, Any]
    conversation: Dict[str, Any]
    stream: Dict[str, Any] = Field(default_factory=dict)
    executionMessageQueue: Dict[str, Any]
    plan: Dict[str, Any]
    retrieval: Dict[str, Any]
    analysis: Dict[str, Any]
    artifacts: AiSearchArtifactsPayload = Field(default_factory=AiSearchArtifactsPayload)
    analysisSeed: Optional[Dict[str, Any]] = None
