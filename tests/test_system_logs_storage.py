from __future__ import annotations

from datetime import datetime, timedelta

from backend.storage.sqlite_storage import SQLiteTaskStorage


def _insert_sample(storage: SQLiteTaskStorage, log_id: str, category: str, success: bool, ts: str):
    storage.insert_system_log(
        {
            "log_id": log_id,
            "timestamp": ts,
            "category": category,
            "event_name": "sample_event",
            "level": "INFO",
            "owner_id": "authing:user-1",
            "task_id": "task-1",
            "task_type": "patent_analysis",
            "request_id": "req-1",
            "trace_id": "trace-1",
            "method": "GET",
            "path": "/api/tasks",
            "status_code": 200 if success else 500,
            "duration_ms": 12,
            "provider": "llm",
            "target_host": "example.com",
            "success": success,
            "message": "ok" if success else "failed",
            "payload_inline_json": '{"k":"v"}',
            "payload_file_path": None,
            "payload_bytes": 9,
            "payload_overflow": False,
            "created_at": ts,
        }
    )


def test_system_logs_insert_list_summary_cleanup(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "system_logs_test.db")
    now = datetime.now()
    old_ts = (now - timedelta(days=30)).isoformat()
    new_ts = now.isoformat()

    _insert_sample(storage, "log-old", "task_execution", True, old_ts)
    _insert_sample(storage, "log-new-1", "llm_call", False, new_ts)
    _insert_sample(storage, "log-new-2", "user_action", True, new_ts)

    listed = storage.list_system_logs(page=1, page_size=10)
    assert listed["total"] == 3
    assert len(listed["items"]) == 3

    failed = storage.list_system_logs(success=False, page=1, page_size=10)
    assert failed["total"] == 1
    assert failed["items"][0]["log_id"] == "log-new-1"

    by_category = storage.list_system_logs(category="llm_call", page=1, page_size=10)
    assert by_category["total"] == 1
    assert by_category["items"][0]["category"] == "llm_call"

    detail = storage.get_system_log("log-new-2")
    assert detail is not None
    assert detail["log_id"] == "log-new-2"

    summary = storage.summarize_system_logs()
    assert summary["totalLogs"] == 3
    assert summary["failedLogs"] == 1
    assert summary["llmCallCount"] == 1
    assert any(item["category"] == "llm_call" for item in summary["byCategory"])

    deleted = storage.cleanup_system_logs_before((now - timedelta(days=14)).isoformat())
    assert deleted >= 1
    listed_after = storage.list_system_logs(page=1, page_size=10)
    assert listed_after["total"] == 2
