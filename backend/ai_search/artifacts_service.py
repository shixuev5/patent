"""Artifacts/report collaborator for AI Search."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.state import PHASE_COMPLETED, get_ai_search_meta
from fastapi import HTTPException
from fastapi.responses import FileResponse

from .models import AiSearchArtifactAttachment

ATTACHMENT_SPECS: dict[str, dict[str, str | bool]] = {
    "result_bundle": {
        "output_key": "bundle_zip",
        "kind": "result_bundle",
        "media_type": "application/zip",
        "suffix": ".zip",
        "prefix": "AI 检索结果",
        "default_filename": "ai_search_result_bundle.zip",
        "primary": True,
    },
    "feature_comparison_csv": {
        "output_key": "feature_comparison_csv",
        "kind": "feature_comparison_csv",
        "media_type": "text/csv",
        "suffix": ".csv",
        "prefix": "AI 检索特征对比",
        "default_filename": "feature_comparison.csv",
        "primary": False,
    },
    "report_pdf": {
        "output_key": "pdf",
        "kind": "report_pdf",
        "media_type": "application/pdf",
        "suffix": ".pdf",
        "prefix": "AI 检索报告",
        "default_filename": "ai_search_report.pdf",
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

    def _current_feature_comparison(
        self,
        task: Any,
        plan_version: int,
        *,
        fallback_latest: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if plan_version <= 0:
            return None
        run = self.facade.snapshots._active_run(task)
        run_id = str(run.get("run_id") or "").strip() if isinstance(run, dict) else ""
        active_batch_id = str(run.get("active_batch_id") or "").strip() if isinstance(run, dict) else ""
        active_batch = self.storage.get_ai_search_batch(active_batch_id) if active_batch_id else None
        if str((active_batch or {}).get("batch_type") or "").strip() == "feature_comparison":
            table = self.storage.get_ai_search_feature_comparison(
                task.id,
                run_id or plan_version,
            )
            if table:
                return table
        if fallback_latest:
            return self.storage.get_ai_search_feature_comparison(task.id, run_id or plan_version)
        return None

    def _finalize_terminal_artifacts(
        self,
        task_id: str,
        plan_version: int,
        *,
        termination_reason: str = "",
    ) -> Dict[str, Any]:
        task = self.storage.get_task(task_id)
        current_plan = self.facade.snapshots._plan_payload(self.storage.get_ai_search_plan(task_id, plan_version))
        documents = self.storage.list_ai_search_documents(task_id, plan_version)
        feature_comparison = self._current_feature_comparison(task, plan_version, fallback_latest=True)
        context = AiSearchAgentContext(self.storage, task_id)
        gap_context = context.latest_gap_context()
        feature_compare_markdown_getter = getattr(context, "latest_agent_markdown_content", None)
        feature_compare_markdown = ""
        if callable(feature_compare_markdown_getter):
            try:
                feature_compare_markdown = str(
                    feature_compare_markdown_getter("feature-comparer", plan_version=plan_version) or ""
                ).strip()
            except Exception:
                feature_compare_markdown = ""
        artifacts = self.facade._build_terminal_artifacts(
            task=task,
            current_plan=current_plan,
            documents=documents,
            feature_comparison=feature_comparison,
            close_read_result=gap_context.get("close_read_result") if isinstance(gap_context.get("close_read_result"), dict) else None,
            feature_compare_result=gap_context.get("feature_compare_result") if isinstance(gap_context.get("feature_compare_result"), dict) else None,
            feature_compare_markdown=feature_compare_markdown,
            source_patent_data=context.load_source_patent_data(),
            termination_reason=termination_reason,
        )
        for item in artifacts.get("classified_documents") or []:
            document_id = str(item.get("document_id") or "").strip()
            if not document_id:
                continue
            self.storage.update_ai_search_document(
                task_id,
                plan_version,
                document_id,
                document_type=str(item.get("document_type") or "").strip().upper() or None,
                report_row_order=int(item.get("report_row_order") or 0) or None,
            )

        refreshed_task = self.storage.get_task(task_id)
        metadata = refreshed_task.metadata if isinstance(refreshed_task.metadata, dict) else {}
        output_files = metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {}
        next_output_files = {
            **output_files,
            "pdf": artifacts.get("pdf"),
            "bundle_zip": artifacts.get("bundle_zip"),
        }
        feature_comparison_csv = str(artifacts.get("feature_comparison_csv") or "").strip()
        if feature_comparison_csv:
            next_output_files["feature_comparison_csv"] = feature_comparison_csv
        self.storage.update_task(
            task_id,
            metadata={
                **metadata,
                "output_files": next_output_files,
            },
        )
        return artifacts
