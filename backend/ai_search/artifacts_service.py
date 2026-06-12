"""Artifacts/report collaborator for AI Search."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from patent_agents.ai_search.src.office_action import (
    build_office_action_input,
    generate_office_action_payload,
)
from patent_agents.ai_search.src.reporting import (
    latest_report_text,
    render_office_action_markdown,
    render_markdown_report,
    write_selected_documents_csv,
)
from patent_agents.common.rendering.report_render import render_markdown_to_pdf, write_markdown
from patent_agents.ai_search.src.state import PHASE_COMPLETED, get_ai_search_meta
from fastapi import HTTPException
from fastapi.responses import FileResponse

from config import settings

from .models import AiSearchArtifactAttachment

ATTACHMENT_SPECS: dict[str, dict[str, str | bool]] = {
    "search_report_pdf": {
        "output_key": "ai_search_report_pdf",
        "kind": "search_report_pdf",
        "media_type": "application/pdf",
        "suffix": ".pdf",
        "prefix": "AI 检索报告",
        "default_filename": "ai_search_report.pdf",
        "primary": True,
    },
    "search_report_markdown": {
        "output_key": "ai_search_report_markdown",
        "kind": "search_report_markdown",
        "media_type": "text/markdown",
        "suffix": ".md",
        "prefix": "AI 检索报告",
        "default_filename": "ai_search_report.md",
        "primary": False,
    },
    "selected_documents_csv": {
        "output_key": "ai_search_selected_documents_csv",
        "kind": "selected_documents_csv",
        "media_type": "text/csv",
        "suffix": ".csv",
        "prefix": "AI 检索已选文献",
        "default_filename": "selected_documents.csv",
        "primary": False,
    },
    "search_report_json": {
        "output_key": "ai_search_report_json",
        "kind": "search_report_json",
        "media_type": "application/json",
        "suffix": ".json",
        "prefix": "AI 检索数据",
        "default_filename": "ai_search_report.json",
        "primary": False,
    },
    "office_action_pdf": {
        "output_key": "ai_search_office_action_pdf",
        "kind": "office_action_pdf",
        "media_type": "application/pdf",
        "suffix": ".pdf",
        "prefix": "审查意见通知书",
        "default_filename": "office_action.pdf",
        "primary": True,
    },
    "office_action_markdown": {
        "output_key": "ai_search_office_action_markdown",
        "kind": "office_action_markdown",
        "media_type": "text/markdown",
        "suffix": ".md",
        "prefix": "审查意见通知书",
        "default_filename": "office_action.md",
        "primary": False,
    },
    "office_action_json": {
        "output_key": "ai_search_office_action_json",
        "kind": "office_action_json",
        "media_type": "application/json",
        "suffix": ".json",
        "prefix": "审查意见通知书数据",
        "default_filename": "office_action.json",
        "primary": False,
    },
}


class AiSearchArtifactsService:
    def __init__(self, facade: Any) -> None:
        self.facade = facade

    @property
    def storage(self):
        return self.facade.storage

    def _task_artifact_label(self, task: Any) -> str:
        value = str(getattr(task, "title", None) or getattr(task, "id", "")).strip()
        return value or str(getattr(task, "id", "")).strip() or "task"

    def _task_output_path(self, task: Any, attachment_id: str) -> Optional[Path]:
        spec = ATTACHMENT_SPECS.get(str(attachment_id or "").strip())
        if not spec:
            return None
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        output_files = metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {}
        output_key = str(spec.get("output_key") or "").strip()
        raw_path = str(output_files.get(output_key) or "").strip()
        if raw_path:
            path = Path(raw_path)
        else:
            output_dir = Path(str(getattr(task, "output_dir", "") or "").strip())
            default_filename = str(spec.get("default_filename") or "").strip()
            if not output_dir or not output_dir.exists() or not default_filename:
                return None
            path = output_dir / default_filename
        if not path.exists() or not path.is_file():
            return None
        return path

    def _task_has_terminal_artifacts(self, task: Any) -> bool:
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        output_files = metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {}
        if any(str(output_files.get(str(spec.get("output_key") or "")) or "").strip() for spec in ATTACHMENT_SPECS.values()):
            return True
        status = str(getattr(task.status, "value", task.status) or "").strip().lower()
        if status == "completed":
            return True
        meta = get_ai_search_meta(task)
        return str(meta.get("current_phase") or "").strip() == PHASE_COMPLETED

    def _attachment_name(self, task: Any, attachment_id: str) -> str:
        spec = ATTACHMENT_SPECS[str(attachment_id)]
        label = self._task_artifact_label(task)
        return f"{spec['prefix']}_{label}{spec['suffix']}"

    def _attachment_created_at(self, path: Path) -> str:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def _snapshot_attachments(self, task: Any) -> list[AiSearchArtifactAttachment]:
        if not self._task_has_terminal_artifacts(task):
            return []
        attachments: list[AiSearchArtifactAttachment] = []
        for attachment_id, spec in ATTACHMENT_SPECS.items():
            path = self._task_output_path(task, attachment_id)
            if not path:
                continue
            attachments.append(
                AiSearchArtifactAttachment(
                    attachmentId=attachment_id,
                    kind=str(spec["kind"]),
                    name=self._attachment_name(task, attachment_id),
                    downloadUrl=f"/api/ai-search/sessions/{task.id}/attachments/{attachment_id}/download",
                    mediaType=str(spec["media_type"]),
                    sizeBytes=path.stat().st_size,
                    createdAt=self._attachment_created_at(path),
                    isPrimary=bool(spec["primary"]),
                )
            )
        return attachments

    def download_attachment(self, task: Any, attachment_id: str) -> FileResponse:
        normalized_attachment_id = str(attachment_id or "").strip()
        if normalized_attachment_id not in ATTACHMENT_SPECS:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "AI_SEARCH_ATTACHMENT_NOT_FOUND",
                    "message": "附件不存在。",
                },
            )
        attachments = self._snapshot_attachments(task)
        attachment = next((item for item in attachments if item.attachmentId == normalized_attachment_id), None)
        if attachment is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "AI_SEARCH_ATTACHMENT_NOT_FOUND",
                    "message": "附件不存在或尚未生成。",
                },
            )
        path = self._task_output_path(task, normalized_attachment_id)
        if path is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "AI_SEARCH_ATTACHMENT_NOT_FOUND",
                    "message": "附件不存在或尚未生成。",
                },
            )
        return FileResponse(
            path=str(path),
            filename=attachment.name,
            media_type=attachment.mediaType,
        )

    def _output_dir(self, task: Any) -> Path:
        raw_output_dir = str(getattr(task, "output_dir", "") or "").strip()
        output_dir = Path(raw_output_dir) if raw_output_dir else settings.OUTPUT_DIR / task.id
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _documents_for_active_plan(self, task: Any) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 1)
        documents = self.storage.list_ai_search_documents(task.id, plan_version)
        candidates = [item for item in documents if str(item.get("stage") or "") not in {"selected", "rejected"}]
        selected = [item for item in documents if str(item.get("stage") or "") == "selected"]
        return plan_version, candidates, selected

    def _source_context(self, task: Any) -> dict[str, Any]:
        meta = get_ai_search_meta(task)
        return {
            "source_type": str(meta.get("source_type") or "").strip() or None,
            "source_task_id": str(meta.get("source_task_id") or "").strip() or None,
            "source_pn": str(meta.get("source_pn") or getattr(task, "pn", "") or "").strip() or None,
            "source_title": str(meta.get("source_title") or "").strip() or None,
            "seed_mode": str(meta.get("seed_mode") or "").strip() or None,
        }

    def _trace_summary(self, task_id: str) -> list[dict[str, str]]:
        traces: list[dict[str, str]] = []
        for event in self.storage.list_ai_search_stream_events(task_id, after_seq=0):
            event_type = str(event.get("event_type") or "").strip()
            if event_type != "trace.completed":
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            label = str(payload.get("label") or payload.get("toolName") or "").strip()
            detail = str(payload.get("detail") or "").strip()
            output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
            if not detail and output:
                new_count = output.get("new_count")
                retrieved_count = output.get("retrieved_count")
                if new_count is not None or retrieved_count is not None:
                    detail = f"召回 {retrieved_count or 0} 条，新增 {new_count or 0} 条"
            if label:
                traces.append({"label": label, "detail": detail})
        return traces

    def _report_payload(self, task: Any) -> dict[str, Any]:
        meta = get_ai_search_meta(task)
        plan_version, candidates, selected = self._documents_for_active_plan(task)
        messages = self.storage.list_ai_search_messages(task.id)
        report_text = latest_report_text(messages)
        return {
            "sessionId": task.id,
            "title": str(getattr(task, "title", "") or "").strip(),
            "planVersion": plan_version,
            "sourceContext": self._source_context(task),
            "stopPolicy": meta.get("stop_policy") if isinstance(meta.get("stop_policy"), dict) else {},
            "stats": {
                "searchRounds": int(meta.get("search_rounds") or 0),
                "queryCount": int(meta.get("query_count") or 0),
                "candidateCount": len(candidates),
                "selectedCount": len(selected),
            },
            "report": report_text,
            "selectedDocuments": selected,
            "candidateDocuments": candidates,
            "traceSummary": self._trace_summary(task.id),
        }

    def _store_output_files(self, task: Any, output_files_update: dict[str, str]) -> Any:
        metadata = dict(getattr(task, "metadata", {}) if isinstance(getattr(task, "metadata", {}), dict) else {})
        output_files = dict(metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {})
        output_files.update(output_files_update)
        metadata["output_files"] = output_files
        self.storage.update_task(task.id, metadata=metadata)
        return self.storage.get_task(task.id) or task

    def export_session_report(self, task: Any) -> list[AiSearchArtifactAttachment]:
        output_dir = self._output_dir(task)
        report_payload = self._report_payload(task)

        markdown_path = output_dir / "ai_search_report.md"
        json_path = output_dir / "ai_search_report.json"
        csv_path = output_dir / "selected_documents.csv"
        pdf_path = output_dir / "ai_search_report.pdf"

        markdown_text = render_markdown_report(report_payload)
        write_markdown(markdown_text, markdown_path)
        render_markdown_to_pdf(
            md_text=markdown_text,
            output_path=pdf_path,
            title="AI 检索报告",
            enable_mathjax=False,
            enable_echarts=False,
        )

        json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        write_selected_documents_csv(csv_path, report_payload["selectedDocuments"])

        updated = self._store_output_files(
            task,
            {
                "ai_search_report_pdf": str(pdf_path),
                "ai_search_report_markdown": str(markdown_path),
                "ai_search_report_json": str(json_path),
                "ai_search_selected_documents_csv": str(csv_path),
            },
        )
        return self._snapshot_attachments(updated)

    def export_office_action(self, task: Any) -> list[AiSearchArtifactAttachment]:
        report_payload = self._report_payload(task)
        source_context = report_payload.get("sourceContext") if isinstance(report_payload.get("sourceContext"), dict) else {}
        selected = report_payload.get("selectedDocuments") if isinstance(report_payload.get("selectedDocuments"), list) else []
        if not selected:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "AI_SEARCH_OFFICE_ACTION_NO_SELECTED_DOCUMENTS",
                    "message": "生成审查意见通知书前至少需要选中 1 篇对比文献。",
                },
            )
        if not str(source_context.get("source_pn") or source_context.get("source_title") or "").strip():
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "AI_SEARCH_OFFICE_ACTION_SOURCE_CONTEXT_REQUIRED",
                    "message": "当前检索会话缺少目标专利上下文，请从 AI 分析任务创建检索会话后再生成通知书。",
                },
            )

        input_payload = build_office_action_input(
            session_id=task.id,
            title=str(getattr(task, "title", "") or "").strip(),
            source_context=source_context,
            selected_documents=selected,
            report_text=str(report_payload.get("report") or "").strip(),
        )
        try:
            office_action_payload = generate_office_action_payload(input_payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "code": "AI_SEARCH_OFFICE_ACTION_DRAFT_INVALID",
                    "message": str(exc),
                },
            ) from exc

        output_dir = self._output_dir(task)
        markdown_path = output_dir / "office_action.md"
        json_path = output_dir / "office_action.json"
        pdf_path = output_dir / "office_action.pdf"
        markdown_text = render_office_action_markdown(office_action_payload)
        write_markdown(markdown_text, markdown_path)
        render_markdown_to_pdf(
            md_text=markdown_text,
            output_path=pdf_path,
            title="审查意见通知书",
            enable_mathjax=False,
            enable_echarts=False,
        )
        json_path.write_text(json.dumps(office_action_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        updated = self._store_output_files(
            task,
            {
                "ai_search_office_action_pdf": str(pdf_path),
                "ai_search_office_action_markdown": str(markdown_path),
                "ai_search_office_action_json": str(json_path),
            },
        )
        return self._snapshot_attachments(updated)
