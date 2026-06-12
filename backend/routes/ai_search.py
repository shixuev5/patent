"""
AI search routes.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from backend.auth import _get_current_user
from backend.models import CurrentUser

from backend.ai_search.models import (
    AiSearchCreateFromAnalysisRequest,
    AiSearchCreateFromReplyRequest,
    AiSearchDocumentSelectionRequest,
    AiSearchMessageRequest,
    AiSearchSessionUpdateRequest,
    AiSearchStopPolicyUpdateRequest,
)
from backend.ai_search.service import AiSearchService


router = APIRouter()
service = AiSearchService()


@router.get("/api/ai-search/sessions")
async def list_ai_search_sessions(
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.list_sessions(current_user.user_id)


@router.post("/api/ai-search/sessions")
async def create_ai_search_session(
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.create_session(current_user.user_id)


@router.post("/api/ai-search/sessions/from-analysis")
async def create_ai_search_session_from_analysis(
    request: AiSearchCreateFromAnalysisRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.create_session_from_analysis_seed(current_user.user_id, request.analysisTaskId)


@router.post("/api/ai-search/sessions/from-reply")
async def create_ai_search_session_from_reply(
    request: AiSearchCreateFromReplyRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.create_session_from_reply_seed(current_user.user_id, request.replyTaskId)


@router.get("/api/ai-search/sessions/{session_id}")
async def get_ai_search_session(
    session_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.get_snapshot(session_id, current_user.user_id)


@router.get("/api/ai-search/sessions/{session_id}/events/stream")
async def subscribe_ai_search_events(
    session_id: str,
    after_seq: int = 0,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return StreamingResponse(
        service.subscribe_stream(session_id, current_user.user_id, after_seq=after_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/api/ai-search/sessions/{session_id}/attachments/{attachment_id}/download")
async def download_ai_search_attachment(
    session_id: str,
    attachment_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.download_attachment(session_id, current_user.user_id, attachment_id)


@router.patch("/api/ai-search/sessions/{session_id}")
async def update_ai_search_session(
    session_id: str,
    request: AiSearchSessionUpdateRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.update_session(
        session_id,
        current_user.user_id,
        title=request.title,
        pinned=request.pinned,
    )


@router.patch("/api/ai-search/sessions/{session_id}/stop-policy")
async def update_ai_search_stop_policy(
    session_id: str,
    request: AiSearchStopPolicyUpdateRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    policy = {
        "max_rounds": request.maxRounds,
        "max_queries": request.maxQueries,
        "max_candidates": request.maxCandidates,
        "max_selected_documents": request.maxSelectedDocuments,
        "max_no_new_result_rounds": request.maxNoNewResultRounds,
        "deadline_seconds": request.deadlineSeconds,
        "target_coverage": request.targetCoverage,
        "stop_when": request.stopWhen,
        "databases": request.databases,
    }
    return service.update_stop_policy(session_id, current_user.user_id, policy)


@router.delete("/api/ai-search/sessions/{session_id}")
async def delete_ai_search_session(
    session_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.delete_session(session_id, current_user.user_id)


@router.post("/api/ai-search/sessions/{session_id}/messages/stream")
async def stream_ai_search_messages(
    session_id: str,
    request: AiSearchMessageRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return StreamingResponse(
        service.stream_message(session_id, current_user.user_id, request.content),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/api/ai-search/sessions/{session_id}/analysis-seed/stream")
async def stream_ai_search_analysis_seed(
    session_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return StreamingResponse(
        service.stream_analysis_seed(session_id, current_user.user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/api/ai-search/sessions/{session_id}/cancel")
async def cancel_ai_search_current_run(
    session_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.cancel_current_run(session_id, current_user.user_id)


@router.post("/api/ai-search/sessions/{session_id}/report/export")
async def export_ai_search_report(
    session_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.export_report(session_id, current_user.user_id)


@router.post("/api/ai-search/sessions/{session_id}/documents/supplement")
async def supplement_ai_search_documents(
    session_id: str,
    patentNumbers: str = Form(""),
    reviewGoal: str = Form(""),
    files: Optional[List[UploadFile]] = File(None),
    current_user: CurrentUser = Depends(_get_current_user),
):
    return await service.supplement_documents(
        session_id,
        current_user.user_id,
        patent_numbers=patentNumbers,
        files=files,
        review_goal=reviewGoal,
    )


@router.post("/api/ai-search/sessions/{session_id}/documents/selection/stream")
async def stream_ai_search_document_selection(
    session_id: str,
    request: AiSearchDocumentSelectionRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return StreamingResponse(
        service.stream_document_selection(
            session_id,
            current_user.user_id,
            request.planVersion,
            request.reviewDocumentIds,
            request.removeDocumentIds,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
