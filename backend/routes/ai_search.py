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
    AiSearchDocumentReviewRequest,
    AiSearchExecutionQueueMessageRequest,
    AiSearchFeatureComparisonRequest,
    AiSearchMessageRequest,
    AiSearchPlanConfirmRequest,
    AiSearchSessionUpdateRequest,
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


@router.post("/api/ai-search/sessions/{session_id}/execution-message-queue")
async def append_ai_search_execution_message_queue(
    session_id: str,
    request: AiSearchExecutionQueueMessageRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.append_execution_queue_message(session_id, current_user.user_id, request.content)


@router.delete("/api/ai-search/sessions/{session_id}/execution-message-queue/{queue_message_id}")
async def delete_ai_search_execution_message_queue(
    session_id: str,
    queue_message_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return service.delete_execution_queue_message(session_id, current_user.user_id, queue_message_id)


@router.post("/api/ai-search/sessions/{session_id}/resume/stream")
async def stream_ai_search_resume(
    session_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return StreamingResponse(
        service.stream_resume(session_id, current_user.user_id),
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


@router.post("/api/ai-search/sessions/{session_id}/document-review/stream")
async def stream_ai_search_document_review(
    session_id: str,
    request: AiSearchDocumentReviewRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return StreamingResponse(
        service.stream_document_review(
            session_id,
            current_user.user_id,
            request.planVersion,
            request.reviewDocumentIds,
            request.removeDocumentIds,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )

@router.post("/api/ai-search/sessions/{session_id}/feature-comparison/stream")
async def stream_ai_search_feature_comparison(
    session_id: str,
    request: AiSearchFeatureComparisonRequest,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return StreamingResponse(
        service.stream_feature_comparison(session_id, current_user.user_id, request.planVersion),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/api/ai-search/sessions/{session_id}/decision/continue")
async def stream_ai_search_decision_continue(
    session_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return StreamingResponse(
        service.stream_decision_continue(session_id, current_user.user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/api/ai-search/sessions/{session_id}/decision/complete")
async def stream_ai_search_decision_complete(
    session_id: str,
    current_user: CurrentUser = Depends(_get_current_user),
):
    return StreamingResponse(
        service.stream_decision_complete(session_id, current_user.user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
