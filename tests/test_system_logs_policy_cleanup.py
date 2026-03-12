from __future__ import annotations

from backend import system_logs


class _PolicyCleanupStorage:
    def __init__(self, payload_paths: list[str], deleted_rows: int):
        self.payload_paths = payload_paths
        self.deleted_rows = deleted_rows
        self.list_calls = 0
        self.delete_calls = 0

    def list_system_log_payload_paths_for_policy_cleanup(self):
        self.list_calls += 1
        return list(self.payload_paths)

    def cleanup_system_logs_by_policy(self):
        self.delete_calls += 1
        return self.deleted_rows


def test_cleanup_system_logs_by_policy_once_removes_files_and_marks(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    payload_dir = logs_dir / "payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)
    payload1 = payload_dir / "1.json.gz"
    payload2 = payload_dir / "2.json.gz"
    payload1.write_text("x", encoding="utf-8")
    payload2.write_text("x", encoding="utf-8")

    storage = _PolicyCleanupStorage([str(payload1), str(payload2)], deleted_rows=2)
    marker = logs_dir / ".system_log_policy_cleanup_v1.done"
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", logs_dir)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", payload_dir)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_POLICY_CLEANUP_MARKER_FILE", marker)

    first = system_logs.cleanup_system_logs_by_policy_once()
    assert first["executed"] is True
    assert first["deleted_db"] == 2
    assert first["deleted_payload_files"] == 2
    assert marker.exists()
    assert storage.list_calls == 1
    assert storage.delete_calls == 1
    assert not payload1.exists()
    assert not payload2.exists()

    second = system_logs.cleanup_system_logs_by_policy_once()
    assert second["executed"] is False
    assert second["deleted_db"] == 0
    assert second["deleted_payload_files"] == 0
    assert storage.list_calls == 1
    assert storage.delete_calls == 1
