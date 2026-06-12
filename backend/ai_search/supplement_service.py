"""User-supplied document ingestion for AI Search."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, UploadFile

from backend.storage import TaskStatus
from backend.time_utils import utc_now_z
from config import settings
from patent_agents.ai_search.src.ids import (
    build_ai_search_canonical_id,
    stable_ai_search_document_id,
)
from patent_agents.ai_search.src.runtime import (
    AiSearchRuntimeContext,
    _apply_patent_detail,
    _fetch_patent_detail_for_detail_agent,
    _parse_patent_number_list,
    _safe_text,
    documents_payload,
)
from patent_agents.ai_search.src.state import (
    PHASE_IDLE,
    PHASE_RUNNING,
    get_ai_search_meta,
    merge_ai_search_meta,
)


SUPPLEMENT_RUNNING_CODE = "AI_SEARCH_SUPPLEMENT_RUNNING"
SUPPLEMENT_EMPTY_CODE = "AI_SEARCH_SUPPLEMENT_EMPTY"


class AiSearchSupplementService:
    def __init__(self, facade: Any) -> None:
        self.facade = facade

    @property
    def storage(self):
        return self.facade.storage

    def _append_event(
        self,
        task_id: str,
        event_type: str,
        payload: Dict[str, Any],
        *,
        run_id: str = "",
        entity_id: str = "",
    ) -> Dict[str, Any]:
        return self.storage.append_ai_search_stream_event(
            {
                "event_id": uuid.uuid4().hex,
                "session_id": task_id,
                "task_id": task_id,
                "run_id": run_id or None,
                "event_type": event_type,
                "entity_id": entity_id or None,
                "payload": payload,
            }
        ) or {
            "event_type": event_type,
            "session_id": task_id,
            "task_id": task_id,
            "run_id": run_id,
            "entity_id": entity_id,
            "payload": payload,
            "created_at": utc_now_z(),
            "seq": 0,
        }

    def _ensure_document_run(self, task: Any) -> Dict[str, Any]:
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 1)
        run = self.storage.get_ai_search_run(task.id, plan_version=plan_version)
        if run:
            return run
        run_id = uuid.uuid4().hex
        self.storage.create_ai_search_run(
            {
                "run_id": run_id,
                "task_id": task.id,
                "plan_version": plan_version,
                "phase": PHASE_IDLE,
                "status": TaskStatus.PROCESSING.value,
                "selected_document_count": int(meta.get("selected_document_count") or 0),
            }
        )
        self.storage.update_task(
            task.id,
            metadata=merge_ai_search_meta(task, active_plan_version=plan_version, current_run_id=run_id),
        )
        return self.storage.get_ai_search_run(task.id, run_id) or {"run_id": run_id, "plan_version": plan_version}

    def _save_upload(self, task_id: str, upload: UploadFile) -> Path:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix != ".pdf":
            raise HTTPException(status_code=400, detail="补充文献目前仅支持 PDF 文件。")
        safe_name = Path(upload.filename or "supplement.pdf").name
        upload_dir = settings.UPLOAD_DIR / task_id / "ai_search_supplements"
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / f"{uuid.uuid4().hex}_{safe_name}"
        return target

    async def _read_upload_file(self, task_id: str, upload: UploadFile) -> tuple[Path, bytes]:
        target = self._save_upload(task_id, upload)
        content = await upload.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"补充文献为空：{upload.filename or 'upload.pdf'}")
        target.write_bytes(content)
        return target, content

    def _extract_pdf_text(self, path: Path, *, max_pages: int = 80) -> str:
        try:
            from pypdf import PdfReader
        except Exception as exc:  # pragma: no cover - dependency is expected in project env
            raise RuntimeError("缺少 pypdf，无法解析 PDF。") from exc
        reader = PdfReader(str(path))
        parts: List[str] = []
        for page in reader.pages[:max_pages]:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts).strip()

    def _pdf_record(
        self,
        runtime: AiSearchRuntimeContext,
        *,
        path: Path,
        content: bytes,
        text: str,
        original_name: str,
    ) -> Dict[str, Any]:
        digest = hashlib.sha256(content).hexdigest()
        title = Path(original_name or path.name).stem or "用户补充 PDF"
        canonical_id = build_ai_search_canonical_id(source_type="user_pdf", external_id=digest)
        document_id = stable_ai_search_document_id(runtime.task_id, runtime.plan_version, canonical_id)
        compact_text = " ".join(text.split())
        abstract = compact_text[:1200]
        key_passages = [
            {"source": "pdf", "preview": chunk[:500]}
            for chunk in [text[index : index + 1200].strip() for index in range(0, min(len(text), 3600), 1200)]
            if chunk
        ]
        return {
            "run_id": runtime.run_id,
            "task_id": runtime.task_id,
            "plan_version": runtime.plan_version,
            "document_id": document_id,
            "source_type": "user_pdf",
            "external_id": digest,
            "canonical_id": canonical_id,
            "url": str(path),
            "title": title,
            "abstract": abstract,
            "document_type": "uploaded_pdf",
            "evidence_summary": text[:12000],
            "stage": "candidate",
            "score": 0.65,
            "agent_reason": "用户补充 PDF，待 agent 筛查对比。",
            "key_passages_json": key_passages,
            "user_pinned": True,
            "detail_fingerprint": digest,
            "detail_source": "user_uploaded_pdf",
        }

    async def _ingest_pdf(
        self,
        runtime: AiSearchRuntimeContext,
        upload: UploadFile,
        *,
        parent_trace_id: str,
    ) -> Dict[str, Any]:
        original_name = Path(upload.filename or "upload.pdf").name
        trace = runtime.start_trace(
            tool_name="ingest_user_pdf",
            label=f"读取用户补充 PDF：{original_name}",
            trace_type="tool",
            actor_name="ai-search-agent",
            input={"filename": original_name},
            parent_trace_id=parent_trace_id,
        )
        try:
            path, content = await self._read_upload_file(runtime.task_id, upload)
            text = await asyncio.to_thread(self._extract_pdf_text, path)
            if not text:
                raise ValueError("PDF 未解析出可用文本。")
            record = self._pdf_record(
                runtime,
                path=path,
                content=content,
                text=text,
                original_name=original_name,
            )
            self.storage.upsert_ai_search_documents([record])
            result = {
                "filename": original_name,
                "document_id": record["document_id"],
                "title": record["title"],
                "text_chars": len(text),
                "stored_as_candidate": True,
            }
            runtime.finish_trace(
                trace,
                tool_name="ingest_user_pdf",
                label=f"用户补充 PDF 已入库：{original_name}",
                trace_type="tool",
                actor_name="ai-search-agent",
                output=result,
                parent_trace_id=parent_trace_id,
            )
            return result
        except Exception as exc:
            result = {"filename": original_name, "error": str(exc), "stored_as_candidate": False}
            runtime.finish_trace(
                trace,
                tool_name="ingest_user_pdf",
                label=f"用户补充 PDF 入库失败：{original_name}",
                detail=str(exc),
                status="failed",
                trace_type="tool",
                actor_name="ai-search-agent",
                output=result,
                parent_trace_id=parent_trace_id,
            )
            return result

    def _review_prompt(self, *, patent_numbers: List[str], pdf_results: List[Dict[str, Any]], review_goal: str) -> str:
        segments = []
        if patent_numbers:
            segments.append(f"用户补充了公开号：{', '.join(patent_numbers)}")
        pdf_titles = [str(item.get("title") or item.get("filename") or "").strip() for item in pdf_results if item.get("stored_as_candidate")]
        if pdf_titles:
            segments.append(f"用户补充了 PDF 文献：{', '.join(pdf_titles)}")
        goal = _safe_text(review_goal) or "请筛查这些用户补充文献与当前检索目标/已选证据的相关性，指出命中点、缺口，并建议是否选为对比文献。"
        return f"{'；'.join(segments) or '用户补充了文献'}。{goal}"

    async def supplement_documents(
        self,
        session_id: str,
        owner_id: str,
        *,
        patent_numbers: str = "",
        files: Optional[List[UploadFile]] = None,
        review_goal: str = "",
    ) -> Dict[str, Any]:
        task = self.facade.sessions._get_owned_session_task(session_id, owner_id)
        meta = get_ai_search_meta(task)
        if str(meta.get("current_phase") or "").strip() == PHASE_RUNNING:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": SUPPLEMENT_RUNNING_CODE,
                    "message": "当前检索轮次仍在进行中，请先停止本轮或等待结束后再补充文献。",
                },
            )

        uploads = [item for item in (files or []) if item and (item.filename or "").strip()]
        numbers = _parse_patent_number_list(patent_numbers, limit=30)
        if not numbers and not uploads:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": SUPPLEMENT_EMPTY_CODE,
                    "message": "请至少填写一个公开号，或上传一份 PDF 文献。",
                },
            )

        run = self._ensure_document_run(task)
        runtime = AiSearchRuntimeContext(self.storage, task.id, str(run.get("run_id") or ""), int(run.get("plan_version") or 1))
        parent_trace = runtime.start_trace(
            tool_name="user_supplement_documents",
            label="导入用户补充文献",
            trace_type="agent",
            actor_name="ai-search-agent",
            input={
                "patent_numbers": numbers,
                "file_count": len(uploads),
                "review_goal": _safe_text(review_goal),
            },
            metadata={"source": "user_supplement"},
        )

        patent_results = await asyncio.gather(
            *[
                _fetch_patent_detail_for_detail_agent(
                    runtime,
                    number,
                    parent_trace_id=parent_trace[0],
                    allow_new_candidate=True,
                )
                for number in numbers
            ]
        ) if numbers else []
        pdf_results = await asyncio.gather(
            *[self._ingest_pdf(runtime, upload, parent_trace_id=parent_trace[0]) for upload in uploads]
        ) if uploads else []

        imported_count = len([item for item in [*patent_results, *pdf_results] if item.get("stored_as_candidate") or item.get("target_detail")])
        failed_items = [
            item
            for item in [*patent_results, *pdf_results]
            if item.get("error") or item.get("blocked")
        ]
        self._append_event(task.id, "documents.updated", documents_payload(runtime), run_id=runtime.run_id)
        prompt = self._review_prompt(patent_numbers=numbers, pdf_results=pdf_results, review_goal=review_goal)
        task = self.storage.get_task(task.id)
        existing_files = []
        if task and isinstance(task.metadata, dict):
            existing_files = list(task.metadata.get("supplemental_files") or [])
        stored_files = [
            {
                "filename": item.get("filename"),
                "document_id": item.get("document_id"),
                "title": item.get("title"),
            }
            for item in pdf_results
            if item.get("stored_as_candidate")
        ]
        if task and stored_files:
            self.storage.update_task(
                task.id,
                metadata=merge_ai_search_meta(task, supplemental_files=[*existing_files, *stored_files]),
            )
        runtime.finish_trace(
            parent_trace,
            tool_name="user_supplement_documents",
            label=f"用户补充文献已导入：{imported_count} 篇",
            trace_type="agent",
            actor_name="ai-search-agent",
            output={
                "imported_count": imported_count,
                "patent_count": len(numbers),
                "pdf_count": len([item for item in pdf_results if item.get("stored_as_candidate")]),
                "failed_count": len(failed_items),
                "review_prompt": prompt,
            },
            metadata={"source": "user_supplement"},
        )
        return {
            "importedCount": imported_count,
            "patentCount": len(numbers),
            "pdfCount": len([item for item in pdf_results if item.get("stored_as_candidate")]),
            "failedItems": failed_items,
            "reviewPrompt": prompt,
            "snapshot": self.facade.snapshots.get_snapshot(session_id, owner_id),
        }
