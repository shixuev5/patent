"""Artifacts/report collaborator for AI Search."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from agents.ai_search.src.context import AiSearchAgentContext

class AiSearchArtifactsService:
    def __init__(self, facade: Any) -> None:
        self.facade = facade
        self.storage = facade.storage

    def _snapshot_download_url(self, task: Any) -> Optional[str]:
        if str(getattr(task.status, "value", task.status) or "").strip().lower() != "completed":
            return None
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        output_files = metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {}
        bundle_zip = str(output_files.get("bundle_zip") or "").strip()
        if not bundle_zip:
            return None
        bundle_path = Path(bundle_zip)
        if not bundle_path.exists() or not bundle_path.is_file():
            return None
        return f"/api/tasks/{task.id}/download"

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
        current_feature_comparison_id = str(run.get("active_batch_id") or "").strip() if isinstance(run, dict) else ""
        if current_feature_comparison_id:
            table = self.storage.get_ai_search_feature_comparison(
                task.id,
                str(run.get("run_id") or "") if isinstance(run, dict) else plan_version,
            )
            if table:
                return table
        if fallback_latest:
            return self.storage.get_ai_search_feature_comparison(task.id, str(run.get("run_id") or "") if isinstance(run, dict) else plan_version)
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
        artifacts = self.facade._build_terminal_artifacts(
            task=task,
            current_plan=current_plan,
            documents=documents,
            feature_comparison=feature_comparison,
            close_read_result=gap_context.get("close_read_result") if isinstance(gap_context.get("close_read_result"), dict) else None,
            feature_compare_result=gap_context.get("feature_compare_result") if isinstance(gap_context.get("feature_compare_result"), dict) else None,
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
