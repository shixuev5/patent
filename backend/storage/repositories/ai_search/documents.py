"""AI search document and result repository methods."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.time_utils import utc_now_z

from .base import AiSearchRunLookupMixin


class AiSearchDocumentsRepositoryMixin(AiSearchRunLookupMixin):
    def _row_to_ai_search_document(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "run_id": row.get("run_id"),
            "document_id": row.get("document_id"),
            "task_id": row.get("task_id"),
            "plan_version": int(row["plan_version"]),
            "source_type": row.get("source_type"),
            "external_id": row.get("external_id"),
            "canonical_id": row.get("canonical_id"),
            "pn": row.get("pn"),
            "doi": row.get("doi"),
            "url": row.get("url"),
            "title": row.get("title"),
            "abstract": row.get("abstract"),
            "venue": row.get("venue"),
            "language": row.get("language"),
            "publication_date": row.get("publication_date"),
            "application_date": row.get("application_date"),
            "primary_ipc": row.get("primary_ipc"),
            "document_type": row.get("document_type"),
            "claim_ids_json": self._parse_metadata(row.get("claim_ids_json")),
            "evidence_locations_json": self._parse_metadata(row.get("evidence_locations_json")),
            "evidence_summary": row.get("evidence_summary"),
            "report_row_order": int(row["report_row_order"]) if row.get("report_row_order") is not None else None,
            "ipc_cpc_json": self._parse_metadata(row.get("ipc_cpc_json")),
            "source_batches_json": self._parse_metadata(row.get("source_batches_json")),
            "source_lanes_json": self._parse_metadata(row.get("source_lanes_json")),
            "source_sub_plans_json": self._parse_metadata(row.get("source_sub_plans_json")),
            "source_steps_json": self._parse_metadata(row.get("source_steps_json")),
            "stage": row.get("stage"),
            "score": float(row["score"]) if row.get("score") is not None else None,
            "agent_reason": row.get("agent_reason"),
            "key_passages_json": self._parse_metadata(row.get("key_passages_json")),
            "user_pinned": bool(int(row.get("user_pinned") or 0)),
            "user_removed": bool(int(row.get("user_removed") or 0)),
            "coarse_status": row.get("coarse_status") or "pending",
            "coarse_reason": row.get("coarse_reason"),
            "coarse_screened_at": row.get("coarse_screened_at"),
            "close_read_status": row.get("close_read_status") or "pending",
            "close_read_reason": row.get("close_read_reason"),
            "close_read_at": row.get("close_read_at"),
            "detail_fingerprint": row.get("detail_fingerprint"),
            "detail_source": row.get("detail_source"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def upsert_ai_search_documents(self, records: List[Dict[str, Any]]) -> int:
        if not records:
            return 0
        changed = 0
        now = utc_now_z()
        for record in records:
            document_id = str(record.get("document_id") or "").strip()
            run_id = str(record.get("run_id") or "").strip()
            task_id = str(record.get("task_id") or "").strip()
            plan_version = int(record.get("plan_version") or 0)
            stage = str(record.get("stage") or "").strip()
            if not run_id and task_id and plan_version > 0:
                run_id = str(self._resolve_ai_search_run_id(task_id, plan_version) or "").strip()
            if not document_id or not run_id or not task_id or plan_version <= 0 or not stage:
                continue
            changed += self._changed_rows(
                self._request(
                    """
                INSERT INTO ai_search_documents (
                    run_id, document_id, task_id, plan_version, source_type, external_id, canonical_id, pn, doi, url, title, abstract,
                    venue, language, publication_date, application_date, primary_ipc, document_type, claim_ids_json, evidence_locations_json,
                    evidence_summary, report_row_order, ipc_cpc_json, source_batches_json, source_lanes_json, source_sub_plans_json,
                    source_steps_json, stage, score, agent_reason, key_passages_json, user_pinned, user_removed, coarse_status, coarse_reason,
                    coarse_screened_at, close_read_status, close_read_reason, close_read_at, detail_fingerprint, detail_source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, document_id) DO UPDATE SET
                    source_type = excluded.source_type,
                    external_id = excluded.external_id,
                    canonical_id = excluded.canonical_id,
                    pn = excluded.pn,
                    doi = excluded.doi,
                    url = excluded.url,
                    title = excluded.title,
                    abstract = excluded.abstract,
                    venue = excluded.venue,
                    language = excluded.language,
                    publication_date = excluded.publication_date,
                    application_date = excluded.application_date,
                    primary_ipc = excluded.primary_ipc,
                    document_type = excluded.document_type,
                    claim_ids_json = excluded.claim_ids_json,
                    evidence_locations_json = excluded.evidence_locations_json,
                    evidence_summary = excluded.evidence_summary,
                    report_row_order = excluded.report_row_order,
                    ipc_cpc_json = excluded.ipc_cpc_json,
                    source_batches_json = excluded.source_batches_json,
                    source_lanes_json = excluded.source_lanes_json,
                    source_sub_plans_json = excluded.source_sub_plans_json,
                    source_steps_json = excluded.source_steps_json,
                    stage = excluded.stage,
                    score = excluded.score,
                    agent_reason = excluded.agent_reason,
                    key_passages_json = excluded.key_passages_json,
                    user_pinned = excluded.user_pinned,
                    user_removed = excluded.user_removed,
                    coarse_status = excluded.coarse_status,
                    coarse_reason = excluded.coarse_reason,
                    coarse_screened_at = excluded.coarse_screened_at,
                    close_read_status = excluded.close_read_status,
                    close_read_reason = excluded.close_read_reason,
                    close_read_at = excluded.close_read_at,
                    detail_fingerprint = excluded.detail_fingerprint,
                    detail_source = excluded.detail_source,
                    created_at = COALESCE(ai_search_documents.created_at, excluded.created_at),
                    updated_at = excluded.updated_at
                """,
                    [
                        run_id,
                        document_id,
                        task_id,
                        plan_version,
                        record.get("source_type"),
                        record.get("external_id"),
                        record.get("canonical_id"),
                        record.get("pn"),
                        record.get("doi"),
                        record.get("url"),
                        record.get("title"),
                        record.get("abstract"),
                        record.get("venue"),
                        record.get("language"),
                        record.get("publication_date"),
                        record.get("application_date"),
                        record.get("primary_ipc"),
                        record.get("document_type"),
                        self._encode_json_value(record.get("claim_ids_json") or []),
                        self._encode_json_value(record.get("evidence_locations_json") or []),
                        record.get("evidence_summary"),
                        record.get("report_row_order"),
                        self._encode_json_value(record.get("ipc_cpc_json") or []),
                        self._encode_json_value(record.get("source_batches_json") or []),
                        self._encode_json_value(record.get("source_lanes_json") or []),
                        self._encode_json_value(record.get("source_sub_plans_json") or []),
                        self._encode_json_value(record.get("source_steps_json") or []),
                        stage,
                        record.get("score"),
                        record.get("agent_reason"),
                        self._encode_json_value(record.get("key_passages_json") or []),
                        1 if record.get("user_pinned") else 0,
                        1 if record.get("user_removed") else 0,
                        str(record.get("coarse_status") or "pending"),
                        record.get("coarse_reason"),
                        record.get("coarse_screened_at"),
                        str(record.get("close_read_status") or "pending"),
                        record.get("close_read_reason"),
                        record.get("close_read_at"),
                        record.get("detail_fingerprint"),
                        record.get("detail_source"),
                        str(record.get("created_at") or now),
                        str(record.get("updated_at") or now),
                    ],
                )
            )
        return changed

    def list_ai_search_documents(
        self, task_id: str, plan_version: Any, stages: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        run_id = self._resolve_ai_search_run_id(task_id, plan_version)
        if not run_id:
            return []
        where = ["task_id = ?", "run_id = ?"]
        params: List[Any] = [task_id, run_id]
        if stages:
            placeholders = ", ".join("?" for _ in stages)
            where.append(f"stage IN ({placeholders})")
            params.extend(stages)
        rows = self._fetchall(
            f"""
            SELECT *
            FROM ai_search_documents
            WHERE {' AND '.join(where)}
            ORDER BY
                CASE stage
                    WHEN 'selected' THEN 0
                    WHEN 'shortlisted' THEN 1
                    WHEN 'candidate' THEN 2
                    WHEN 'rejected' THEN 3
                    ELSE 9
                END,
                COALESCE(score, 0) DESC,
                updated_at DESC
            """,
            params,
        )
        return [self._row_to_ai_search_document(row) for row in rows]

    def update_ai_search_document(
        self, task_id: str, plan_version: int, document_id: str, **kwargs
    ) -> bool:
        run_id = self._resolve_ai_search_run_id(task_id, plan_version)
        if not run_id:
            return False
        allowed_fields = {
            "stage",
            "score",
            "agent_reason",
            "source_type",
            "external_id",
            "canonical_id",
            "doi",
            "url",
            "key_passages_json",
            "venue",
            "language",
            "publication_date",
            "application_date",
            "primary_ipc",
            "document_type",
            "claim_ids_json",
            "evidence_locations_json",
            "evidence_summary",
            "report_row_order",
            "user_pinned",
            "user_removed",
            "title",
            "abstract",
            "source_batches_json",
            "source_lanes_json",
            "source_sub_plans_json",
            "source_steps_json",
            "ipc_cpc_json",
            "coarse_status",
            "coarse_reason",
            "coarse_screened_at",
            "close_read_status",
            "close_read_reason",
            "close_read_at",
            "detail_fingerprint",
            "detail_source",
            "updated_at",
        }
        updates = {key: kwargs[key] for key in kwargs if key in allowed_fields}
        if not updates:
            return False
        updates.setdefault("updated_at", utc_now_z())
        for key in list(updates.keys()):
            if key.endswith("_json"):
                updates[key] = self._encode_json_value(updates[key])
            elif key in {"user_pinned", "user_removed"}:
                updates[key] = 1 if updates[key] else 0
        set_clause = ", ".join(f"{key} = ?" for key in updates.keys())
        return self._changed_rows(
            self._request(
                f"UPDATE ai_search_documents SET {set_clause} WHERE task_id = ? AND run_id = ? AND document_id = ?",
                [*updates.values(), task_id, run_id, document_id],
            )
        ) > 0
