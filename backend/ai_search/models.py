"""
AI search API models and route error codes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

SEARCH_IN_PROGRESS_CODE = "SEARCH_IN_PROGRESS"
INVALID_SESSION_PHASE_CODE = "INVALID_SESSION_PHASE"
AI_SEARCH_SESSION_NOT_FOUND_CODE = "AI_SEARCH_SESSION_NOT_FOUND"
SESSION_TITLE_REQUIRED_CODE = "SESSION_TITLE_REQUIRED"
SESSION_DELETE_BLOCKED_CODE = "SESSION_DELETE_BLOCKED"


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


class AiSearchSessionUpdateRequest(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None


class AiSearchStopPolicyUpdateRequest(BaseModel):
    maxRounds: Optional[int] = Field(default=None, ge=1)
    maxQueries: Optional[int] = Field(default=None, ge=1)
    maxCandidates: Optional[int] = Field(default=None, ge=1)
    maxSelectedDocuments: Optional[int] = Field(default=None, ge=1)
    maxNoNewResultRounds: Optional[int] = Field(default=None, ge=1)
    deadlineSeconds: Optional[int] = Field(default=None, ge=30)
    targetCoverage: Optional[str] = None
    stopWhen: Optional[str] = None
    databases: Optional[List[str]] = None


class AiSearchDocumentSelectionRequest(BaseModel):
    planVersion: int = Field(..., ge=1)
    reviewDocumentIds: Optional[List[str]] = None
    removeDocumentIds: Optional[List[str]] = None


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
    retrieval: Dict[str, Any]
    artifacts: AiSearchArtifactsPayload = Field(default_factory=AiSearchArtifactsPayload)
    analysisSeed: Optional[Dict[str, Any]] = None
