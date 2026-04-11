from __future__ import annotations

from backend.storage.d1_storage import D1TaskStorage


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
