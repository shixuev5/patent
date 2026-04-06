"""
AI search routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from backend.auth import _get_current_user
from backend.models import CurrentUser

from backend.ai_search.models import (
    AiSearchAnswerRequest,
    AiSearchCreateFromAnalysisRequest,
    AiSearchFeatureTableRequest,
    AiSearchMessageRequest,
    AiSearchPlanConfirmRequest,
    AiSearchSessionUpdateRequest,
    AiSearchSelectedDocumentsPatchRequest,
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
    return service.create_session_from_analysis(current_user.user_id, request.analysisTaskId)


@router.get("/api/ai-search/sessions/{session_id}")
async def get_ai_search_session(
    session_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.get_snapshot(session_id, current_user.user_id)


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


@router.post("/api/ai-search/sessions/{session_id}/answers/stream")
async def stream_ai_search_answers(
    session_id: str,
    request: AiSearchAnswerRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return StreamingResponse(
        service.stream_answer(session_id, current_user.user_id, request.questionId, request.answer),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/api/ai-search/sessions/{session_id}/plan/confirm/stream")
async def stream_ai_search_plan_confirmation(
    session_id: str,
    request: AiSearchPlanConfirmRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return StreamingResponse(
        service.stream_plan_confirmation(session_id, current_user.user_id, request.planVersion),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.patch("/api/ai-search/sessions/{session_id}/selected-documents")
async def patch_ai_search_selected_documents(
    session_id: str,
    request: AiSearchSelectedDocumentsPatchRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.patch_selected_documents(
        session_id,
        current_user.user_id,
        request.planVersion,
        request.addDocumentIds,
        request.removeDocumentIds,
    )


@router.post("/api/ai-search/sessions/{session_id}/feature-table/stream")
async def stream_ai_search_feature_table(
    session_id: str,
    request: AiSearchFeatureTableRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return StreamingResponse(
        service.stream_feature_table(session_id, current_user.user_id, request.planVersion),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
