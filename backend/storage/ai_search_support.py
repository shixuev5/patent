"""
AI search storage helpers shared by SQLite and D1 backends.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, Optional, Tuple


AI_SEARCH_STORAGE_SQL = """
CREATE TABLE IF NOT EXISTS ai_search_messages (
    message_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    plan_version INTEGER,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT,
    stream_status TEXT,
    question_id TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_search_plans (
    task_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    review_markdown TEXT NOT NULL,
    execution_spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    superseded_at TEXT,
    PRIMARY KEY (task_id, plan_version)
);

CREATE TABLE IF NOT EXISTS ai_search_documents (
    document_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    pn TEXT,
    title TEXT,
    abstract TEXT,
    publication_date TEXT,
    application_date TEXT,
    primary_ipc TEXT,
    document_type TEXT,
    claim_ids_json TEXT,
    evidence_locations_json TEXT,
    evidence_summary TEXT,
    report_row_order INTEGER,
    ipc_cpc_json TEXT,
    source_batches_json TEXT,
    source_lanes_json TEXT,
    source_sub_plans_json TEXT,
    source_steps_json TEXT,
    stage TEXT NOT NULL,
    score REAL,
    agent_reason TEXT,
    key_passages_json TEXT,
    user_pinned INTEGER NOT NULL DEFAULT 0,
    user_removed INTEGER NOT NULL DEFAULT 0,
    coarse_status TEXT NOT NULL DEFAULT 'pending',
    coarse_reason TEXT,
    coarse_screened_at TEXT,
    close_read_status TEXT NOT NULL DEFAULT 'pending',
    close_read_reason TEXT,
    close_read_at TEXT,
    detail_fingerprint TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_search_feature_comparisons (
    feature_comparison_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    table_json TEXT,
    summary_markdown TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_search_checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    checkpoint_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS ai_search_checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    write_idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    typed_value_json TEXT NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx)
);

CREATE TABLE IF NOT EXISTS ai_search_checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL,
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    typed_value_json TEXT NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE INDEX IF NOT EXISTS idx_ai_search_messages_task_created ON ai_search_messages(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_search_messages_task_question ON ai_search_messages(task_id, question_id);
CREATE INDEX IF NOT EXISTS idx_ai_search_plans_task_created ON ai_search_plans(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_search_documents_task_plan_stage ON ai_search_documents(task_id, plan_version, stage);
CREATE INDEX IF NOT EXISTS idx_ai_search_documents_task_plan_pn ON ai_search_documents(task_id, plan_version, pn);
CREATE INDEX IF NOT EXISTS idx_ai_search_feature_comparisons_task_plan_updated ON ai_search_feature_comparisons(task_id, plan_version, updated_at);
CREATE INDEX IF NOT EXISTS idx_ai_search_checkpoints_thread_created ON ai_search_checkpoints(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_search_checkpoint_writes_thread_checkpoint ON ai_search_checkpoint_writes(thread_id, checkpoint_ns, checkpoint_id);
"""


def stable_ai_search_document_id(
    task_id: str,
    plan_version: int,
    pn: Optional[str],
    *,
    fallback_seed: Optional[str] = None,
) -> str:
    seed = f"{task_id}:{plan_version}:{(pn or fallback_seed or '').strip().upper()}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def encode_typed_value(value: Tuple[str, bytes]) -> str:
    kind, payload = value
    return json.dumps(
        {
            "type": kind,
            "data": base64.b64encode(payload).decode("ascii"),
        },
        ensure_ascii=False,
    )


def decode_typed_value(raw: Any) -> Tuple[str, bytes]:
    if isinstance(raw, tuple) and len(raw) == 2:
        kind, payload = raw
        if isinstance(kind, str) and isinstance(payload, (bytes, bytearray)):
            return kind, bytes(payload)
    if not isinstance(raw, str):
        raise ValueError("typed value payload must be a JSON string")
    data = json.loads(raw)
    kind = str(data.get("type") or "")
    payload_b64 = str(data.get("data") or "")
    return kind, base64.b64decode(payload_b64.encode("ascii"))
