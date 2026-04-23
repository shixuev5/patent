"""Shared storage DDL used by SQLite and D1 backends."""

from .ai_search_sql import AI_SEARCH_STORAGE_SQL


REQUIRED_COLUMNS = {
    "tasks": [
        ("owner_id", "owner_id TEXT"),
        ("task_type", "task_type TEXT NOT NULL DEFAULT 'patent_analysis'"),
        ("pn", "pn TEXT"),
        ("title", "title TEXT"),
        ("status", "status TEXT NOT NULL DEFAULT 'pending'"),
        ("progress", "progress INTEGER DEFAULT 0"),
        ("current_step", "current_step TEXT"),
        ("output_dir", "output_dir TEXT"),
        ("error_message", "error_message TEXT"),
        ("created_at", "created_at TEXT NOT NULL"),
        ("updated_at", "updated_at TEXT NOT NULL"),
        ("completed_at", "completed_at TEXT"),
        ("deleted_at", "deleted_at TEXT"),
        ("metadata", "metadata TEXT"),
    ],
    "patent_analyses": [
        ("first_completed_at", "first_completed_at TEXT NOT NULL"),
        ("sha256", "sha256 TEXT"),
    ],
    "users": [
        ("owner_id", "owner_id TEXT PRIMARY KEY"),
        ("authing_sub", "authing_sub TEXT NOT NULL UNIQUE"),
        ("role", "role TEXT"),
        ("name", "name TEXT"),
        ("nickname", "nickname TEXT"),
        ("email", "email TEXT"),
        ("phone", "phone TEXT"),
        ("picture", "picture TEXT"),
        ("notification_email_enabled", "notification_email_enabled INTEGER NOT NULL DEFAULT 0"),
        ("work_notification_email", "work_notification_email TEXT"),
        ("personal_notification_email", "personal_notification_email TEXT"),
        ("raw_profile", "raw_profile TEXT"),
        ("created_at", "created_at TEXT NOT NULL"),
        ("updated_at", "updated_at TEXT NOT NULL"),
        ("last_login_at", "last_login_at TEXT NOT NULL"),
    ],
    "account_month_targets": [
        ("owner_id", "owner_id TEXT NOT NULL"),
        ("year", "year INTEGER NOT NULL"),
        ("month", "month INTEGER NOT NULL"),
        ("target_count", "target_count INTEGER NOT NULL"),
        ("created_at", "created_at TEXT NOT NULL"),
        ("updated_at", "updated_at TEXT NOT NULL"),
    ],
    "task_llm_usage": [
        ("task_id", "task_id TEXT PRIMARY KEY"),
        ("owner_id", "owner_id TEXT NOT NULL"),
        ("task_type", "task_type TEXT NOT NULL"),
        ("task_status", "task_status TEXT"),
        ("prompt_tokens", "prompt_tokens INTEGER NOT NULL DEFAULT 0"),
        ("completion_tokens", "completion_tokens INTEGER NOT NULL DEFAULT 0"),
        ("total_tokens", "total_tokens INTEGER NOT NULL DEFAULT 0"),
        ("reasoning_tokens", "reasoning_tokens INTEGER NOT NULL DEFAULT 0"),
        ("llm_call_count", "llm_call_count INTEGER NOT NULL DEFAULT 0"),
        ("estimated_cost_cny", "estimated_cost_cny REAL NOT NULL DEFAULT 0"),
        ("price_missing", "price_missing INTEGER NOT NULL DEFAULT 0"),
        ("model_breakdown_json", "model_breakdown_json TEXT"),
        ("first_usage_at", "first_usage_at TEXT"),
        ("last_usage_at", "last_usage_at TEXT"),
        ("currency", "currency TEXT NOT NULL DEFAULT 'CNY'"),
        ("created_at", "created_at TEXT NOT NULL"),
        ("updated_at", "updated_at TEXT NOT NULL"),
    ],
    "system_logs": [
        ("log_id", "log_id TEXT PRIMARY KEY"),
        ("timestamp", "timestamp TEXT NOT NULL"),
        ("category", "category TEXT NOT NULL"),
        ("event_name", "event_name TEXT NOT NULL"),
        ("level", "level TEXT NOT NULL"),
        ("owner_id", "owner_id TEXT"),
        ("task_id", "task_id TEXT"),
        ("task_type", "task_type TEXT"),
        ("request_id", "request_id TEXT"),
        ("trace_id", "trace_id TEXT"),
        ("method", "method TEXT"),
        ("path", "path TEXT"),
        ("status_code", "status_code INTEGER"),
        ("duration_ms", "duration_ms INTEGER"),
        ("provider", "provider TEXT"),
        ("target_host", "target_host TEXT"),
        ("success", "success INTEGER NOT NULL DEFAULT 0"),
        ("message", "message TEXT"),
        ("payload_inline_json", "payload_inline_json TEXT"),
        ("payload_file_path", "payload_file_path TEXT"),
        ("payload_bytes", "payload_bytes INTEGER NOT NULL DEFAULT 0"),
        ("payload_overflow", "payload_overflow INTEGER NOT NULL DEFAULT 0"),
        ("created_at", "created_at TEXT NOT NULL"),
    ],
    "refresh_sessions": [
        ("token_hash", "token_hash TEXT PRIMARY KEY"),
        ("owner_id", "owner_id TEXT NOT NULL"),
        ("expires_at", "expires_at TEXT NOT NULL"),
        ("created_at", "created_at TEXT NOT NULL"),
        ("updated_at", "updated_at TEXT NOT NULL"),
        ("revoked_at", "revoked_at TEXT"),
        ("replaced_by_token_hash", "replaced_by_token_hash TEXT"),
    ],
    "wechat_bindings": [
        ("binding_id", "binding_id TEXT PRIMARY KEY"),
        ("owner_id", "owner_id TEXT NOT NULL"),
        ("status", "status TEXT NOT NULL"),
        ("bot_account_id", "bot_account_id TEXT"),
        ("wechat_user_id", "wechat_user_id TEXT"),
        ("wechat_display_name", "wechat_display_name TEXT"),
        ("delivery_peer_id", "delivery_peer_id TEXT"),
        ("delivery_peer_name", "delivery_peer_name TEXT"),
        ("push_task_completed", "push_task_completed INTEGER NOT NULL DEFAULT 1"),
        ("push_task_failed", "push_task_failed INTEGER NOT NULL DEFAULT 1"),
        ("push_ai_search_pending_action", "push_ai_search_pending_action INTEGER NOT NULL DEFAULT 1"),
        ("bound_at", "bound_at TEXT"),
        ("disconnected_at", "disconnected_at TEXT"),
        ("last_inbound_at", "last_inbound_at TEXT"),
        ("last_outbound_at", "last_outbound_at TEXT"),
        ("created_at", "created_at TEXT NOT NULL"),
        ("updated_at", "updated_at TEXT NOT NULL"),
    ],
    "wechat_login_sessions": [
        ("login_session_id", "login_session_id TEXT PRIMARY KEY"),
        ("owner_id", "owner_id TEXT NOT NULL"),
        ("status", "status TEXT NOT NULL"),
        ("qr_url", "qr_url TEXT"),
        ("expires_at", "expires_at TEXT NOT NULL"),
        ("bot_account_id", "bot_account_id TEXT"),
        ("wechat_user_id", "wechat_user_id TEXT"),
        ("wechat_display_name", "wechat_display_name TEXT"),
        ("error_message", "error_message TEXT"),
        ("online_at", "online_at TEXT"),
        ("created_at", "created_at TEXT NOT NULL"),
        ("updated_at", "updated_at TEXT NOT NULL"),
    ],
    "wechat_flow_sessions": [
        ("flow_session_id", "flow_session_id TEXT PRIMARY KEY"),
        ("owner_id", "owner_id TEXT NOT NULL"),
        ("flow_type", "flow_type TEXT NOT NULL"),
        ("status", "status TEXT NOT NULL"),
        ("current_step", "current_step TEXT"),
        ("draft_payload_json", "draft_payload_json TEXT"),
        ("expires_at", "expires_at TEXT"),
        ("created_at", "created_at TEXT NOT NULL"),
        ("updated_at", "updated_at TEXT NOT NULL"),
    ],
    "wechat_conversation_sessions": [
        ("conversation_id", "conversation_id TEXT PRIMARY KEY"),
        ("owner_id", "owner_id TEXT NOT NULL"),
        ("binding_id", "binding_id TEXT NOT NULL"),
        ("peer_id", "peer_id TEXT NOT NULL"),
        ("peer_name", "peer_name TEXT"),
        ("status", "status TEXT NOT NULL"),
        ("active_context_kind", "active_context_kind TEXT NOT NULL DEFAULT 'none'"),
        ("active_context_session_id", "active_context_session_id TEXT"),
        ("active_context_title", "active_context_title TEXT"),
        ("memory_json", "memory_json TEXT"),
        ("last_inbound_at", "last_inbound_at TEXT"),
        ("last_outbound_at", "last_outbound_at TEXT"),
        ("created_at", "created_at TEXT NOT NULL"),
        ("updated_at", "updated_at TEXT NOT NULL"),
    ],
    "wechat_delivery_jobs": [
        ("delivery_job_id", "delivery_job_id TEXT PRIMARY KEY"),
        ("owner_id", "owner_id TEXT NOT NULL"),
        ("binding_id", "binding_id TEXT"),
        ("task_id", "task_id TEXT"),
        ("event_type", "event_type TEXT NOT NULL"),
        ("status", "status TEXT NOT NULL"),
        ("delivery_stage", "delivery_stage TEXT NOT NULL DEFAULT 'queued'"),
        ("payload_json", "payload_json TEXT"),
        ("stage_details_json", "stage_details_json TEXT"),
        ("attempt_count", "attempt_count INTEGER NOT NULL DEFAULT 0"),
        ("max_attempts", "max_attempts INTEGER NOT NULL DEFAULT 3"),
        ("next_attempt_at", "next_attempt_at TEXT"),
        ("claimed_at", "claimed_at TEXT"),
        ("completed_at", "completed_at TEXT"),
        ("failed_at", "failed_at TEXT"),
        ("last_error", "last_error TEXT"),
        ("created_at", "created_at TEXT NOT NULL"),
        ("updated_at", "updated_at TEXT NOT NULL"),
    ],
    "ai_search_documents": [
        ("run_id", "run_id TEXT"),
        ("source_type", "source_type TEXT"),
        ("external_id", "external_id TEXT"),
        ("canonical_id", "canonical_id TEXT"),
        ("publication_date", "publication_date TEXT"),
        ("doi", "doi TEXT"),
        ("url", "url TEXT"),
        ("venue", "venue TEXT"),
        ("language", "language TEXT"),
        ("application_date", "application_date TEXT"),
        ("primary_ipc", "primary_ipc TEXT"),
        ("document_type", "document_type TEXT"),
        ("claim_ids_json", "claim_ids_json TEXT"),
        ("evidence_locations_json", "evidence_locations_json TEXT"),
        ("evidence_summary", "evidence_summary TEXT"),
        ("report_row_order", "report_row_order INTEGER"),
        ("source_lanes_json", "source_lanes_json TEXT"),
        ("source_sub_plans_json", "source_sub_plans_json TEXT"),
        ("source_steps_json", "source_steps_json TEXT"),
        ("coarse_status", "coarse_status TEXT NOT NULL DEFAULT 'pending'"),
        ("coarse_reason", "coarse_reason TEXT"),
        ("coarse_screened_at", "coarse_screened_at TEXT"),
        ("close_read_status", "close_read_status TEXT NOT NULL DEFAULT 'pending'"),
        ("close_read_reason", "close_read_reason TEXT"),
        ("close_read_at", "close_read_at TEXT"),
        ("detail_fingerprint", "detail_fingerprint TEXT"),
        ("detail_source", "detail_source TEXT"),
    ],
    "ai_search_pending_actions": [
        ("plan_version", "plan_version INTEGER"),
        ("source", "source TEXT"),
        ("resolution_json", "resolution_json TEXT"),
        ("updated_at", "updated_at TEXT NOT NULL DEFAULT ''"),
        ("superseded_by", "superseded_by TEXT"),
    ],
}


SQLITE_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    owner_id TEXT,
    task_type TEXT NOT NULL DEFAULT 'patent_analysis',
    pn TEXT,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    progress INTEGER DEFAULT 0,
    current_step TEXT,
    output_dir TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    deleted_at TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS patent_analyses (
    pn TEXT PRIMARY KEY,
    first_completed_at TEXT NOT NULL,
    sha256 TEXT
);

CREATE TABLE IF NOT EXISTS users (
    owner_id TEXT PRIMARY KEY,
    authing_sub TEXT NOT NULL UNIQUE,
    role TEXT,
    name TEXT,
    nickname TEXT,
    email TEXT,
    phone TEXT,
    picture TEXT,
    notification_email_enabled INTEGER NOT NULL DEFAULT 0,
    work_notification_email TEXT,
    personal_notification_email TEXT,
    raw_profile TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_month_targets (
    owner_id TEXT NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    target_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, year, month)
);

CREATE TABLE IF NOT EXISTS task_llm_usage (
    task_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    task_status TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    llm_call_count INTEGER NOT NULL DEFAULT 0,
    estimated_cost_cny REAL NOT NULL DEFAULT 0,
    price_missing INTEGER NOT NULL DEFAULT 0,
    model_breakdown_json TEXT,
    first_usage_at TEXT,
    last_usage_at TEXT,
    currency TEXT NOT NULL DEFAULT 'CNY',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_logs (
    log_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    category TEXT NOT NULL,
    event_name TEXT NOT NULL,
    level TEXT NOT NULL,
    owner_id TEXT,
    task_id TEXT,
    task_type TEXT,
    request_id TEXT,
    trace_id TEXT,
    method TEXT,
    path TEXT,
    status_code INTEGER,
    duration_ms INTEGER,
    provider TEXT,
    target_host TEXT,
    success INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    payload_inline_json TEXT,
    payload_file_path TEXT,
    payload_bytes INTEGER NOT NULL DEFAULT 0,
    payload_overflow INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_sessions (
    token_hash TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revoked_at TEXT,
    replaced_by_token_hash TEXT
);

CREATE TABLE IF NOT EXISTS wechat_bindings (
    binding_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL,
    bot_account_id TEXT,
    wechat_user_id TEXT,
    wechat_display_name TEXT,
    delivery_peer_id TEXT,
    delivery_peer_name TEXT,
    push_task_completed INTEGER NOT NULL DEFAULT 1,
    push_task_failed INTEGER NOT NULL DEFAULT 1,
    push_ai_search_pending_action INTEGER NOT NULL DEFAULT 1,
    bound_at TEXT,
    disconnected_at TEXT,
    last_inbound_at TEXT,
    last_outbound_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wechat_login_sessions (
    login_session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL,
    qr_url TEXT,
    expires_at TEXT NOT NULL,
    bot_account_id TEXT,
    wechat_user_id TEXT,
    wechat_display_name TEXT,
    error_message TEXT,
    online_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wechat_flow_sessions (
    flow_session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    flow_type TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT,
    draft_payload_json TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wechat_conversation_sessions (
    conversation_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    peer_name TEXT,
    status TEXT NOT NULL,
    active_context_kind TEXT NOT NULL DEFAULT 'none',
    active_context_session_id TEXT,
    active_context_title TEXT,
    memory_json TEXT,
    last_inbound_at TEXT,
    last_outbound_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wechat_delivery_jobs (
    delivery_job_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    binding_id TEXT,
    task_id TEXT,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    delivery_stage TEXT NOT NULL DEFAULT 'queued',
    payload_json TEXT,
    stage_details_json TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TEXT,
    claimed_at TEXT,
    completed_at TEXT,
    failed_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_owner_id ON tasks(owner_id);
CREATE INDEX IF NOT EXISTS idx_tasks_pn ON tasks(pn);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_updated_at ON tasks(updated_at);
CREATE INDEX IF NOT EXISTS idx_tasks_task_type ON tasks(task_type);
CREATE INDEX IF NOT EXISTS idx_tasks_deleted_created_at ON tasks(deleted_at, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_deleted_owner_id ON tasks(deleted_at, owner_id);
CREATE INDEX IF NOT EXISTS idx_patent_analyses_completed_at ON patent_analyses(first_completed_at);
CREATE INDEX IF NOT EXISTS idx_users_authing_sub ON users(authing_sub);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_account_month_targets_owner_ym ON account_month_targets(owner_id, year, month);
CREATE INDEX IF NOT EXISTS idx_task_llm_usage_owner_id ON task_llm_usage(owner_id);
CREATE INDEX IF NOT EXISTS idx_task_llm_usage_last_usage_at ON task_llm_usage(last_usage_at);
CREATE INDEX IF NOT EXISTS idx_task_llm_usage_task_type ON task_llm_usage(task_type);
CREATE INDEX IF NOT EXISTS idx_task_llm_usage_task_status ON task_llm_usage(task_status);
CREATE INDEX IF NOT EXISTS idx_system_logs_timestamp ON system_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_system_logs_category ON system_logs(category);
CREATE INDEX IF NOT EXISTS idx_system_logs_owner_id ON system_logs(owner_id);
CREATE INDEX IF NOT EXISTS idx_system_logs_task_id ON system_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_system_logs_request_id ON system_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_system_logs_provider ON system_logs(provider);
CREATE INDEX IF NOT EXISTS idx_system_logs_success ON system_logs(success);
CREATE INDEX IF NOT EXISTS idx_refresh_sessions_owner_id ON refresh_sessions(owner_id);
CREATE INDEX IF NOT EXISTS idx_refresh_sessions_expires_at ON refresh_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_refresh_sessions_revoked_at ON refresh_sessions(revoked_at);
CREATE INDEX IF NOT EXISTS idx_wechat_bindings_owner_status ON wechat_bindings(owner_id, status);
CREATE INDEX IF NOT EXISTS idx_wechat_bindings_account_status ON wechat_bindings(bot_account_id, status);
CREATE INDEX IF NOT EXISTS idx_wechat_bindings_delivery_peer_status ON wechat_bindings(bot_account_id, delivery_peer_id, status);
CREATE INDEX IF NOT EXISTS idx_wechat_login_sessions_owner_status ON wechat_login_sessions(owner_id, status);
CREATE INDEX IF NOT EXISTS idx_wechat_login_sessions_expires_at ON wechat_login_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_wechat_flow_sessions_owner_type_status ON wechat_flow_sessions(owner_id, flow_type, status);
CREATE INDEX IF NOT EXISTS idx_wechat_conversation_sessions_owner_status ON wechat_conversation_sessions(owner_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wechat_conversation_sessions_binding_peer ON wechat_conversation_sessions(binding_id, peer_id);
CREATE INDEX IF NOT EXISTS idx_wechat_conversation_sessions_context ON wechat_conversation_sessions(active_context_kind, active_context_session_id);
CREATE INDEX IF NOT EXISTS idx_wechat_delivery_jobs_status_next_attempt ON wechat_delivery_jobs(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_wechat_delivery_jobs_owner_status ON wechat_delivery_jobs(owner_id, status);
""" + AI_SEARCH_STORAGE_SQL
