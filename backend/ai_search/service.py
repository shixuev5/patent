"""
AI search session service.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import HTTPException
from langgraph.types import Command

from agents.ai_search.main import (
    build_feature_comparer_agent,
    build_main_agent,
    extract_latest_ai_message,
    extract_structured_response,
)
from agents.ai_search.src.screening import build_feature_prompt
from agents.ai_search.src.subagents.search_elements import normalize_search_elements_payload
from agents.ai_search.src.state import (
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_COMPLETED,
    PHASE_DRAFTING_PLAN,
    PHASE_FAILED,
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
from backend.system_logs import emit_system_log
from backend.storage import TaskType, get_pipeline_manager
from backend.time_utils import utc_now_z
from backend.usage import _enforce_daily_quota
from backend.utils import _build_r2_storage

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


task_manager = get_pipeline_manager()

MAIN_AGENT_CHECKPOINT_NS = "ai_search_main"
DEFAULT_MESSAGE_PHASES = {
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_DRAFTING_PLAN,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_RESULTS_READY,
    PHASE_COMPLETED,
}
DATE_PART_RE = re.compile(r"\d+")


def _load_json_file(path_value: Any) -> Optional[Dict[str, Any]]:
    path_text = str(path_value or "").strip()
    if not path_text:
        return None
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _load_json_bytes(raw: Optional[bytes]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _normalize_analysis_date(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    parts = DATE_PART_RE.findall(text)
    if len(parts) < 3:
        return None
    year, month, day = parts[:3]
    if len(year) != 4:
        return None
    try:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except ValueError:
        return None


def _string_list(values: Any) -> List[str]:
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


def _applicant_names(biblio: Dict[str, Any]) -> List[str]:
    applicants = biblio.get("applicants")
    if isinstance(applicants, str):
        applicants = [applicants]
    if not isinstance(applicants, list):
        return []
    outputs: List[str] = []
    for item in applicants:
        if isinstance(item, dict):
            text = str(item.get("name") or "").strip()
        else:
            text = str(item or "").strip()
        if text and text not in outputs:
            outputs.append(text)
    return outputs


def _seed_search_elements_from_analysis(analysis_payload: Dict[str, Any], patent_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    patent_data = patent_payload if isinstance(patent_payload, dict) else {}
    biblio = patent_data.get("bibliographic_data") if isinstance(patent_data.get("bibliographic_data"), dict) else {}
    search_strategy = analysis_payload.get("search_strategy") if isinstance(analysis_payload.get("search_strategy"), dict) else {}
    search_matrix = search_strategy.get("search_matrix") if isinstance(search_strategy.get("search_matrix"), list) else []
    report_core = analysis_payload.get("report_core") if isinstance(analysis_payload.get("report_core"), dict) else {}
    metadata = analysis_payload.get("metadata") if isinstance(analysis_payload.get("metadata"), dict) else {}

    invention_title = str(
        report_core.get("ai_title")
        or biblio.get("invention_title")
        or metadata.get("resolved_pn")
        or "当前专利"
    ).strip()
    resolved_pn = str(metadata.get("resolved_pn") or biblio.get("publication_number") or "").strip()
    objective = (
        f"围绕专利 {resolved_pn}《{invention_title}》检索可能构成对比文件的现有技术。"
        if resolved_pn
        else f"围绕《{invention_title}》检索可能构成对比文件的现有技术。"
    )

    mapped_elements: List[Dict[str, Any]] = []
    for item in search_matrix:
        if not isinstance(item, dict):
            continue
        element_name = str(item.get("element_name") or "").strip()
        if not element_name:
            continue
        notes_parts = _string_list(item.get("notes"))
        if not notes_parts and str(item.get("notes") or "").strip():
            notes_parts = [str(item.get("notes") or "").strip()]
        if str(item.get("block_id") or "").strip():
            notes_parts.append(f"block={str(item.get('block_id') or '').strip()}")
        if str(item.get("element_role") or "").strip():
            notes_parts.append(f"role={str(item.get('element_role') or '').strip()}")
        if str(item.get("priority_tier") or "").strip():
            notes_parts.append(f"priority={str(item.get('priority_tier') or '').strip()}")
        effect_cluster_ids = _string_list(item.get("effect_cluster_ids"))
        if effect_cluster_ids:
            notes_parts.append(f"effects={','.join(effect_cluster_ids)}")
        mapped_elements.append(
            {
                "element_name": element_name,
                "keywords_zh": _string_list(item.get("keywords_zh")),
                "keywords_en": _string_list(item.get("keywords_en")),
                "block_id": str(item.get("block_id") or "").strip(),
                "element_role": str(item.get("element_role") or "").strip(),
                "priority_tier": str(item.get("priority_tier") or "").strip(),
                "effect_cluster_ids": effect_cluster_ids,
                "notes": "；".join(part for part in notes_parts if part),
            }
        )

    return normalize_search_elements_payload(
        {
            "status": "complete" if objective and mapped_elements else "needs_answer",
            "objective": objective,
            "applicants": _applicant_names(biblio),
            "filing_date": _normalize_analysis_date(
                biblio.get("application_date") or biblio.get("filing_date")
            ),
            "priority_date": _normalize_analysis_date(biblio.get("priority_date")),
            "search_elements": mapped_elements,
            "missing_items": [],
            "clarification_summary": "已从 AI 分析结果导入首轮检索要素。",
        }
    )


def _seed_prompt_from_analysis(
    analysis_payload: Dict[str, Any],
    patent_payload: Optional[Dict[str, Any]],
    seeded_search_elements: Dict[str, Any],
) -> str:
    patent_data = patent_payload if isinstance(patent_payload, dict) else {}
    biblio = patent_data.get("bibliographic_data") if isinstance(patent_data.get("bibliographic_data"), dict) else {}
    search_strategy = analysis_payload.get("search_strategy") if isinstance(analysis_payload.get("search_strategy"), dict) else {}
    semantic_strategy = search_strategy.get("semantic_strategy") if isinstance(search_strategy.get("semantic_strategy"), dict) else {}
    report_core = analysis_payload.get("report_core") if isinstance(analysis_payload.get("report_core"), dict) else {}
    metadata = analysis_payload.get("metadata") if isinstance(analysis_payload.get("metadata"), dict) else {}
    semantic_queries = semantic_strategy.get("queries") if isinstance(semantic_strategy.get("queries"), list) else []
    report = analysis_payload.get("report") if isinstance(analysis_payload.get("report"), dict) else {}

    seed_context = {
        "source": {
            "type": "analysis",
            "analysis_task_id": str(metadata.get("task_id") or "").strip(),
            "publication_number": str(metadata.get("resolved_pn") or biblio.get("publication_number") or "").strip(),
            "title": str(report_core.get("ai_title") or biblio.get("invention_title") or "").strip(),
        },
        "goal": "基于 AI 分析结果生成 AI 检索草稿。如果信息已足够，直接生成待确认检索计划；如果仍缺关键信息，只追问缺失项。不要开始真实检索执行。",
        "analysis_summary": {
            "technical_problem": str(report_core.get("technical_problem") or report.get("technical_problem") or "").strip(),
            "technical_means": str(report_core.get("technical_means") or report.get("technical_means") or "").strip(),
            "technical_effects": report_core.get("technical_effects") if isinstance(report_core.get("technical_effects"), list) else [],
        },
        "seeded_search_elements": seeded_search_elements,
        "semantic_queries": semantic_queries,
    }
    return (
        "请根据以下 AI 分析结果生成一份 AI 检索草稿。"
        "要求：先整理检索要素，再决定是直接产出待确认计划，还是仅追问缺失项；不要启动真实检索。\n\n"
        f"{json.dumps(seed_context, ensure_ascii=False, indent=2)}"
    )


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
            pinned=bool(meta.get("pinned")),
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

    def _source_summary(self, task: Any) -> Optional[Dict[str, Any]]:
        meta = get_ai_search_meta(task)
        source_type = str(meta.get("source_type") or "").strip()
        if source_type != "analysis":
            return None
        return {
            "sourceType": source_type,
            "sourceTaskId": str(meta.get("source_task_id") or "").strip(),
            "sourcePn": str(meta.get("source_pn") or "").strip(),
            "sourceTitle": str(meta.get("source_title") or "").strip(),
            "seedMode": str(meta.get("seed_mode") or "").strip(),
            "summaryText": "已从 AI 分析结果导入检索上下文，系统已预填检索要素并起草检索草稿。",
        }

    def _load_analysis_artifacts(self, task: Any) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        output_files = metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {}

        analysis_payload = _load_json_file(output_files.get("json"))
        patent_payload = None
        if getattr(task, "output_dir", None):
            patent_payload = _load_json_file(str(Path(str(task.output_dir)) / "patent.json"))

        if analysis_payload is None or patent_payload is None:
            r2_storage = _build_r2_storage()
            if analysis_payload is None:
                analysis_payload = _load_json_bytes(r2_storage.get_bytes(str(output_files.get("analysis_r2_key") or "").strip()))
            if patent_payload is None:
                patent_payload = _load_json_bytes(r2_storage.get_bytes(str(output_files.get("patent_r2_key") or "").strip()))

        if not isinstance(analysis_payload, dict):
            raise HTTPException(status_code=409, detail="AI 分析结果不存在，暂时无法生成检索草稿。")
        return analysis_payload, patent_payload if isinstance(patent_payload, dict) else None

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
            sourceSummary=self._source_summary(task),
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

    def create_session_from_analysis(self, owner_id: str, analysis_task_id: str) -> AiSearchCreateSessionResponse:
        _enforce_daily_quota(owner_id, task_type=TaskType.AI_SEARCH.value)
        analysis_task = self.storage.get_task(str(analysis_task_id or "").strip())
        if (
            not analysis_task
            or str(analysis_task.owner_id or "") != str(owner_id or "")
            or str(analysis_task.task_type or "") != TaskType.PATENT_ANALYSIS.value
        ):
            raise HTTPException(status_code=404, detail="AI 分析任务不存在。")
        if str(getattr(analysis_task.status, "value", analysis_task.status) or "") != "completed":
            raise HTTPException(status_code=409, detail="仅支持从已完成的 AI 分析任务生成检索草稿。")

        emit_system_log(
            category="task_execution",
            event_name="ai_search_seed_requested",
            owner_id=owner_id,
            task_id=str(analysis_task.id),
            task_type=TaskType.AI_SEARCH.value,
            success=True,
            message="请求从 AI 分析创建 AI 检索草稿",
            payload={"analysis_task_id": str(analysis_task.id), "analysis_pn": str(analysis_task.pn or "").strip() or None},
        )

        analysis_payload, patent_payload = self._load_analysis_artifacts(analysis_task)
        seeded_search_elements = _seed_search_elements_from_analysis(analysis_payload, patent_payload)
        source_pn = str(
            analysis_payload.get("metadata", {}).get("resolved_pn")
            if isinstance(analysis_payload.get("metadata"), dict) else ""
        ).strip() or str(getattr(analysis_task, "pn", "") or "").strip()
        source_title = str(getattr(analysis_task, "title", "") or "").strip()
        seed_prompt = _seed_prompt_from_analysis(analysis_payload, patent_payload, seeded_search_elements)
        task = task_manager.create_task(
            owner_id=owner_id,
            task_type=TaskType.AI_SEARCH.value,
            title=f"AI 检索草稿 - {source_pn or source_title or analysis_task.id}",
        )
        thread_id = f"ai-search-{task.id}"
        seed_meta = default_ai_search_meta(thread_id)
        seed_meta["current_phase"] = PHASE_DRAFTING_PLAN
        self.storage.update_task(
            task.id,
            metadata=merge_ai_search_meta(
                task,
                **seed_meta,
                source_type="analysis",
                source_task_id=str(analysis_task.id),
                source_pn=source_pn or None,
                source_title=source_title or None,
                seed_mode="analysis",
            ),
            status=phase_to_task_status(PHASE_DRAFTING_PLAN),
            progress=phase_progress(PHASE_DRAFTING_PLAN),
            current_step=phase_step(PHASE_DRAFTING_PLAN),
        )
        self.storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": task.id,
                "role": "assistant",
                "kind": "search_elements_update",
                "content": str(seeded_search_elements.get("clarification_summary") or "").strip() or None,
                "stream_status": "completed",
                "metadata": seeded_search_elements,
            }
        )
        self._append_message(
            task.id,
            "assistant",
            "chat",
            "已从 AI 分析结果导入检索上下文，正在生成检索草稿。",
        )

        try:
            previous_assistant = self._latest_assistant_chat(task.id)
            result = self._run_main_agent(
                task.id,
                thread_id,
                {"messages": [{"role": "user", "content": seed_prompt}]},
            )
            assistant_text = extract_latest_ai_message(result["values"])
            active_plan_version = int(get_ai_search_meta(self.storage.get_task(task.id)).get("active_plan_version") or 0)
            if assistant_text and assistant_text != previous_assistant:
                self._append_message(task.id, "assistant", "chat", assistant_text, plan_version=active_plan_version or None)
        except Exception as exc:
            self.storage.update_task(
                task.id,
                metadata=merge_ai_search_meta(self.storage.get_task(task.id), current_phase=PHASE_FAILED),
                status=phase_to_task_status(PHASE_FAILED),
                progress=phase_progress(PHASE_FAILED),
                current_step=phase_step(PHASE_FAILED),
                error_message=f"生成 AI 检索草稿失败：{exc}",
            )
            emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_failed",
                owner_id=owner_id,
                task_id=task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=False,
                message="从 AI 分析创建 AI 检索草稿失败",
                payload={"analysis_task_id": str(analysis_task.id), "error": str(exc)},
            )
            raise

        snapshot = self.get_snapshot(task.id, owner_id)
        emit_system_log(
            category="task_execution",
            event_name="ai_search_seed_created",
            owner_id=owner_id,
            task_id=task.id,
            task_type=TaskType.AI_SEARCH.value,
            success=True,
            message="已从 AI 分析创建 AI 检索草稿",
            payload={
                "analysis_task_id": str(analysis_task.id),
                "analysis_pn": source_pn or None,
                "phase": snapshot.phase,
            },
        )
        if snapshot.phase == PHASE_AWAITING_PLAN_CONFIRMATION:
            emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_plan_ready",
                owner_id=owner_id,
                task_id=task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=True,
                message="AI 检索草稿已进入计划确认阶段",
                payload={"analysis_task_id": str(analysis_task.id), "plan_version": snapshot.pendingConfirmation.get("planVersion") if snapshot.pendingConfirmation else None},
            )
        if snapshot.phase == PHASE_AWAITING_USER_ANSWER:
            emit_system_log(
                category="task_execution",
                event_name="ai_search_seed_question_required",
                owner_id=owner_id,
                task_id=task.id,
                task_type=TaskType.AI_SEARCH.value,
                success=True,
                message="AI 检索草稿仍需用户补充信息",
                payload={"analysis_task_id": str(analysis_task.id), "question": snapshot.pendingQuestion.get("prompt") if snapshot.pendingQuestion else None},
            )
        return AiSearchCreateSessionResponse(sessionId=task.id, taskId=task.id, threadId=thread_id)

    def list_sessions(self, owner_id: str) -> AiSearchSessionListResponse:
        tasks = [
            task
            for task in task_manager.list_tasks(owner_id=owner_id, limit=200)
            if str(task.task_type or "") == TaskType.AI_SEARCH.value
        ]
        return AiSearchSessionListResponse(items=[self._session_summary(task) for task in tasks], total=len(tasks))

    def update_session(
        self,
        session_id: str,
        owner_id: str,
        *,
        title: Optional[str] = None,
        pinned: Optional[bool] = None,
    ) -> AiSearchSessionSummary:
        task = self._get_owned_session_task(session_id, owner_id)

        updates: Dict[str, Any] = {}
        if title is not None:
            normalized_title = str(title).strip()
            if not normalized_title:
                raise HTTPException(status_code=422, detail="会话标题不能为空。")
            updates["title"] = normalized_title
        if pinned is not None:
            updates["metadata"] = merge_ai_search_meta(task, pinned=bool(pinned))

        if not updates:
            return self._session_summary(task)

        self.storage.update_task(session_id, **updates)
        updated = self._get_owned_session_task(session_id, owner_id)
        return self._session_summary(updated)

    def delete_session(self, session_id: str, owner_id: str) -> Dict[str, bool]:
        task = self._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        phase = str(meta.get("current_phase") or PHASE_COLLECTING_REQUIREMENTS)
        if phase == PHASE_SEARCHING:
            raise HTTPException(status_code=409, detail="检索执行中，请稍后再删除会话。")

        task_manager.delete_task(session_id)
        return {"deleted": True}

    def _main_agent_config(self, thread_id: str) -> Dict[str, Any]:
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": MAIN_AGENT_CHECKPOINT_NS,
            }
        }

    def _main_agent_state_config(self, agent: Any, thread_id: str) -> Dict[str, Any]:
        config = self._main_agent_config(thread_id)
        config["configurable"]["__pregel_checkpointer"] = agent.checkpointer
        return config

    def _run_main_agent(self, task_id: str, thread_id: str, payload: Any) -> Dict[str, Any]:
        agent = build_main_agent(self.storage, task_id)
        config = self._main_agent_config(thread_id)
        interrupted = False
        for chunk in agent.stream(payload, config):
            if "__interrupt__" in chunk:
                interrupted = True
        state = agent.get_state(self._main_agent_state_config(agent, thread_id))
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
            self._run_main_agent,
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
            self._run_main_agent,
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
            self._run_main_agent,
            task.id,
            thread_id,
            Command(resume={"confirmed": True, "plan_version": plan_version}),
        )
        assistant_text = extract_latest_ai_message(planning_result["values"])
        snapshot = self.get_snapshot(task.id, owner_id)
        if assistant_text and assistant_text != previous_assistant:
            self._append_message(task.id, "assistant", "chat", assistant_text, plan_version=plan_version)
            yield self._format_event("message.completed", task.id, snapshot.phase, {"content": assistant_text})
        yield self._format_event("plan.confirmed", task.id, snapshot.phase, {"planVersion": plan_version})
        async for event in self._emit_snapshot_events(snapshot):
            yield event
        yield self._format_event("run.completed", task.id, snapshot.phase, {"interrupted": planning_result["interrupted"]})

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
            PHASE_COMPLETED,
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
            {"messages": [{"role": "user", "content": build_feature_prompt(search_elements, selected_documents)}]},
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
