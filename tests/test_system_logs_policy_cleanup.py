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


def test_cleanup_system_logs_by_policy_removes_files(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    payload_dir = logs_dir / "payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)
    payload1 = payload_dir / "1.json.gz"
    payload2 = payload_dir / "2.json.gz"
    payload1.write_text("x", encoding="utf-8")
    payload2.write_text("x", encoding="utf-8")

    storage = _PolicyCleanupStorage([str(payload1), str(payload2)], deleted_rows=2)
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", logs_dir)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", payload_dir)

    summary = system_logs.cleanup_system_logs_by_policy()
    assert summary["deleted_db"] == 2
    assert summary["deleted_payload_files"] == 2
    assert storage.list_calls == 1
    assert storage.delete_calls == 1
    assert not payload1.exists()
    assert not payload2.exists()
