"""
AI search session service.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Sequence

from fastapi import HTTPException
from langgraph.types import Command

from agents.ai_search.main import (
    build_close_reader_agent,
    build_coarse_screener_agent,
    build_feature_comparer_agent,
    build_planning_agent,
    extract_latest_ai_message,
    extract_structured_response,
)
from agents.ai_search.src.state import (
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_COMPLETED,
    PHASE_DRAFTING_PLAN,
    PHASE_RESULTS_READY,
    PHASE_SEARCHING,
    build_plan_summary,
    default_ai_search_meta,
    get_ai_search_meta,
    latest_search_elements,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from agents.common.retrieval.local_evidence_retriever import LocalEvidenceRetriever
from agents.common.search_clients.factory import SearchClientFactory
from backend.storage import TaskType, get_pipeline_manager
from backend.time_utils import utc_now_z
from backend.usage import _enforce_daily_quota

from .models import (
    AI_SEARCH_SESSION_NOT_FOUND_CODE,
    INVALID_SESSION_PHASE_CODE,
    PENDING_QUESTION_EXISTS_CODE,
    PLAN_CONFIRMATION_REQUIRED_CODE,
    SEARCH_IN_PROGRESS_CODE,
    STALE_PLAN_CONFIRMATION_CODE,
    AiSearchCreateSessionResponse,
    AiSearchSessionListResponse,
    AiSearchSessionSummary,
    AiSearchSnapshotResponse,
)
from backend.storage.ai_search_support import stable_ai_search_document_id


task_manager = get_pipeline_manager()

PLANNING_CHECKPOINT_NS = "ai_search_planning"
DEFAULT_MESSAGE_PHASES = {
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_DRAFTING_PLAN,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_RESULTS_READY,
    PHASE_COMPLETED,
}
DEFAULT_QUERY_BATCH_LIMIT = 8
DEFAULT_BATCH_RESULT_LIMIT = 50
DEFAULT_CANDIDATE_LIMIT = 120
DEFAULT_COARSE_CHUNK_SIZE = 20
DEFAULT_SHORTLIST_LIMIT = 20
DEFAULT_SELECTED_LIMIT = 10
DEFAULT_KEY_PASSAGES_LIMIT = 6
DEFAULT_PASSAGE_PREVIEW_CHARS = 400


class AiSearchService:
    def __init__(self):
        self.storage = task_manager.storage

    def _raise_session_not_found(self) -> None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": AI_SEARCH_SESSION_NOT_FOUND_CODE,
                "message": "AI 检索会话不存在。",
            },
        )

    def _raise_invalid_phase(self, phase: str, message: str) -> None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": INVALID_SESSION_PHASE_CODE,
                "message": message,
                "phase": phase,
            },
        )

    def _get_owned_session_task(self, session_id: str, owner_id: str) -> Any:
        task = self.storage.get_task(session_id)
        if not task or str(task.owner_id or "") != str(owner_id or "") or str(task.task_type or "") != TaskType.AI_SEARCH.value:
            self._raise_session_not_found()
        return task

    def _session_summary(self, task: Any) -> AiSearchSessionSummary:
        meta = get_ai_search_meta(task)
        return AiSearchSessionSummary(
            sessionId=task.id,
            taskId=task.id,
            title=str(task.title or "未命名 AI 检索会话"),
            status=task.status.value,
            phase=str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS),
            activePlanVersion=meta.get("active_plan_version"),
            selectedDocumentCount=int(meta.get("selected_document_count") or 0),
            createdAt=utc_now_z() if not getattr(task, "created_at", None) else task.created_at.isoformat(),
            updatedAt=utc_now_z() if not getattr(task, "updated_at", None) else task.updated_at.isoformat(),
        )

    def _display_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        visible_kinds = {"chat", "question", "answer"}
        return [
            item
            for item in messages
            if str(item.get("kind") or "") in visible_kinds
        ]

    def _current_plan(self, task: Any) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        active_plan_version = meta.get("active_plan_version")
        if active_plan_version:
            plan = self.storage.get_ai_search_plan(task.id, int(active_plan_version))
            if plan:
                return plan
        return self.storage.get_ai_search_plan(task.id)

    def _pending_question(self, task: Any, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        question_id = str(meta.get("pending_question_id") or "").strip()
        if not question_id:
            return None
        for item in reversed(messages):
            if str(item.get("question_id") or "") == question_id:
                metadata = item.get("metadata")
                return metadata if isinstance(metadata, dict) else None
        return None

    def _pending_confirmation(self, task: Any, current_plan: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        pending_plan_version = int(meta.get("pending_confirmation_plan_version") or 0)
        if pending_plan_version <= 0:
            return None
        if current_plan and int(current_plan.get("plan_version") or 0) == pending_plan_version:
            summary = build_plan_summary(current_plan)
            return {
                "planVersion": pending_plan_version,
                "planSummary": summary,
                "confirmationLabel": "确认检索计划",
            }
        return None

    def _documents_for_snapshot(self, task: Any) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 0)
        if plan_version <= 0:
            return [], []
        documents = self.storage.list_ai_search_documents(task.id, plan_version)
        selected = [item for item in documents if str(item.get("stage") or "") == "selected"]
        candidate = [item for item in documents if str(item.get("stage") or "") != "selected"]
        return candidate, selected

    def get_snapshot(self, session_id: str, owner_id: str) -> AiSearchSnapshotResponse:
        task = self._get_owned_session_task(session_id, owner_id)
        messages = self.storage.list_ai_search_messages(task.id)
        current_plan = self._current_plan(task)
        search_elements = latest_search_elements(messages)
        candidate_documents, selected_documents = self._documents_for_snapshot(task)
        feature_table = None
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if active_plan_version > 0:
            feature_table = self.storage.get_ai_search_feature_table(
                task.id,
                active_plan_version,
                feature_table_id=str(meta.get("current_feature_table_id") or "").strip() or None,
            )
        return AiSearchSnapshotResponse(
            session=self._session_summary(task),
            phase=str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS),
            messages=self._display_messages(messages),
            searchElements=search_elements,
            currentPlan=current_plan,
            candidateDocuments=candidate_documents,
            selectedDocuments=selected_documents,
            featureTable=feature_table,
            pendingQuestion=self._pending_question(task, messages),
            pendingConfirmation=self._pending_confirmation(task, current_plan),
        )

    def _update_phase(self, task_id: str, phase: str, **meta_updates: Any) -> None:
        task = self.storage.get_task(task_id)
        metadata = merge_ai_search_meta(task, current_phase=phase, **meta_updates)
        self.storage.update_task(
            task_id,
            metadata=metadata,
            status=phase_to_task_status(phase),
            progress=phase_progress(phase),
            current_step=phase_step(phase),
        )

    def _append_message(
        self,
        task_id: str,
        role: str,
        kind: str,
        content: str,
        *,
        plan_version: Optional[int] = None,
        question_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": task_id,
                "plan_version": plan_version,
                "role": role,
                "kind": kind,
                "content": content,
                "stream_status": "completed",
                "question_id": question_id,
                "metadata": metadata or {},
            }
        )

    def _latest_assistant_chat(self, task_id: str) -> str:
        messages = self.storage.list_ai_search_messages(task_id)
        for item in reversed(messages):
            if str(item.get("role") or "") == "assistant" and str(item.get("kind") or "") == "chat":
                return str(item.get("content") or "").strip()
        return ""

    def create_session(self, owner_id: str) -> AiSearchCreateSessionResponse:
        _enforce_daily_quota(owner_id, task_type=TaskType.AI_SEARCH.value)
        task = task_manager.create_task(
            owner_id=owner_id,
            task_type=TaskType.AI_SEARCH.value,
            title=None,
        )
        thread_id = f"ai-search-{task.id}"
        self.storage.update_task(
            task.id,
            title=f"AI 检索会话 - {task.id}",
            metadata=merge_ai_search_meta(task, **default_ai_search_meta(thread_id)),
            status=phase_to_task_status(PHASE_COLLECTING_REQUIREMENTS),
            progress=phase_progress(PHASE_COLLECTING_REQUIREMENTS),
            current_step=phase_step(PHASE_COLLECTING_REQUIREMENTS),
        )
        self._append_message(
            task.id,
            "assistant",
            "chat",
            "请描述检索目标、核心技术方案、关注特征，并尽量提供申请人、申请日或优先权日等约束条件。",
        )
        return AiSearchCreateSessionResponse(sessionId=task.id, taskId=task.id, threadId=thread_id)

    def list_sessions(self, owner_id: str) -> AiSearchSessionListResponse:
        tasks = [
            task
            for task in task_manager.list_tasks(owner_id=owner_id, limit=200)
            if str(task.task_type or "") == TaskType.AI_SEARCH.value
        ]
        return AiSearchSessionListResponse(items=[self._session_summary(task) for task in tasks], total=len(tasks))

    def _planning_config(self, thread_id: str) -> Dict[str, Any]:
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": PLANNING_CHECKPOINT_NS,
            }
        }

    def _run_planning_agent(self, task_id: str, thread_id: str, payload: Any) -> Dict[str, Any]:
        agent = build_planning_agent(self.storage, task_id)
        config = self._planning_config(thread_id)
        interrupted = False
        for chunk in agent.stream(payload, config):
            if "__interrupt__" in chunk:
                interrupted = True
        state = agent.get_state(config)
        values = state.values if state else {}
        return {"interrupted": interrupted, "values": values}

    async def _emit_snapshot_events(
        self,
        snapshot: AiSearchSnapshotResponse,
    ) -> AsyncIterator[str]:
        if snapshot.searchElements is not None:
            yield self._format_event("search_elements.updated", snapshot.session.sessionId, snapshot.phase, snapshot.searchElements)
        if snapshot.currentPlan is not None:
            yield self._format_event("plan.updated", snapshot.session.sessionId, snapshot.phase, snapshot.currentPlan)
        if snapshot.pendingQuestion is not None:
            yield self._format_event("question.required", snapshot.session.sessionId, snapshot.phase, snapshot.pendingQuestion)
        if snapshot.pendingConfirmation is not None:
            yield self._format_event("plan.awaiting_confirmation", snapshot.session.sessionId, snapshot.phase, snapshot.pendingConfirmation)
        if snapshot.candidateDocuments:
            yield self._format_event(
                "documents.updated",
                snapshot.session.sessionId,
                snapshot.phase,
                {"count": len(snapshot.candidateDocuments), "items": snapshot.candidateDocuments},
            )
        if snapshot.selectedDocuments:
            yield self._format_event(
                "selection.updated",
                snapshot.session.sessionId,
                snapshot.phase,
                {"count": len(snapshot.selectedDocuments), "items": snapshot.selectedDocuments},
            )
        if snapshot.featureTable is not None:
            yield self._format_event("feature_table.updated", snapshot.session.sessionId, snapshot.phase, snapshot.featureTable)

    def _format_event(self, event_type: str, session_id: str, phase: str, payload: Any) -> str:
        message = {
            "type": event_type,
            "sessionId": session_id,
            "taskId": session_id,
            "phase": phase,
            "payload": payload,
        }
        return f"data: {json.dumps(message, ensure_ascii=False)}\n\n"

    async def stream_message(self, session_id: str, owner_id: str, content: str) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS)
        if phase == PHASE_SEARCHING:
            raise HTTPException(
                status_code=409,
                detail={"code": SEARCH_IN_PROGRESS_CODE, "message": "检索执行中，暂不支持发送新消息。"},
            )
        if phase == PHASE_AWAITING_USER_ANSWER and meta.get("pending_question_id"):
            raise HTTPException(
                status_code=409,
                detail={"code": PENDING_QUESTION_EXISTS_CODE, "message": "请先回答当前追问。"},
            )
        if phase not in DEFAULT_MESSAGE_PHASES:
            self._raise_invalid_phase(phase, "当前阶段不允许发送普通消息。")

        if phase == PHASE_AWAITING_PLAN_CONFIRMATION and meta.get("active_plan_version"):
            active_plan_version = int(meta["active_plan_version"])
            self.storage.update_ai_search_plan(task.id, active_plan_version, status="superseded", superseded_at=utc_now_z())
            self._update_phase(task.id, PHASE_DRAFTING_PLAN, pending_confirmation_plan_version=None)

        self._append_message(task.id, "user", "chat", content)
        self._update_phase(task.id, PHASE_DRAFTING_PLAN)
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        previous_assistant = self._latest_assistant_chat(task.id)
        result = await asyncio.to_thread(
            self._run_planning_agent,
            task.id,
            thread_id,
            {"messages": [{"role": "user", "content": content}]},
        )
        assistant_text = extract_latest_ai_message(result["values"])
        active_plan_version = int(get_ai_search_meta(self.storage.get_task(task.id)).get("active_plan_version") or 0)
        if assistant_text and assistant_text != previous_assistant:
            self._append_message(task.id, "assistant", "chat", assistant_text, plan_version=active_plan_version or None)
            yield self._format_event("message.completed", task.id, self.get_snapshot(task.id, owner_id).phase, {"content": assistant_text})
        snapshot = self.get_snapshot(task.id, owner_id)
        async for event in self._emit_snapshot_events(snapshot):
            yield event
        yield self._format_event("run.completed", task.id, snapshot.phase, {"interrupted": result["interrupted"]})

    async def stream_answer(self, session_id: str, owner_id: str, question_id: str, answer: str) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or "")
        pending_question_id = str(meta.get("pending_question_id") or "").strip()
        if phase != PHASE_AWAITING_USER_ANSWER or not pending_question_id:
            self._raise_invalid_phase(phase, "当前没有待回答的问题。")
        if pending_question_id != question_id:
            raise HTTPException(
                status_code=409,
                detail={"code": PENDING_QUESTION_EXISTS_CODE, "message": "回答的问题已过期。"},
            )
        self._append_message(task.id, "user", "answer", answer, question_id=question_id)
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        previous_assistant = self._latest_assistant_chat(task.id)
        result = await asyncio.to_thread(
            self._run_planning_agent,
            task.id,
            thread_id,
            Command(resume=answer),
        )
        assistant_text = extract_latest_ai_message(result["values"])
        active_plan_version = int(get_ai_search_meta(self.storage.get_task(task.id)).get("active_plan_version") or 0)
        if assistant_text and assistant_text != previous_assistant:
            self._append_message(task.id, "assistant", "chat", assistant_text, plan_version=active_plan_version or None)
            yield self._format_event("message.completed", task.id, self.get_snapshot(task.id, owner_id).phase, {"content": assistant_text})
        snapshot = self.get_snapshot(task.id, owner_id)
        yield self._format_event("question.resolved", task.id, snapshot.phase, {"questionId": question_id, "answer": answer})
        async for event in self._emit_snapshot_events(snapshot):
            yield event
        yield self._format_event("run.completed", task.id, snapshot.phase, {"interrupted": result["interrupted"]})

    async def stream_plan_confirmation(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or "")
        if phase != PHASE_AWAITING_PLAN_CONFIRMATION:
            raise HTTPException(
                status_code=409,
                detail={"code": PLAN_CONFIRMATION_REQUIRED_CODE, "message": "当前没有待确认的检索计划。"},
            )
        pending_plan_version = int(meta.get("pending_confirmation_plan_version") or 0)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if pending_plan_version != plan_version or active_plan_version != plan_version:
            raise HTTPException(
                status_code=409,
                detail={"code": STALE_PLAN_CONFIRMATION_CODE, "message": "当前计划版本已失效，请刷新后重试。"},
            )
        thread_id = str(meta.get("thread_id") or f"ai-search-{task.id}")
        previous_assistant = self._latest_assistant_chat(task.id)
        planning_result = await asyncio.to_thread(
            self._run_planning_agent,
            task.id,
            thread_id,
            Command(resume={"confirmed": True, "plan_version": plan_version}),
        )
        assistant_text = extract_latest_ai_message(planning_result["values"])
        if assistant_text and assistant_text != previous_assistant:
            self._append_message(task.id, "assistant", "chat", assistant_text, plan_version=plan_version)
            yield self._format_event("message.completed", task.id, PHASE_SEARCHING, {"content": assistant_text})
        yield self._format_event("plan.confirmed", task.id, PHASE_SEARCHING, {"planVersion": plan_version})
        async for event in self._run_search_pipeline(task.id, owner_id, plan_version):
            yield event

    def _normalize_query_terms(self, values: Sequence[Any]) -> List[str]:
        outputs: List[str] = []
        for item in values or []:
            value = str(item or "").strip()
            if value and value not in outputs:
                outputs.append(value)
        return outputs

    def _normalize_date_text(self, value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        if re.fullmatch(r"\d{8}", text):
            return f"{text[:4]}-{text[4:6]}-{text[6:]}"
        return None

    def _compact_date_text(self, value: Any) -> str | None:
        normalized = self._normalize_date_text(value)
        if not normalized:
            return None
        return normalized.replace("-", "")

    def resolve_cutoff_date(self, search_elements: Dict[str, Any]) -> str | None:
        if not isinstance(search_elements, dict):
            return None
        return self._normalize_date_text(search_elements.get("priority_date")) or self._normalize_date_text(search_elements.get("filing_date"))

    def normalize_applicants(self, search_elements: Dict[str, Any]) -> List[str]:
        if not isinstance(search_elements, dict):
            return []
        values = search_elements.get("applicants") or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return []
        outputs: List[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in outputs:
                outputs.append(text)
        return outputs

    def build_search_constraints(self, search_elements: Dict[str, Any]) -> Dict[str, Any]:
        cutoff_date = self.resolve_cutoff_date(search_elements)
        return {
            "applicant_terms": self.normalize_applicants(search_elements),
            "filing_date": self._normalize_date_text(search_elements.get("filing_date") if isinstance(search_elements, dict) else None),
            "priority_date": self._normalize_date_text(search_elements.get("priority_date") if isinstance(search_elements, dict) else None),
            "effective_cutoff_date": cutoff_date,
            "cutoff_date_yyyymmdd": self._compact_date_text(cutoff_date),
        }

    def _escape_query_term(self, value: str) -> str:
        return str(value or "").replace("\\", " ").replace('"', " ").strip()

    def _build_constraint_clauses(self, search_elements: Dict[str, Any]) -> List[str]:
        constraints = self.build_search_constraints(search_elements)
        clauses: List[str] = []
        cutoff = str(constraints.get("cutoff_date_yyyymmdd") or "").strip()
        if cutoff:
            clauses.append(f"PBD:[* TO {cutoff}]")
        applicants = constraints.get("applicant_terms") or []
        applicant_clauses = [
            f'AN:("{self._escape_query_term(applicant)}")'
            for applicant in applicants[:4]
            if self._escape_query_term(applicant)
        ]
        if applicant_clauses:
            clauses.append(applicant_clauses[0] if len(applicant_clauses) == 1 else "(" + " OR ".join(applicant_clauses) + ")")
        return clauses

    def _build_query_text(self, batch: Dict[str, Any], search_elements: Optional[Dict[str, Any]] = None) -> str:
        must_terms = self._normalize_query_terms(batch.get("must_terms_zh") or []) + self._normalize_query_terms(batch.get("must_terms_en") or [])
        should_terms = self._normalize_query_terms(batch.get("should_terms_zh") or []) + self._normalize_query_terms(batch.get("should_terms_en") or [])
        negative_terms = self._normalize_query_terms(batch.get("negative_terms") or [])
        parts: List[str] = []
        if must_terms:
            parts.append(" AND ".join(f'"{item}"' for item in must_terms[:8]))
        if should_terms:
            parts.append("(" + " OR ".join(f'"{item}"' for item in should_terms[:8]) + ")")
        if negative_terms:
            parts.append(" ".join(f'NOT "{item}"' for item in negative_terms[:6]))
        query = " ".join(parts).strip() or str(batch.get("goal") or "").strip()
        constraint_clauses = self._build_constraint_clauses(search_elements or {})
        if query and constraint_clauses:
            return f"({query}) AND " + " AND ".join(constraint_clauses)
        if constraint_clauses:
            return " AND ".join(constraint_clauses)
        return query

    def _search_patents(self, batch: Dict[str, Any], search_elements: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        client = SearchClientFactory.get_client("zhihuiya")
        query_text = self._build_query_text(batch, search_elements)
        result_limit = int(batch.get("result_limit") or DEFAULT_BATCH_RESULT_LIMIT)
        if query_text:
            result = client.search(query_text, limit=min(result_limit, DEFAULT_BATCH_RESULT_LIMIT))
            if isinstance(result, dict):
                return result
        constraints = self.build_search_constraints(search_elements or {})
        semantic_parts = (
            self._normalize_query_terms(batch.get("must_terms_zh") or [])
            + self._normalize_query_terms(batch.get("should_terms_zh") or [])
            + [str(batch.get("goal") or "").strip()]
        )
        applicant_terms = constraints.get("applicant_terms") or []
        if applicant_terms:
            semantic_parts.append("相关申请人：" + "、".join(applicant_terms))
        if constraints.get("effective_cutoff_date"):
            semantic_parts.append(f"检索截止日：{constraints['effective_cutoff_date']}")
        semantic_text = " ".join(
            [part for part in semantic_parts if str(part or "").strip()]
        ).strip()
        return client.search_semantic(
            semantic_text,
            to_date=str(constraints.get("cutoff_date_yyyymmdd") or ""),
            limit=min(result_limit, DEFAULT_BATCH_RESULT_LIMIT),
        )

    def _build_candidate_documents(
        self,
        task_id: str,
        plan_version: int,
        search_results: Iterable[Dict[str, Any]],
        batch_id: str,
    ) -> List[Dict[str, Any]]:
        documents: List[Dict[str, Any]] = []
        for item in search_results:
            pn = str(item.get("pn") or "").strip().upper()
            if not pn:
                continue
            documents.append(
                {
                    "document_id": stable_ai_search_document_id(task_id, plan_version, pn),
                    "task_id": task_id,
                    "plan_version": plan_version,
                    "pn": pn,
                    "title": str(item.get("title") or "").strip(),
                    "abstract": str(item.get("abstract") or "").strip(),
                    "ipc_cpc_json": item.get("cpc") or [],
                    "source_batches_json": [batch_id],
                    "stage": "candidate",
                    "score": item.get("score"),
                    "agent_reason": "",
                    "key_passages_json": [],
                    "user_pinned": False,
                    "user_removed": False,
                }
            )
        return documents

    def _coarse_prompt(
        self,
        search_elements: Dict[str, Any],
        documents: List[Dict[str, Any]],
    ) -> str:
        constraints = self.build_search_constraints(search_elements)
        return (
            "根据检索要素对下面候选文献做粗筛，只能基于标题、摘要、分类号和来源批次判断。\n"
            f"检索边界:\n{json.dumps(constraints, ensure_ascii=False)}\n"
            f"检索要素:\n{json.dumps(search_elements, ensure_ascii=False)}\n"
            f"候选文献:\n{json.dumps(documents, ensure_ascii=False)}"
        )

    def _detail_to_text(self, detail: Dict[str, Any]) -> str:
        parts = [
            str(detail.get("title") or ""),
            str(detail.get("abstract") or ""),
            str(detail.get("claims") or ""),
            str(detail.get("description") or ""),
        ]
        return "\n\n".join(part.strip() for part in parts if str(part).strip())

    def _load_document_details(self, pn: str) -> Dict[str, Any]:
        client = SearchClientFactory.get_client("zhihuiya")
        detail = client.get_patent_details(pn)
        detail = detail if isinstance(detail, dict) else {}
        return {
            "pn": pn,
            "title": str(detail.get("title") or detail.get("basic_info", {}).get("title") or "").strip(),
            "abstract": str(detail.get("abstract") or detail.get("basic_info", {}).get("abstract") or "").strip(),
            "claims": str(detail.get("claims") or detail.get("claims_info") or "").strip(),
            "description": str(detail.get("description") or detail.get("description_info") or "").strip(),
            "raw": detail,
        }

    def _fallback_passages(self, text: str, terms: List[str]) -> List[Dict[str, Any]]:
        if not text:
            return []
        paragraphs = [item.strip() for item in re.split(r"\n{2,}", text) if item.strip()]
        scored: List[tuple[int, str]] = []
        for paragraph in paragraphs:
            lowered = paragraph.lower()
            score = sum(1 for term in terms if term.lower() in lowered)
            if score > 0:
                scored.append((score, paragraph))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "document_id": "",
                "passage": paragraph[:DEFAULT_PASSAGE_PREVIEW_CHARS],
                "reason": "关键词命中",
                "location": f"paragraph_{index + 1}",
            }
            for index, (_, paragraph) in enumerate(scored[:DEFAULT_KEY_PASSAGES_LIMIT])
        ]

    def _collect_key_terms(self, search_elements: Dict[str, Any]) -> List[str]:
        terms: List[str] = []
        for element in search_elements.get("search_elements") or []:
            if not isinstance(element, dict):
                continue
            for key in ("keywords_zh", "keywords_en"):
                for value in element.get(key) or []:
                    text = str(value or "").strip()
                    if text and text not in terms:
                        terms.append(text)
        return terms[:24]

    def _build_local_evidence(self, task_id: str, plan_version: int, details: List[Dict[str, Any]], search_elements: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        terms = self._collect_key_terms(search_elements)
        documents = [
            {
                "doc_id": item["pn"],
                "title": item.get("title") or item["pn"],
                "content": self._detail_to_text(item),
                "source_type": "comparison_document",
            }
            for item in details
        ]
        db_path = Path(task_manager.storage.db_path if hasattr(task_manager.storage, "db_path") else Path.cwd())  # type: ignore[attr-defined]
        index_path = db_path.parent / f"ai_search_{task_id}_{plan_version}.sqlite"
        evidence_map: Dict[str, List[Dict[str, Any]]] = {}
        try:
            retriever = LocalEvidenceRetriever(str(index_path))
            retriever.build_index(documents)
            for item in details:
                candidates = retriever.search(" ".join(terms), intent="evidence", doc_filters=[item["pn"]], top_k=DEFAULT_KEY_PASSAGES_LIMIT)
                cards = retriever.build_evidence_cards(
                    candidates,
                    context_k=DEFAULT_KEY_PASSAGES_LIMIT,
                    max_context_chars=DEFAULT_PASSAGE_PREVIEW_CHARS * DEFAULT_KEY_PASSAGES_LIMIT,
                    max_quote_chars=DEFAULT_PASSAGE_PREVIEW_CHARS,
                )
                evidence_map[item["pn"]] = [
                    {
                        "document_id": item["pn"],
                        "passage": card.get("quote", ""),
                        "reason": card.get("analysis", ""),
                        "location": card.get("location"),
                    }
                    for card in cards.get("cards") or []
                ]
        except Exception:
            for item in details:
                passages = self._fallback_passages(self._detail_to_text(item), terms)
                for passage in passages:
                    passage["document_id"] = item["pn"]
                evidence_map[item["pn"]] = passages
        return evidence_map

    def _close_reader_prompt(
        self,
        search_elements: Dict[str, Any],
        documents: List[Dict[str, Any]],
        evidence_map: Dict[str, List[Dict[str, Any]]],
    ) -> str:
        constraints = self.build_search_constraints(search_elements)
        payload = []
        for item in documents:
            payload.append(
                {
                    "document_id": item["document_id"],
                    "pn": item["pn"],
                    "title": item["title"],
                    "abstract": item["abstract"],
                    "claims": item.get("claims", ""),
                    "description_excerpt": item.get("description", "")[:4000],
                    "evidence": evidence_map.get(item["pn"], []),
                }
            )
        return (
            "请根据检索要素与证据段落，对 shortlisted 文献进行精读，判断是否纳入对比文件。\n"
            f"检索边界:\n{json.dumps(constraints, ensure_ascii=False)}\n"
            f"检索要素:\n{json.dumps(search_elements, ensure_ascii=False)}\n"
            f"shortlist 文献:\n{json.dumps(payload, ensure_ascii=False)}"
        )

    def _feature_prompt(
        self,
        search_elements: Dict[str, Any],
        selected_documents: List[Dict[str, Any]],
    ) -> str:
        constraints = self.build_search_constraints(search_elements)
        payload = []
        for item in selected_documents:
            payload.append(
                {
                    "document_id": item["document_id"],
                    "pn": item["pn"],
                    "title": item["title"],
                    "abstract": item["abstract"],
                    "key_passages": item.get("key_passages_json") or [],
                }
            )
        return (
            "请基于检索要素和已选对比文件，输出特征对比表。\n"
            f"检索边界:\n{json.dumps(constraints, ensure_ascii=False)}\n"
            f"检索要素:\n{json.dumps(search_elements, ensure_ascii=False)}\n"
            f"已选对比文件:\n{json.dumps(payload, ensure_ascii=False)}"
        )

    async def _run_search_pipeline(self, task_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        task = self.storage.get_task(task_id)
        if not task:
            self._raise_session_not_found()
        plan = self.storage.get_ai_search_plan(task_id, plan_version)
        if not plan:
            self._raise_invalid_phase(PHASE_SEARCHING, "当前计划不存在。")
        plan_json = plan.get("plan_json") if isinstance(plan.get("plan_json"), dict) else {}
        search_elements = plan.get("search_elements_json") if isinstance(plan.get("search_elements_json"), dict) else {}
        query_batches = (plan_json.get("query_batches") or [])[:DEFAULT_QUERY_BATCH_LIMIT]
        self._update_phase(task_id, PHASE_SEARCHING, active_plan_version=plan_version)

        candidates_by_pn: Dict[str, Dict[str, Any]] = {}
        for batch in query_batches:
            batch_id = str(batch.get("batch_id") or uuid.uuid4().hex[:8]).strip()
            raw_result = await asyncio.to_thread(self._search_patents, batch, search_elements)
            documents = self._build_candidate_documents(task_id, plan_version, raw_result.get("results") or [], batch_id)
            for item in documents:
                pn = str(item.get("pn") or "").strip().upper()
                if not pn:
                    continue
                existing = candidates_by_pn.get(pn)
                if existing:
                    sources = list(existing.get("source_batches_json") or [])
                    if batch_id not in sources:
                        sources.append(batch_id)
                    existing["source_batches_json"] = sources
                    continue
                candidates_by_pn[pn] = item
                if len(candidates_by_pn) >= DEFAULT_CANDIDATE_LIMIT:
                    break
            if len(candidates_by_pn) >= DEFAULT_CANDIDATE_LIMIT:
                break

        candidate_records = list(candidates_by_pn.values())[:DEFAULT_CANDIDATE_LIMIT]
        self.storage.upsert_ai_search_documents(candidate_records)
        yield self._format_event(
            "documents.updated",
            task_id,
            PHASE_SEARCHING,
            {"count": len(candidate_records), "items": candidate_records},
        )

        if not candidate_records:
            self._append_message(task_id, "assistant", "chat", "当前计划未检索到候选文献。", plan_version=plan_version)
            self._update_phase(task_id, PHASE_RESULTS_READY, selected_document_count=0)
            snapshot = self.get_snapshot(task_id, owner_id)
            yield self._format_event("run.completed", task_id, snapshot.phase, {"selectedCount": 0})
            return

        yield self._format_event("subagent.started", task_id, PHASE_SEARCHING, {"name": "coarse-screener"})
        coarse_agent = build_coarse_screener_agent()
        shortlisted_ids: List[str] = []
        rejected_ids: List[str] = []
        for start in range(0, len(candidate_records), DEFAULT_COARSE_CHUNK_SIZE):
            chunk = candidate_records[start : start + DEFAULT_COARSE_CHUNK_SIZE]
            result = await asyncio.to_thread(
                coarse_agent.invoke,
                {"messages": [{"role": "user", "content": self._coarse_prompt(search_elements, chunk)}]},
            )
            structured = extract_structured_response(result)
            shortlisted_ids.extend([item for item in structured.get("keep") or [] if item not in shortlisted_ids])
            rejected_ids.extend([item for item in structured.get("discard") or [] if item not in rejected_ids])
            if len(shortlisted_ids) >= DEFAULT_SHORTLIST_LIMIT:
                shortlisted_ids = shortlisted_ids[:DEFAULT_SHORTLIST_LIMIT]
                break

        shortlisted_ids = shortlisted_ids[:DEFAULT_SHORTLIST_LIMIT]
        shortlist_records = [item for item in candidate_records if item["document_id"] in shortlisted_ids]
        shortlist_id_set = {item["document_id"] for item in shortlist_records}
        for item in candidate_records:
            if item["document_id"] in shortlist_id_set:
                self.storage.update_ai_search_document(task_id, plan_version, item["document_id"], stage="shortlisted")
            elif item["document_id"] in rejected_ids:
                self.storage.update_ai_search_document(task_id, plan_version, item["document_id"], stage="rejected")
        yield self._format_event(
            "subagent.completed",
            task_id,
            PHASE_SEARCHING,
            {"name": "coarse-screener", "shortlistCount": len(shortlist_records)},
        )

        if not shortlist_records:
            self._append_message(task_id, "assistant", "chat", "候选文献已完成粗筛，但没有形成 shortlist。", plan_version=plan_version)
            self._update_phase(task_id, PHASE_RESULTS_READY, selected_document_count=0)
            snapshot = self.get_snapshot(task_id, owner_id)
            async for event in self._emit_snapshot_events(snapshot):
                yield event
            yield self._format_event("run.completed", task_id, snapshot.phase, {"selectedCount": 0})
            return

        detail_records = []
        for item in shortlist_records:
            detail = await asyncio.to_thread(self._load_document_details, item["pn"])
            detail_records.append(
                {
                    **item,
                    **detail,
                }
            )
        evidence_map = self._build_local_evidence(task_id, plan_version, detail_records, search_elements)

        yield self._format_event("subagent.started", task_id, PHASE_SEARCHING, {"name": "close-reader"})
        close_agent = build_close_reader_agent()
        close_result = await asyncio.to_thread(
            close_agent.invoke,
            {"messages": [{"role": "user", "content": self._close_reader_prompt(search_elements, detail_records, evidence_map)}]},
        )
        close_structured = extract_structured_response(close_result)
        selected_ids = [item for item in close_structured.get("selected") or []][:DEFAULT_SELECTED_LIMIT]
        rejected_detail_ids = [item for item in close_structured.get("rejected") or []]
        passages_by_doc: Dict[str, List[Dict[str, Any]]] = {}
        for item in close_structured.get("key_passages") or []:
            document_id = str(item.get("document_id") or "").strip()
            if not document_id:
                continue
            passages_by_doc.setdefault(document_id, []).append(
                {
                    "passage": str(item.get("passage") or "")[:DEFAULT_PASSAGE_PREVIEW_CHARS],
                    "reason": str(item.get("reason") or "").strip(),
                    "location": item.get("location"),
                }
            )
        for item in detail_records:
            document_id = item["document_id"]
            if document_id in selected_ids:
                self.storage.update_ai_search_document(
                    task_id,
                    plan_version,
                    document_id,
                    stage="selected",
                    key_passages_json=passages_by_doc.get(document_id) or evidence_map.get(item["pn"], [])[:DEFAULT_KEY_PASSAGES_LIMIT],
                    agent_reason="纳入对比文件",
                )
            elif document_id in rejected_detail_ids:
                self.storage.update_ai_search_document(
                    task_id,
                    plan_version,
                    document_id,
                    stage="rejected",
                    key_passages_json=passages_by_doc.get(document_id) or [],
                    agent_reason="精读后排除",
                )
        yield self._format_event(
            "subagent.completed",
            task_id,
            PHASE_SEARCHING,
            {"name": "close-reader", "selectedCount": len(selected_ids)},
        )

        self._append_message(
            task_id,
            "assistant",
            "chat",
            f"已完成检索、粗筛和精读，推荐 {len(selected_ids)} 篇对比文件。",
            plan_version=plan_version,
        )
        self._update_phase(
            task_id,
            PHASE_RESULTS_READY,
            active_plan_version=plan_version,
            selected_document_count=len(selected_ids),
            current_feature_table_id=None,
        )
        snapshot = self.get_snapshot(task_id, owner_id)
        async for event in self._emit_snapshot_events(snapshot):
            yield event
        yield self._format_event("run.completed", task_id, snapshot.phase, {"selectedCount": len(selected_ids)})

    def patch_selected_documents(
        self,
        session_id: str,
        owner_id: str,
        plan_version: int,
        add_document_ids: Optional[List[str]],
        remove_document_ids: Optional[List[str]],
    ) -> AiSearchSnapshotResponse:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if active_plan_version != int(plan_version):
            raise HTTPException(
                status_code=409,
                detail={"code": STALE_PLAN_CONFIRMATION_CODE, "message": "当前只允许操作活动计划版本。"},
            )
        phase = str(meta.get("current_phase") or "")
        if phase not in {PHASE_RESULTS_READY, PHASE_COMPLETED}:
            self._raise_invalid_phase(phase, "当前阶段不允许调整对比文件。")
        add_ids = [str(item).strip() for item in (add_document_ids or []) if str(item).strip()]
        remove_ids = [str(item).strip() for item in (remove_document_ids or []) if str(item).strip()]
        for document_id in add_ids:
            self.storage.update_ai_search_document(
                task.id,
                plan_version,
                document_id,
                stage="selected",
                user_pinned=True,
                user_removed=False,
                agent_reason="用户手动加入对比文件",
            )
        for document_id in remove_ids:
            self.storage.update_ai_search_document(
                task.id,
                plan_version,
                document_id,
                stage="rejected",
                user_pinned=False,
                user_removed=True,
                agent_reason="用户手动移出对比文件",
            )
        selected_count = len(self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"]))
        self._update_phase(
            task.id,
            PHASE_RESULTS_READY,
            selected_document_count=selected_count,
            current_feature_table_id=None,
        )
        return self.get_snapshot(task.id, owner_id)

    async def stream_feature_table(self, session_id: str, owner_id: str, plan_version: int) -> AsyncIterator[str]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        active_plan_version = int(meta.get("active_plan_version") or 0)
        if active_plan_version != int(plan_version):
            raise HTTPException(
                status_code=409,
                detail={"code": STALE_PLAN_CONFIRMATION_CODE, "message": "当前只允许生成活动计划版本的特征对比表。"},
            )
        selected_documents = self.storage.list_ai_search_documents(task.id, plan_version, stages=["selected"])
        if not selected_documents:
            self._raise_invalid_phase(PHASE_RESULTS_READY, "当前没有已选对比文件。")
        plan = self.storage.get_ai_search_plan(task.id, plan_version) or {}
        search_elements = plan.get("search_elements_json") if isinstance(plan.get("search_elements_json"), dict) else {}
        yield self._format_event("subagent.started", task.id, PHASE_RESULTS_READY, {"name": "feature-comparer"})
        feature_agent = build_feature_comparer_agent()
        result = await asyncio.to_thread(
            feature_agent.invoke,
            {"messages": [{"role": "user", "content": self._feature_prompt(search_elements, selected_documents)}]},
        )
        structured = extract_structured_response(result)
        feature_table_id = uuid.uuid4().hex
        self.storage.create_ai_search_feature_table(
            {
                "feature_table_id": feature_table_id,
                "task_id": task.id,
                "plan_version": plan_version,
                "status": "completed",
                "table_json": structured.get("table_rows") or [],
                "summary_markdown": structured.get("summary_markdown") or "",
            }
        )
        self._append_message(
            task.id,
            "assistant",
            "chat",
            str(structured.get("overall_findings") or "特征对比表已生成。"),
            plan_version=plan_version,
        )
        self._update_phase(
            task.id,
            PHASE_COMPLETED,
            current_feature_table_id=feature_table_id,
            selected_document_count=len(selected_documents),
        )
        yield self._format_event("subagent.completed", task.id, PHASE_COMPLETED, {"name": "feature-comparer"})
        snapshot = self.get_snapshot(task.id, owner_id)
        async for event in self._emit_snapshot_events(snapshot):
            yield event
        yield self._format_event("run.completed", task.id, snapshot.phase, {"featureTableId": feature_table_id})
