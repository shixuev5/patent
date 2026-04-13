from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import system_logs


class _MemoryStorage:
    def __init__(self):
        self.rows = []

    def insert_system_log(self, record):
        self.rows.append(record)
        return True


def test_request_logging_middleware_skips_success_get_request(tmp_path, monkeypatch):
    storage = _MemoryStorage()
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")

    app = FastAPI()
    app.middleware("http")(system_logs.request_logging_middleware)

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/api/ping")
    assert response.status_code == 200
    assert response.headers.get("X-Request-Id")
    assert system_logs.flush_system_log_queue(timeout_seconds=1.0)

    assert storage.rows == []


def test_request_logging_middleware_records_success_post_request(tmp_path, monkeypatch):
    storage = _MemoryStorage()
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")

    app = FastAPI()
    app.middleware("http")(system_logs.request_logging_middleware)

    @app.post("/api/ping")
    async def ping():
        return {"ok": True}

    client = TestClient(app)
    response = client.post("/api/ping", json={"hello": "world"})
    assert response.status_code == 200
    assert response.headers.get("X-Request-Id")
    assert system_logs.flush_system_log_queue(timeout_seconds=1.0)

    assert len(storage.rows) >= 1
    row = storage.rows[-1]
    assert row["category"] == "user_action"
    assert row["event_name"] == "http_request"
    assert row["path"] == "/api/ping"
    assert row["method"] == "POST"
    assert row["status_code"] == 200


def test_request_logging_middleware_skips_get_exception(tmp_path, monkeypatch):
    storage = _MemoryStorage()
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")

    app = FastAPI()
    app.middleware("http")(system_logs.request_logging_middleware)

    @app.get("/api/error")
    async def raise_error():
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/error")
    assert response.status_code == 500
    assert system_logs.flush_system_log_queue(timeout_seconds=1.0)
    assert storage.rows == []


def test_request_logging_middleware_records_post_exception(tmp_path, monkeypatch):
    storage = _MemoryStorage()
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")

    app = FastAPI()
    app.middleware("http")(system_logs.request_logging_middleware)

    @app.post("/api/error")
    async def raise_error():
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/error", json={"hello": "world"})
    assert response.status_code == 500
    assert system_logs.flush_system_log_queue(timeout_seconds=1.0)

    assert len(storage.rows) >= 1
    row = storage.rows[-1]
    assert row["category"] == "user_action"
    assert row["event_name"] == "http_request"
    assert row["path"] == "/api/error"
    assert row["method"] == "POST"
    assert row["success"] == 0


def test_request_logging_middleware_skips_success_internal_request(tmp_path, monkeypatch):
    storage = _MemoryStorage()
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")

    app = FastAPI()
    app.middleware("http")(system_logs.request_logging_middleware)

    @app.post("/api/internal/runtime/heartbeat")
    async def internal_heartbeat():
        return {"ok": True}

    client = TestClient(app)
    response = client.post("/api/internal/runtime/heartbeat", json={"ping": True})
    assert response.status_code == 200
    assert system_logs.flush_system_log_queue(timeout_seconds=1.0)
    assert storage.rows == []


def test_request_logging_middleware_keeps_internal_request_failures(tmp_path, monkeypatch):
    storage = _MemoryStorage()
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")

    app = FastAPI()
    app.middleware("http")(system_logs.request_logging_middleware)

    @app.post("/api/internal/wechat/gateway/login-state")
    async def login_state_error():
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/internal/wechat/gateway/login-state", json={"status": "online"})
    assert response.status_code == 500
    assert system_logs.flush_system_log_queue(timeout_seconds=1.0)
    assert len(storage.rows) == 1
    row = storage.rows[0]
    assert row["path"] == "/api/internal/wechat/gateway/login-state"
    assert row["success"] == 0
