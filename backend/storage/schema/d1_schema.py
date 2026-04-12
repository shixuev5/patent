"""D1-specific schema definitions."""

from .ai_search_sql import AI_SEARCH_STORAGE_SQL


D1_SCHEMA_META_TABLE = "_schema_meta"
D1_SCHEMA_META_KEY = "d1_bootstrap_version"
D1_SCHEMA_META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS _schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

D1_EXTRA_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_patent_analyses_sha256 ON patent_analyses(sha256)",
    "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)",
    "CREATE INDEX IF NOT EXISTS idx_wechat_bindings_owner_status ON wechat_bindings(owner_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_wechat_bindings_peer_status ON wechat_bindings(bot_account_id, wechat_peer_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_wechat_bind_sessions_owner_status ON wechat_bind_sessions(owner_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_wechat_bind_sessions_expires_at ON wechat_bind_sessions(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_wechat_flow_sessions_owner_type_status ON wechat_flow_sessions(owner_id, flow_type, status)",
    "CREATE INDEX IF NOT EXISTS idx_wechat_delivery_jobs_status_next_attempt ON wechat_delivery_jobs(status, next_attempt_at)",
    "CREATE INDEX IF NOT EXISTS idx_wechat_delivery_jobs_owner_status ON wechat_delivery_jobs(owner_id, status)",
)

D1_CREATE_TABLES_SQL = """
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
    wechat_peer_id TEXT,
    wechat_peer_name TEXT,
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

CREATE TABLE IF NOT EXISTS wechat_bind_sessions (
    bind_session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL,
    bind_code TEXT NOT NULL,
    qr_payload TEXT NOT NULL,
    qr_svg TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    bot_account_id TEXT,
    wechat_peer_id TEXT,
    wechat_peer_name TEXT,
    error_message TEXT,
    bound_at TEXT,
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

CREATE TABLE IF NOT EXISTS wechat_delivery_jobs (
    delivery_job_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    binding_id TEXT,
    task_id TEXT,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT,
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
""" + AI_SEARCH_STORAGE_SQL
