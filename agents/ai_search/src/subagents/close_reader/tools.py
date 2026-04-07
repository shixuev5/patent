"""Close-reader specialist tools."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.stage_limits import DEFAULT_KEY_PASSAGES_LIMIT, DEFAULT_SHORTLIST_LIMIT
from agents.ai_search.src.subagents.close_reader.passages import collect_key_terms, fallback_passages
from agents.ai_search.src.subagents.close_reader.prompt import build_close_reader_prompt
from agents.ai_search.src.subagents.close_reader.workspace import (
    detail_to_text,
    load_document_details,
    prepare_close_read_workspace,
)
from agents.ai_search.src.runtime import extract_json_object
from backend.time_utils import utc_now_z


def build_close_reader_tools(context: Any) -> List[Any]:
    def run_close_read_batch(
        operation: str = "load",
        payload_json: str = "",
        plan_version: int = 0,
        limit: int = DEFAULT_SHORTLIST_LIMIT,
        runtime: ToolRuntime = None,
    ) -> str:
        """执行精读领域动作：准备精读批次，或提交精读结果。"""
        version = int(plan_version or context.active_plan_version() or 0)
        op = str(operation or "load").strip().lower()
        try:
            if op != "commit":
                records = context.storage.list_ai_search_documents(context.task_id, version)
                pending = [
                    item
                    for item in records
                    if str(item.get("coarse_status") or "") == "kept" and str(item.get("close_read_status") or "pending") == "pending"
                ][: max(int(limit or DEFAULT_SHORTLIST_LIMIT), 1)]
                details: List[Dict[str, Any]] = []
                task = context.storage.get_task(context.task_id)
                workspace_root = Path(str(getattr(task, "output_dir", "") or Path.cwd() / ".ai_search" / context.task_id))
                workspace_dir = workspace_root / "close_read" / f"plan_{version}"
                workspace_manifest_path = workspace_dir / "manifest.json"
                cached_manifest: Dict[str, Any] = {}
                if workspace_manifest_path.exists() and workspace_manifest_path.is_file():
                    try:
                        cached_manifest = json.loads(workspace_manifest_path.read_text(encoding="utf-8"))
                    except Exception:
                        cached_manifest = {}
                for item in pending:
                    pn = str(item.get("pn") or "").strip().upper()
                    cached_detail = cached_manifest.get(pn) if isinstance(cached_manifest, dict) else None
                    if isinstance(cached_detail, dict):
                        detail = {
                            "pn": pn,
                            "title": str(cached_detail.get("title") or item.get("title") or "").strip(),
                            "abstract": str(cached_detail.get("abstract") or item.get("abstract") or "").strip(),
                            "claims": str(cached_detail.get("claims") or "").strip(),
                            "description": str(cached_detail.get("description") or "").strip(),
                            "detail_fingerprint": cached_detail.get("detail_fingerprint"),
                        }
                    else:
                        detail = load_document_details(pn)
                    merged = {**item, **detail}
                    details.append(merged)
                    context.storage.update_ai_search_document(
                        context.task_id,
                        version,
                        item["document_id"],
                        detail_fingerprint=detail.get("detail_fingerprint"),
                    )
                file_map = prepare_close_read_workspace(workspace_dir, details)
                claim_context = {
                    "claim_decomposition": context.latest_message_metadata("claim_decomposition"),
                    "claim_search_strategy": context.latest_message_metadata("claim_search_strategy"),
                }
                pending_ids = [str(item.get("document_id") or "").strip() for item in pending if str(item.get("document_id") or "").strip()]
                context.update_todos(
                    "close_read",
                    "in_progress",
                    current_task="close_read",
                    resume_from="run_close_read_batch.commit",
                    state_updates={
                        "workspace_dir": str(workspace_dir),
                        "pending_document_ids": pending_ids,
                        "plan_version": version,
                        "workspace_manifest": str(workspace_manifest_path),
                    },
                )
                return json.dumps(
                    {
                        "plan_version": version,
                        "search_elements": context.current_search_elements(version),
                        "claim_context": claim_context,
                        "workspace_dir": str(workspace_dir),
                        "documents": [
                            {
                                "document_id": item["document_id"],
                                "pn": item["pn"],
                                "title": item.get("title") or "",
                                "abstract": item.get("abstract") or "",
                                "claims": item.get("claims") or "",
                                "description": item.get("description") or "",
                                "fulltext_path": file_map.get(str(item.get("pn") or "").strip().upper()),
                            }
                            for item in details
                        ],
                        "prompt": build_close_reader_prompt(context.current_search_elements(version), details, file_map, claim_context),
                    },
                    ensure_ascii=False,
                )

            if not str(payload_json or "").strip():
                raise ValueError("run_close_read_batch 在 commit 模式下必须提供 payload_json。")
            payload = extract_json_object(payload_json)
            selected_ids = {str(item).strip() for item in (payload.get("selected") or []) if str(item).strip()}
            rejected_ids = {str(item).strip() for item in (payload.get("rejected") or []) if str(item).strip()}
            passages_by_doc: Dict[str, List[Dict[str, Any]]] = {}
            claim_alignments_by_doc: Dict[str, List[Dict[str, Any]]] = {}
            assessments_by_doc: Dict[str, Dict[str, Any]] = {}
            for item in payload.get("key_passages") or []:
                if not isinstance(item, dict):
                    continue
                document_id = str(item.get("document_id") or "").strip()
                if not document_id:
                    continue
                passages_by_doc.setdefault(document_id, []).append(
                    {
                        "passage": str(item.get("passage") or "")[:400],
                        "reason": str(item.get("reason") or "").strip(),
                        "location": item.get("location"),
                        "paragraph_type": str(item.get("paragraph_type") or "").strip(),
                        "hit_terms": item.get("hit_terms") or [],
                        "hit_density": item.get("hit_density"),
                    }
                )
            for item in payload.get("claim_alignments") or []:
                if not isinstance(item, dict):
                    continue
                document_id = str(item.get("document_id") or "").strip()
                if not document_id:
                    continue
                claim_alignments_by_doc.setdefault(document_id, []).append(
                    {
                        "claim_id": str(item.get("claim_id") or "").strip(),
                        "limitation_id": str(item.get("limitation_id") or "").strip(),
                        "passage": str(item.get("passage") or "")[:400],
                        "support": str(item.get("support") or "").strip(),
                        "reason": str(item.get("reason") or "").strip(),
                        "location": item.get("location"),
                    }
                )
            for item in payload.get("document_assessments") or []:
                if not isinstance(item, dict):
                    continue
                document_id = str(item.get("document_id") or "").strip()
                if not document_id:
                    continue
                assessments_by_doc[document_id] = {
                    "decision": str(item.get("decision") or "").strip(),
                    "confidence": float(item.get("confidence") or 0.0),
                    "evidence_sufficiency": str(item.get("evidence_sufficiency") or "").strip(),
                    "missing_evidence": item.get("missing_evidence") or [],
                }
            terms = collect_key_terms(context.current_search_elements(version))
            current_records = context.storage.list_ai_search_documents(context.task_id, version)
            selected_count = 0
            for item in current_records:
                document_id = str(item.get("document_id") or "")
                if str(item.get("coarse_status") or "") != "kept":
                    continue
                if str(item.get("close_read_status") or "pending") != "pending":
                    continue
                passages = passages_by_doc.get(document_id)
                if not passages:
                    detail = load_document_details(str(item.get("pn") or "").strip().upper())
                    passages = [
                        {**passage, "document_id": document_id}
                        for passage in fallback_passages(detail_to_text(detail), terms)[:DEFAULT_KEY_PASSAGES_LIMIT]
                    ]
                alignments = claim_alignments_by_doc.get(document_id) or []
                if alignments:
                    passages = [{**passage, "claim_alignments": alignments} for passage in passages]
                assessment = assessments_by_doc.get(document_id)
                if assessment:
                    passages = [{**passage, "assessment": assessment} for passage in passages]
                if document_id in selected_ids:
                    context.storage.update_ai_search_document(
                        context.task_id,
                        version,
                        document_id,
                        stage="selected",
                        key_passages_json=passages,
                        agent_reason="纳入对比文件",
                        close_read_status="selected",
                        close_read_reason="精读后纳入对比文件",
                        close_read_at=utc_now_z(),
                    )
                    selected_count += 1
                elif document_id in rejected_ids:
                    context.storage.update_ai_search_document(
                        context.task_id,
                        version,
                        document_id,
                        stage="rejected",
                        key_passages_json=passages,
                        agent_reason="精读后排除",
                        close_read_status="rejected",
                        close_read_reason="精读后排除",
                        close_read_at=utc_now_z(),
                    )
            context.update_todos(
                "close_read",
                "completed",
                current_task="generate_feature_table",
                state_updates={"selected_count": selected_count, "document_assessments": assessments_by_doc},
            )
            context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": context.task_id,
                    "plan_version": version or None,
                    "role": "assistant",
                    "kind": "close_read_result",
                    "content": str(payload.get("coverage_summary") or payload.get("selection_summary") or "").strip() or None,
                    "stream_status": "completed",
                    "metadata": payload,
                }
            )
            context.notify_snapshot_changed(runtime, reason="selection")
            return json.dumps({"selected_count": selected_count}, ensure_ascii=False)
        except Exception as exc:
            return context.record_todo_failure("close_read", str(exc), current_task="close_read", resume_from="run_close_read_batch")

    return [run_close_read_batch]
