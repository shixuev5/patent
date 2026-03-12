from __future__ import annotations

from types import SimpleNamespace

import requests

from backend import system_logs


class _MemoryStorage:
    def __init__(self):
        self.rows = []

    def insert_system_log(self, record):
        self.rows.append(record)
        return True


def test_requests_instrumentation_skips_success_calls(tmp_path, monkeypatch):
    storage = _MemoryStorage()
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")
    monkeypatch.setattr(system_logs, "_REQUESTS_PATCHED", False)
    monkeypatch.setattr(system_logs, "_ORIGINAL_SESSION_REQUEST", None)

    def fake_original(self, method, url, *args, **kwargs):
        return SimpleNamespace(
            status_code=200,
            ok=True,
            reason="OK",
            headers={"content-length": "123"},
        )

    monkeypatch.setattr(requests.sessions.Session, "request", fake_original)

    system_logs.instrument_requests()

    session = requests.Session()
    response = session.request("GET", "https://api.example.com/v1/demo?token=abc", timeout=3)
    assert response.status_code == 200
    assert storage.rows == []

def test_requests_instrumentation_logs_failed_calls(tmp_path, monkeypatch):
    storage = _MemoryStorage()
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")
    monkeypatch.setattr(system_logs, "_REQUESTS_PATCHED", False)
    monkeypatch.setattr(system_logs, "_ORIGINAL_SESSION_REQUEST", None)

    def fake_original(self, method, url, *args, **kwargs):
        return SimpleNamespace(
            status_code=500,
            ok=False,
            reason="Server Error",
            headers={"content-length": "123"},
        )

    monkeypatch.setattr(requests.sessions.Session, "request", fake_original)

    system_logs.instrument_requests()

    session = requests.Session()
    response = session.request("GET", "https://api.example.com/v1/demo?token=abc", timeout=3)
    assert response.status_code == 500

    assert storage.rows
    row = storage.rows[-1]
    assert row["category"] == "external_api"
    assert row["event_name"] == "requests_call"
    assert row["status_code"] == 500
    payload_text = row.get("payload_inline_json") or ""
    assert "token=abc" not in payload_text


def test_system_log_db_persistence_is_gated_until_ready(tmp_path, monkeypatch):
    storage = _MemoryStorage()
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DB_ENABLED", True)
    monkeypatch.setattr(system_logs, "_REQUESTS_PATCHED", False)
    monkeypatch.setattr(system_logs, "_ORIGINAL_SESSION_REQUEST", None)
    monkeypatch.setattr(system_logs, "instrument_requests", lambda: None)

    system_logs.initialize_system_logging()
    system_logs.configure_system_log_storage(storage)
    system_logs.emit_system_log(
        category="llm_call",
        event_name="before_ready",
        success=True,
    )
    assert storage.rows == []

    system_logs.set_system_log_db_persistence_ready(True)
    system_logs.emit_system_log(
        category="llm_call",
        event_name="after_ready",
        success=True,
    )
    assert len(storage.rows) == 1
    assert storage.rows[0]["event_name"] == "after_ready"


def test_emit_system_log_applies_policy_filter(tmp_path, monkeypatch):
    storage = _MemoryStorage()
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DB_ENABLED", True)
    system_logs.set_system_log_db_persistence_ready(True)

    system_logs.emit_system_log(category="llm_call", event_name="llm_ok", success=True)
    system_logs.emit_system_log(category="user_action", event_name="get_fail", method="GET", success=False)
    system_logs.emit_system_log(category="user_action", event_name="post_ok", method="POST", success=True)
    system_logs.emit_system_log(category="task_execution", event_name="task_ok", success=True)
    system_logs.emit_system_log(category="task_execution", event_name="task_fail", success=False)

    assert [row["event_name"] for row in storage.rows] == ["llm_ok", "post_ok", "task_fail"]
