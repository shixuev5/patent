"""精读子代理工具。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.runtime_context import resolve_agent_context
from agents.ai_search.src.stage_limits import DEFAULT_SHORTLIST_LIMIT
from agents.ai_search.src.state import PHASE_CLOSE_READ
from agents.ai_search.src.subagents.close_reader.schemas import CloseReaderOutput
from agents.ai_search.src.subagents.close_reader.prompt import build_close_reader_prompt
from agents.ai_search.src.subagents.close_reader.workspace import load_document_details, prepare_close_read_workspace


def _primary_ipc(detail: Dict[str, Any], fallback_values: List[Any]) -> str:
    for source in (
        detail.get("ipc"),
        detail.get("cpc"),
        fallback_values,
    ):
        if not isinstance(source, list):
            continue
        for value in source:
            text = str(value or "").strip()
            if text:
                return text
    return ""


def build_close_reader_tools() -> List[Any]:
    def _resolve_loaded_batch(resolved_context: Any, version: int) -> tuple[Dict[str, Any] | None, List[str]]:
        run = resolved_context.active_run(version)
        active_batch_id = str(run.get("active_batch_id") or "").strip() if isinstance(run, dict) else ""
        if not active_batch_id:
            return None, []
        batch = resolved_context.storage.get_ai_search_batch(active_batch_id)
        if not batch or str(batch.get("batch_type") or "") != "close_read":
            return None, []
        if str(batch.get("status") or "") != "loaded":
            return None, []
        pending_ids = [
            str(item or "").strip()
            for item in resolved_context.storage.list_ai_search_batch_documents(active_batch_id)
            if str(item or "").strip()
        ]
        return batch, pending_ids

    def run_close_read_batch(
        operation: str = "load",
        plan_version: int = 0,
        limit: int = DEFAULT_SHORTLIST_LIMIT,
        payload: Dict[str, Any] | None = None,
        runtime: ToolRuntime = None,
    ) -> str:
        """准备或提交精读批次。"""
        resolved_context = resolve_agent_context(runtime)
        version = int(plan_version or resolved_context.active_plan_version() or 0)
        normalized_operation = str(operation or "load").strip().lower()
        if normalized_operation not in {"load", "commit"}:
            raise ValueError("run_close_read_batch 仅支持 load 或 commit 模式。")
        if normalized_operation == "commit":
            result = CloseReaderOutput.model_validate(payload or {})
            selected_count = resolved_context.persist_close_read_result(
                result.model_dump(mode="python"),
                plan_version=version,
                runtime=runtime.context if runtime else None,
            )
            return json.dumps(
                {
                    "selected_count": int(selected_count or 0),
                    "rejected_count": len(result.rejected),
                    "key_passage_count": len(result.key_passages),
                },
                ensure_ascii=False,
            )
        records = resolved_context.storage.list_ai_search_documents(resolved_context.task_id, version)
        loaded_batch, loaded_ids = _resolve_loaded_batch(resolved_context, version)
        loaded_id_set = set(loaded_ids)
        pending = [
            item
            for item in records
            if str(item.get("coarse_status") or "") == "kept" and str(item.get("close_read_status") or "pending") == "pending"
            and (not loaded_id_set or str(item.get("document_id") or "").strip() in loaded_id_set)
        ][: max(int(limit or DEFAULT_SHORTLIST_LIMIT), 1)]
        details: List[Dict[str, Any]] = []
        task = resolved_context.storage.get_task(resolved_context.task_id)
        workspace_root = Path(str(getattr(task, "output_dir", "") or Path.cwd() / ".ai_search" / resolved_context.task_id))
        workspace_dir = workspace_root / "close_read" / f"plan_{version}"
        workspace_manifest_path = workspace_dir / "manifest.json"
        cached_manifest: Dict[str, Any] = {}
        if workspace_manifest_path.exists() and workspace_manifest_path.is_file():
            try:
                cached_manifest = json.loads(workspace_manifest_path.read_text(encoding="utf-8"))
            except Exception:
                cached_manifest = {}
        for item in pending:
            document_id = str(item.get("document_id") or "").strip()
            cached_detail = cached_manifest.get(document_id) if isinstance(cached_manifest, dict) else None
            if isinstance(cached_detail, dict):
                detail = {
                    "document_id": document_id,
                    "source_type": str(cached_detail.get("source_type") or item.get("source_type") or "").strip(),
                    "pn": str(cached_detail.get("pn") or item.get("pn") or "").strip().upper(),
                    "title": str(cached_detail.get("title") or item.get("title") or "").strip(),
                    "abstract": str(cached_detail.get("abstract") or item.get("abstract") or "").strip(),
                    "claims": str(cached_detail.get("claims") or "").strip(),
                    "description": str(cached_detail.get("description") or "").strip(),
                    "publication_date": str(cached_detail.get("publication_date") or item.get("publication_date") or "").strip(),
                    "application_date": str(cached_detail.get("application_date") or item.get("application_date") or "").strip(),
                    "venue": str(cached_detail.get("venue") or item.get("venue") or "").strip(),
                    "doi": str(cached_detail.get("doi") or item.get("doi") or "").strip(),
                    "url": str(cached_detail.get("url") or item.get("url") or "").strip(),
                    "ipc": cached_detail.get("ipc") if isinstance(cached_detail.get("ipc"), list) else [],
                    "cpc": cached_detail.get("cpc") if isinstance(cached_detail.get("cpc"), list) else [],
                    "detail_fingerprint": cached_detail.get("detail_fingerprint"),
                    "detail_source": str(cached_detail.get("detail_source") or item.get("detail_source") or "").strip(),
                }
            else:
                detail = load_document_details(item)
            merged = {**item, **detail}
            details.append(merged)
            resolved_context.storage.update_ai_search_document(
                resolved_context.task_id,
                version,
                item["document_id"],
                publication_date=str(detail.get("publication_date") or item.get("publication_date") or "").strip() or None,
                application_date=str(detail.get("application_date") or item.get("application_date") or "").strip() or None,
                primary_ipc=_primary_ipc(detail, item.get("ipc_cpc_json") or []),
                detail_fingerprint=detail.get("detail_fingerprint"),
                detail_source=str(detail.get("detail_source") or item.get("detail_source") or "").strip() or None,
            )
        file_map = prepare_close_read_workspace(workspace_dir, details)
        pending_ids = [str(item.get("document_id") or "").strip() for item in pending if str(item.get("document_id") or "").strip()]
        batch_id = str((loaded_batch or {}).get("batch_id") or "").strip() or uuid.uuid4().hex
        run_id = resolved_context.active_run_id(version)
        if loaded_batch:
            resolved_context.storage.update_ai_search_batch(batch_id, workspace_dir=str(workspace_dir))
        else:
            resolved_context.storage.create_ai_search_batch(
                {
                    "batch_id": batch_id,
                    "run_id": run_id,
                    "task_id": resolved_context.task_id,
                    "plan_version": version,
                    "batch_type": "close_read",
                    "status": "loaded",
                    "workspace_dir": str(workspace_dir),
                }
            )
            resolved_context.storage.replace_ai_search_batch_documents(batch_id, run_id, pending_ids)
        resolved_context.update_task_phase(PHASE_CLOSE_READ, runtime=runtime, active_plan_version=version, run_id=run_id, active_batch_id=batch_id)
        return json.dumps(
            {
                "batch_id": batch_id,
                "plan_version": version,
                "search_elements": resolved_context.current_search_elements(version),
                "workspace_dir": str(workspace_dir),
                "documents": [
                    {
                        "document_id": item["document_id"],
                        "source_type": item.get("source_type") or "",
                        "pn": item.get("pn") or "",
                        "doi": item.get("doi") or "",
                        "venue": item.get("venue") or "",
                        "url": item.get("url") or "",
                        "title": item.get("title") or "",
                        "abstract": item.get("abstract") or "",
                        "claims": item.get("claims") or "",
                        "description": item.get("description") or "",
                        "detail_source": item.get("detail_source") or "",
                        "fulltext_path": file_map.get(str(item.get("document_id") or "").strip()),
                    }
                    for item in details
                ],
                "prompt": build_close_reader_prompt(resolved_context.current_search_elements(version), details, file_map),
            },
            ensure_ascii=False,
        )

    return [run_close_read_batch]
