"""精读子代理工具。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.exceptions import ExecutionQueueTakeoverRequested
from agents.ai_search.src.stage_limits import DEFAULT_KEY_PASSAGES_LIMIT, DEFAULT_SHORTLIST_LIMIT
from agents.ai_search.src.state import PHASE_CLOSE_READ
from agents.ai_search.src.subagents.close_reader.passages import collect_key_terms, fallback_passages
from agents.ai_search.src.subagents.close_reader.prompt import build_close_reader_prompt
from agents.ai_search.src.subagents.close_reader.workspace import (
    detail_to_text,
    load_document_details,
    prepare_close_read_workspace,
)
from agents.ai_search.src.runtime import extract_json_object
from backend.time_utils import utc_now_z


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


def _format_evidence_location(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("paragraph_"):
        raw_number = text.split("_", 1)[1]
        try:
            return f"说明书第{int(raw_number):02d}段"
        except Exception:
            return text
    return text


def _sorted_claim_ids(values: List[str]) -> List[str]:
    unique = {str(value or "").strip() for value in values if str(value or "").strip()}
    return sorted(unique, key=lambda item: (0, int(item)) if item.isdigit() else (1, item))


def _summarize_evidence_locations(values: List[str]) -> str:
    ordered = []
    seen = set()
    for value in values:
        text = _format_evidence_location(value)
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)
    return "；".join(ordered[:4])


def build_close_reader_tools(context: Any) -> List[Any]:
    def _resolve_loaded_batch(version: int) -> tuple[Dict[str, Any] | None, List[str]]:
        run = context.active_run(version)
        active_batch_id = str(run.get("active_batch_id") or "").strip() if isinstance(run, dict) else ""
        if not active_batch_id:
            return None, []
        batch = context.storage.get_ai_search_batch(active_batch_id)
        if not batch or str(batch.get("batch_type") or "") != "close_read":
            return None, []
        if str(batch.get("status") or "") != "loaded":
            return None, []
        pending_ids = [
            str(item or "").strip()
            for item in context.storage.list_ai_search_batch_documents(active_batch_id)
            if str(item or "").strip()
        ]
        return batch, pending_ids

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
                loaded_batch, loaded_ids = _resolve_loaded_batch(version)
                loaded_id_set = set(loaded_ids)
                pending = [
                    item
                    for item in records
                    if str(item.get("coarse_status") or "") == "kept" and str(item.get("close_read_status") or "pending") == "pending"
                    and (not loaded_id_set or str(item.get("document_id") or "").strip() in loaded_id_set)
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
                    context.storage.update_ai_search_document(
                        context.task_id,
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
                run_id = context.active_run_id(version)
                if loaded_batch:
                    context.storage.update_ai_search_batch(batch_id, workspace_dir=str(workspace_dir))
                else:
                    context.storage.create_ai_search_batch(
                        {
                            "batch_id": batch_id,
                            "run_id": run_id,
                            "task_id": context.task_id,
                            "plan_version": version,
                            "batch_type": "close_read",
                            "status": "loaded",
                            "workspace_dir": str(workspace_dir),
                        }
                    )
                    context.storage.replace_ai_search_batch_documents(batch_id, run_id, pending_ids)
                context.update_task_phase(PHASE_CLOSE_READ, runtime=runtime, active_plan_version=version, run_id=run_id, active_batch_id=batch_id)
                return json.dumps(
                    {
                        "batch_id": batch_id,
                        "plan_version": version,
                        "search_elements": context.current_search_elements(version),
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
                        "prompt": build_close_reader_prompt(context.current_search_elements(version), details, file_map),
                    },
                    ensure_ascii=False,
                )

            if not str(payload_json or "").strip():
                raise ValueError("run_close_read_batch 在 commit 模式下必须提供 payload_json。")
            payload = extract_json_object(payload_json)
            batch_id = str(payload.get("batch_id") or "").strip()
            batch = context.storage.get_ai_search_batch(batch_id)
            if not batch or str(batch.get("batch_type") or "") != "close_read":
                raise ValueError("close_read commit 缺少有效 batch_id。")
            if str(batch.get("status") or "") == "committed":
                raise ValueError("close_read batch 已提交，不能重复提交。")
            manual_review_batch = str(batch.get("input_hash") or "").strip().startswith("manual_review")
            selected_ids = {str(item).strip() for item in (payload.get("selected") or []) if str(item).strip()}
            rejected_ids = {str(item).strip() for item in (payload.get("rejected") or []) if str(item).strip()}
            current_records = context.storage.list_ai_search_documents(context.task_id, version)
            pending_ids = set(context.storage.list_ai_search_batch_documents(batch_id))
            overlap_ids = selected_ids & rejected_ids
            unresolved_ids = pending_ids - selected_ids - rejected_ids
            unknown_ids = (selected_ids | rejected_ids) - pending_ids
            if overlap_ids:
                raise ValueError(f"close_read 结果中存在重复 document_id: {', '.join(sorted(overlap_ids))}")
            if unresolved_ids:
                raise ValueError(f"close_read 结果遗漏了待处理 document_id: {', '.join(sorted(unresolved_ids))}")
            if unknown_ids:
                raise ValueError(f"close_read 结果包含非待处理 document_id: {', '.join(sorted(unknown_ids))}")
            passages_by_doc: Dict[str, List[Dict[str, Any]]] = {}
            assessments_by_doc: Dict[str, Dict[str, Any]] = {}
            claim_ids_by_doc: Dict[str, List[str]] = {}
            locations_by_doc: Dict[str, List[str]] = {}
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
                location = str(item.get("location") or "").strip()
                if location:
                    locations_by_doc.setdefault(document_id, []).append(location)
            for item in payload.get("claim_alignments") or []:
                if not isinstance(item, dict):
                    continue
                document_id = str(item.get("document_id") or "").strip()
                claim_id = str(item.get("claim_id") or "").strip()
                location = str(item.get("location") or "").strip()
                if document_id and claim_id:
                    claim_ids_by_doc.setdefault(document_id, []).append(claim_id)
                if document_id and location:
                    locations_by_doc.setdefault(document_id, []).append(location)
            for item in payload.get("limitation_coverage") or []:
                if not isinstance(item, dict):
                    continue
                claim_id = str(item.get("claim_id") or "").strip()
                supporting_ids = item.get("supporting_document_ids") if isinstance(item.get("supporting_document_ids"), list) else []
                for document_id in supporting_ids:
                    doc_id = str(document_id or "").strip()
                    if doc_id and claim_id:
                        claim_ids_by_doc.setdefault(doc_id, []).append(claim_id)
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
            selected_count = 0
            for item in current_records:
                document_id = str(item.get("document_id") or "")
                if str(item.get("coarse_status") or "") != "kept":
                    continue
                if str(item.get("close_read_status") or "pending") != "pending":
                    continue
                passages = passages_by_doc.get(document_id)
                if not passages:
                    detail = load_document_details(item)
                    passages = [
                        {**passage, "document_id": document_id}
                        for passage in fallback_passages(detail_to_text(detail), terms)[:DEFAULT_KEY_PASSAGES_LIMIT]
                    ]
                assessment = assessments_by_doc.get(document_id)
                if assessment:
                    passages = [{**passage, "assessment": assessment} for passage in passages]
                claim_ids = _sorted_claim_ids(claim_ids_by_doc.get(document_id, []))
                evidence_locations = []
                for passage in passages:
                    location = str(passage.get("location") or "").strip()
                    if location:
                        evidence_locations.append(location)
                evidence_locations.extend(locations_by_doc.get(document_id, []))
                evidence_summary = _summarize_evidence_locations(evidence_locations)
                if document_id in selected_ids:
                    context.storage.update_ai_search_document(
                        context.task_id,
                        version,
                        document_id,
                        stage="selected",
                        key_passages_json=passages,
                        claim_ids_json=claim_ids,
                        evidence_locations_json=evidence_locations,
                        evidence_summary=evidence_summary,
                        agent_reason="纳入对比文件",
                        close_read_status="selected",
                        close_read_reason="精读后纳入对比文件",
                        close_read_at=utc_now_z(),
                    )
                    context.storage.create_ai_search_document_decision(
                        {
                            "decision_id": uuid.uuid4().hex,
                            "run_id": str(batch.get("run_id") or ""),
                            "batch_id": batch_id,
                            "task_id": context.task_id,
                            "plan_version": version,
                            "document_id": document_id,
                            "decision_stage": "close_read",
                            "decision": "selected",
                            "reason": "精读后纳入对比文件",
                            "metadata": {"assessment": assessment or {}},
                        }
                    )
                    selected_count += 1
                elif document_id in rejected_ids:
                    context.storage.update_ai_search_document(
                        context.task_id,
                        version,
                        document_id,
                        stage="shortlisted" if manual_review_batch else "rejected",
                        key_passages_json=passages,
                        claim_ids_json=claim_ids,
                        evidence_locations_json=evidence_locations,
                        evidence_summary=evidence_summary,
                        agent_reason="人工送审复核未通过" if manual_review_batch else "精读后排除",
                        close_read_status="rejected",
                        close_read_reason="人工送审复核未通过" if manual_review_batch else "精读后排除",
                        close_read_at=utc_now_z(),
                    )
                    context.storage.create_ai_search_document_decision(
                        {
                            "decision_id": uuid.uuid4().hex,
                            "run_id": str(batch.get("run_id") or ""),
                            "batch_id": batch_id,
                            "task_id": context.task_id,
                            "plan_version": version,
                            "document_id": document_id,
                            "decision_stage": "close_read",
                            "decision": "review_rejected" if manual_review_batch else "rejected",
                            "reason": "人工送审复核未通过" if manual_review_batch else "精读后排除",
                            "metadata": {"assessment": assessment or {}},
                        }
                    )
            context.storage.create_ai_search_close_read_result(
                {
                    "result_id": uuid.uuid4().hex,
                    "run_id": str(batch.get("run_id") or ""),
                    "batch_id": batch_id,
                    "task_id": context.task_id,
                    "plan_version": version,
                    "document_assessments": payload.get("document_assessments") or [],
                    "key_passages": payload.get("key_passages") or [],
                    "claim_alignments": payload.get("claim_alignments") or [],
                    "limitation_coverage": payload.get("limitation_coverage") or [],
                    "limitation_gaps": payload.get("limitation_gaps") or [],
                }
            )
            context.storage.update_ai_search_batch(batch_id, status="committed", committed_at=utc_now_z())
            context.update_task_phase(PHASE_CLOSE_READ, runtime=runtime, active_plan_version=version, run_id=str(batch.get("run_id") or ""), active_batch_id=batch_id)
            context.notify_snapshot_changed(runtime, reason="selection")
            takeover = context.consume_execution_message_queue_for_takeover(runtime=runtime)
            if takeover is not None:
                raise takeover
            return json.dumps({"selected_count": selected_count}, ensure_ascii=False)
        except ExecutionQueueTakeoverRequested:
            raise
        except Exception as exc:
            return context.record_todo_failure("close_read", str(exc), current_task="close_read", resume_from="run_close_read_batch")

    return [run_close_read_batch]
