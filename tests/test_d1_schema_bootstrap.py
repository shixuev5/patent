from __future__ import annotations

import requests
import pytest

from backend.storage import D1TaskStorage
from backend.storage.errors import StorageRateLimitedError, StorageUnavailableError
from backend.storage.schema.ddl_utils import relax_column_ddl_for_add_column


def test_relax_column_ddl_for_add_column_removes_incompatible_constraints():
    assert relax_column_ddl_for_add_column("peer_id TEXT NOT NULL") == "peer_id TEXT"
    assert relax_column_ddl_for_add_column("authing_sub TEXT NOT NULL UNIQUE") == "authing_sub TEXT"
    assert relax_column_ddl_for_add_column("owner_id TEXT PRIMARY KEY") == "owner_id TEXT"
    assert (
        relax_column_ddl_for_add_column("status TEXT NOT NULL DEFAULT 'pending'")
        == "status TEXT NOT NULL DEFAULT 'pending'"
    )


def test_d1_init_database_skips_full_bootstrap_when_schema_version_matches(monkeypatch):
    storage = object.__new__(D1TaskStorage)
    calls: list[tuple[str, str | None]] = []

    def _ensure_schema_meta_table():
        calls.append(("ensure", None))

    monkeypatch.setattr(storage, "_ensure_schema_meta_table", _ensure_schema_meta_table)
    monkeypatch.setattr(storage, "_schema_bootstrap_version", lambda: "schema-v1")
    monkeypatch.setattr(storage, "_get_schema_meta_value", lambda key: "schema-v1")
    monkeypatch.setattr(storage, "_request", lambda sql, params=None: calls.append(("request", sql)))
    monkeypatch.setattr(storage, "_get_existing_columns", lambda table: set())
    monkeypatch.setattr(storage, "_set_schema_meta_value", lambda key, value: calls.append(("set", value)))

    storage._init_database()

    assert calls == [("ensure", None)]


def test_d1_init_database_adds_missing_columns_before_indexes(monkeypatch):
    storage = object.__new__(D1TaskStorage)
    request_calls: list[str] = []
    schema_updates: list[tuple[str, str]] = []

    existing_columns = {
        table: {name for name, _ in required}
        for table, required in D1TaskStorage.REQUIRED_COLUMNS.items()
    }
    existing_columns["ai_search_documents"] = {
        name
        for name, _ in D1TaskStorage.REQUIRED_COLUMNS["ai_search_documents"]
        if name != "canonical_id"
    }

    monkeypatch.setattr(storage, "_ensure_schema_meta_table", lambda: None)
    monkeypatch.setattr(storage, "_schema_bootstrap_version", lambda: "schema-v2")
    monkeypatch.setattr(storage, "_get_schema_meta_value", lambda key: None)
    monkeypatch.setattr(
        storage,
        "_get_existing_columns",
        lambda table: set(existing_columns.get(table, set())),
    )

    def _request(sql, params=None):
        request_calls.append(str(sql).strip())
        normalized = " ".join(str(sql).split())
        if normalized.startswith("ALTER TABLE ai_search_documents ADD COLUMN canonical_id TEXT"):
            existing_columns["ai_search_documents"].add("canonical_id")
        return {}

    monkeypatch.setattr(storage, "_request", _request)
    monkeypatch.setattr(storage, "_set_schema_meta_value", lambda key, value: schema_updates.append((key, value)))

    storage._init_database()

    alter_sql = "ALTER TABLE ai_search_documents ADD COLUMN canonical_id TEXT"
    index_sql = "CREATE INDEX IF NOT EXISTS idx_ai_search_documents_run_canonical ON ai_search_documents(run_id, canonical_id)"

    assert alter_sql in request_calls
    assert index_sql in request_calls
    assert request_calls.index(alter_sql) < request_calls.index(index_sql)
    assert schema_updates == [(D1TaskStorage.SCHEMA_META_KEY, "schema-v2")]


def test_d1_init_database_relaxes_incompatible_add_column_constraints(monkeypatch):
    storage = object.__new__(D1TaskStorage)
    request_calls: list[str] = []

    existing_columns = {
        table: {name for name, _ in required}
        for table, required in D1TaskStorage.REQUIRED_COLUMNS.items()
    }
    existing_columns["wechat_conversation_sessions"] = {
        name
        for name, _ in D1TaskStorage.REQUIRED_COLUMNS["wechat_conversation_sessions"]
        if name != "peer_id"
    }

    monkeypatch.setattr(storage, "_ensure_schema_meta_table", lambda: None)
    monkeypatch.setattr(storage, "_schema_bootstrap_version", lambda: "schema-v3")
    monkeypatch.setattr(storage, "_get_schema_meta_value", lambda key: None)
    monkeypatch.setattr(
        storage,
        "_get_existing_columns",
        lambda table: set(existing_columns.get(table, set())),
    )

    def _request(sql, params=None):
        request_calls.append(str(sql).strip())
        normalized = " ".join(str(sql).split())
        if normalized.startswith("ALTER TABLE wechat_conversation_sessions ADD COLUMN peer_id TEXT"):
            existing_columns["wechat_conversation_sessions"].add("peer_id")
        return {}

    monkeypatch.setattr(storage, "_request", _request)
    monkeypatch.setattr(storage, "_set_schema_meta_value", lambda key, value: None)

    storage._init_database()

    alter_sql = "ALTER TABLE wechat_conversation_sessions ADD COLUMN peer_id TEXT"
    index_sql = "CREATE UNIQUE INDEX IF NOT EXISTS idx_wechat_conversation_sessions_binding_peer ON wechat_conversation_sessions(binding_id, peer_id)"
    alter_calls = [sql for sql in request_calls if sql.startswith("ALTER TABLE")]

    assert alter_sql in request_calls
    assert all("peer_id TEXT NOT NULL" not in sql for sql in alter_calls)
    assert request_calls.index(alter_sql) < request_calls.index(index_sql)


def test_d1_request_wraps_rate_limit_as_storage_rate_limited(monkeypatch):
    storage = object.__new__(D1TaskStorage)
    storage.endpoint = "https://api.cloudflare.com/client/v4/accounts/test/d1/database/test/query"
    storage.headers = {"Authorization": "Bearer test"}
    storage.timeout_seconds = 20
    storage.max_retries = 0
    storage.retry_base_delay_seconds = 0.5
    storage.retry_max_delay_seconds = 4.0

    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = "9"
    response.url = storage.endpoint

    def _raise_http_error(*args, **kwargs):
        raise requests.exceptions.HTTPError(response=response)

    monkeypatch.setattr(requests, "post", _raise_http_error)

    with pytest.raises(StorageRateLimitedError) as exc_info:
        storage._request("SELECT 1")

    assert exc_info.value.retry_after_seconds == 9


def test_d1_request_wraps_timeout_as_storage_unavailable(monkeypatch):
    storage = object.__new__(D1TaskStorage)
    storage.endpoint = "https://api.cloudflare.com/client/v4/accounts/test/d1/database/test/query"
    storage.headers = {"Authorization": "Bearer test"}
    storage.timeout_seconds = 20
    storage.max_retries = 0
    storage.retry_base_delay_seconds = 0.5
    storage.retry_max_delay_seconds = 4.0

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("timed out")))

    with pytest.raises(StorageUnavailableError):
        storage._request("SELECT 1")


def test_d1_request_retries_rate_limit_then_succeeds(monkeypatch):
    storage = object.__new__(D1TaskStorage)
    storage.endpoint = "https://api.cloudflare.com/client/v4/accounts/test/d1/database/test/query"
    storage.headers = {"Authorization": "Bearer test"}
    storage.timeout_seconds = 20
    storage.max_retries = 2
    storage.retry_base_delay_seconds = 0.01
    storage.retry_max_delay_seconds = 0.02

    response_429 = requests.Response()
    response_429.status_code = 429
    response_429.headers["Retry-After"] = "1"
    response_429.url = storage.endpoint

    response_ok = requests.Response()
    response_ok.status_code = 200
    response_ok._content = b'{"success":true,"result":[{"success":true,"results":[{"value":1}]}]}'
    response_ok.url = storage.endpoint

    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return response_429
        return response_ok

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("backend.storage.backends.d1_backend.time.sleep", lambda _seconds: None)

    result = storage._request("SELECT 1")

    assert calls["count"] == 2
    assert result["results"] == [{"value": 1}]


def test_d1_request_does_not_retry_generic_request_exception(monkeypatch):
    storage = object.__new__(D1TaskStorage)
    storage.endpoint = "https://api.cloudflare.com/client/v4/accounts/test/d1/database/test/query"
    storage.headers = {"Authorization": "Bearer test"}
    storage.timeout_seconds = 20
    storage.max_retries = 2
    storage.retry_base_delay_seconds = 0.01
    storage.retry_max_delay_seconds = 0.02

    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        raise requests.exceptions.RequestException("generic failure")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("backend.storage.backends.d1_backend.time.sleep", lambda _seconds: None)

    with pytest.raises(StorageUnavailableError) as exc_info:
        storage._request("SELECT 1")

    assert str(exc_info.value) == "D1 request failed"
    assert calls["count"] == 1
