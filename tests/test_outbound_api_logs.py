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


def test_requests_instrumentation_logs_outbound_calls(tmp_path, monkeypatch):
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

    assert storage.rows
    row = storage.rows[-1]
    assert row["category"] == "external_api"
    assert row["event_name"] == "requests_call"
    assert row["status_code"] == 200
    payload_text = row.get("payload_inline_json") or ""
    assert "token=abc" not in payload_text
