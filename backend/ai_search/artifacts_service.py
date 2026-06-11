"""Artifacts/report collaborator for AI Search."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from patent_agents.ai_search.src.reporting import (
    latest_report_text,
    render_markdown_report,
    write_selected_documents_csv,
)
from patent_agents.ai_search.src.state import PHASE_COMPLETED, get_ai_search_meta
from fastapi import HTTPException
from fastapi.responses import FileResponse

from config import settings

from .models import AiSearchArtifactAttachment

ATTACHMENT_SPECS: dict[str, dict[str, str | bool]] = {
    "result_bundle": {
        "output_key": "ai_search_bundle",
        "kind": "result_bundle",
        "media_type": "application/zip",
        "suffix": ".zip",
        "prefix": "AI 检索结果",
        "default_filename": "ai_search_result_bundle.zip",
        "primary": True,
    },
    "report_markdown": {
        "output_key": "ai_search_report_markdown",
        "kind": "report_markdown",
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
    "report_json": {
        "output_key": "ai_search_report_json",
        "kind": "report_json",
        "media_type": "application/json",
        "suffix": ".json",
        "prefix": "AI 检索数据",
        "default_filename": "ai_search_report.json",
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

    def export_session_report(self, task: Any) -> list[AiSearchArtifactAttachment]:
        meta = get_ai_search_meta(task)
        plan_version = int(meta.get("active_plan_version") or 1)
        documents = self.storage.list_ai_search_documents(task.id, plan_version)
        candidates = [item for item in documents if str(item.get("stage") or "") not in {"selected", "rejected"}]
        selected = [item for item in documents if str(item.get("stage") or "") == "selected"]
        messages = self.storage.list_ai_search_messages(task.id)
        report_text = latest_report_text(messages)
        raw_output_dir = str(getattr(task, "output_dir", "") or "").strip()
        output_dir = Path(raw_output_dir) if raw_output_dir else settings.OUTPUT_DIR / task.id
        output_dir.mkdir(parents=True, exist_ok=True)

        report_payload = {
            "sessionId": task.id,
            "title": str(getattr(task, "title", "") or "").strip(),
            "planVersion": plan_version,
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
        }

        markdown_path = output_dir / "ai_search_report.md"
        json_path = output_dir / "ai_search_report.json"
        csv_path = output_dir / "selected_documents.csv"
        zip_path = output_dir / "ai_search_result_bundle.zip"

        markdown_path.write_text(render_markdown_report(report_payload), encoding="utf-8")
        json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        write_selected_documents_csv(csv_path, selected)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(markdown_path, markdown_path.name)
            archive.write(json_path, json_path.name)
            archive.write(csv_path, csv_path.name)

        metadata = dict(getattr(task, "metadata", {}) if isinstance(getattr(task, "metadata", {}), dict) else {})
        output_files = dict(metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {})
        output_files.update(
            {
                "ai_search_report_markdown": str(markdown_path),
                "ai_search_report_json": str(json_path),
                "ai_search_selected_documents_csv": str(csv_path),
                "ai_search_bundle": str(zip_path),
            }
        )
        metadata["output_files"] = output_files
        self.storage.update_task(task.id, metadata=metadata)
        updated = self.storage.get_task(task.id) or task
        return self._snapshot_attachments(updated)
