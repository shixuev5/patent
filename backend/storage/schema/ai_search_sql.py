"""AI search table and index DDL."""

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

CREATE TABLE IF NOT EXISTS ai_search_stream_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    run_id TEXT,
    event_type TEXT NOT NULL,
    entity_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_search_stream_events_session_seq
ON ai_search_stream_events(session_id, seq);

CREATE INDEX IF NOT EXISTS idx_ai_search_stream_events_task_seq
ON ai_search_stream_events(task_id, seq);

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

CREATE TABLE IF NOT EXISTS ai_search_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    selected_document_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS ai_search_documents (
    run_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    source_type TEXT,
    external_id TEXT,
    canonical_id TEXT,
    pn TEXT,
    doi TEXT,
    url TEXT,
    title TEXT,
    abstract TEXT,
    venue TEXT,
    language TEXT,
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
    detail_source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, document_id)
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
CREATE INDEX IF NOT EXISTS idx_ai_search_runs_task_updated ON ai_search_runs(task_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_ai_search_runs_task_plan ON ai_search_runs(task_id, plan_version, updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_search_documents_run_document ON ai_search_documents(run_id, document_id);
CREATE INDEX IF NOT EXISTS idx_ai_search_documents_run_stage ON ai_search_documents(run_id, stage, updated_at);
CREATE INDEX IF NOT EXISTS idx_ai_search_documents_task_plan_stage ON ai_search_documents(task_id, plan_version, stage, updated_at);
CREATE INDEX IF NOT EXISTS idx_ai_search_documents_run_pn ON ai_search_documents(run_id, pn);
CREATE INDEX IF NOT EXISTS idx_ai_search_documents_run_canonical ON ai_search_documents(run_id, canonical_id);
CREATE INDEX IF NOT EXISTS idx_ai_search_checkpoints_thread_created ON ai_search_checkpoints(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_search_checkpoint_writes_thread_checkpoint ON ai_search_checkpoint_writes(thread_id, checkpoint_ns, checkpoint_id);
"""
