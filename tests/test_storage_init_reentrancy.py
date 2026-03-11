from __future__ import annotations

import threading

from backend import system_logs
from backend.storage import task_storage
from backend.storage import d1_storage


def test_get_task_storage_avoids_reentrant_init_deadlock(tmp_path, monkeypatch):
    task_storage.reset_storage_instance()
    monkeypatch.setattr(system_logs, "_STORAGE_REF", None)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DB_ENABLED", True)
    system_logs.set_system_log_db_persistence_ready(True)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")

    monkeypatch.setenv("TASK_STORAGE_BACKEND", "d1")
    monkeypatch.setenv("D1_ACCOUNT_ID", "acc")
    monkeypatch.setenv("D1_DATABASE_ID", "db")
    monkeypatch.setenv("D1_API_TOKEN", "token")

    class _DummyD1Storage:
        def __init__(self, *args, **kwargs):
            system_logs.emit_system_log(
                category="external_api",
                event_name="requests_call",
                success=True,
                payload={"source": "test"},
            )

    monkeypatch.setattr(d1_storage, "D1TaskStorage", _DummyD1Storage)

    result = {}
    error = {}

    def _target():
        try:
            result["storage"] = task_storage.get_task_storage()
        except Exception as exc:  # pragma: no cover - failure path
            error["exc"] = exc

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout=2)

    assert not worker.is_alive(), "get_task_storage re-entrant initialization deadlocked"
    assert "exc" not in error
    assert isinstance(result.get("storage"), _DummyD1Storage)

    task_storage.reset_storage_instance()
