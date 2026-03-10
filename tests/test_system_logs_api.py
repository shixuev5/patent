from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend import admin_auth
from backend.models import CurrentUser
from backend.routes import admin_logs
from backend.storage import User
from backend.storage.sqlite_storage import SQLiteTaskStorage


def _mount_storage(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "system_logs_api_test.db")
    manager = SimpleNamespace(storage=storage)
    monkeypatch.setattr(admin_auth, "task_manager", manager)
    monkeypatch.setattr(admin_logs, "task_manager", manager)
    return storage


def _seed_users(storage: SQLiteTaskStorage):
    storage.upsert_authing_user(
        User(
            owner_id="authing:admin-1",
            authing_sub="admin-1",
            raw_profile={"roles": ["admin"]},
        )
    )
    storage.upsert_authing_user(
        User(
            owner_id="authing:user-1",
            authing_sub="user-1",
            raw_profile={"roles": ["member"]},
        )
    )


def _seed_logs(storage: SQLiteTaskStorage):
    now_iso = datetime.now().isoformat()
    storage.insert_system_log(
        {
            "log_id": "log-1",
            "timestamp": now_iso,
            "category": "llm_call",
            "event_name": "chat_completion_json",
            "level": "INFO",
            "owner_id": "authing:user-1",
            "task_id": "task-1",
            "task_type": "patent_analysis",
            "request_id": "req-1",
            "trace_id": "trace-1",
            "method": "POST",
            "path": "/api/tasks",
            "status_code": 200,
            "duration_ms": 100,
            "provider": "llm",
            "target_host": "api.example.com",
            "success": True,
            "message": "ok",
            "payload_inline_json": '{"foo":"bar"}',
            "payload_file_path": None,
            "payload_bytes": 13,
            "payload_overflow": False,
            "created_at": now_iso,
        }
    )


def test_admin_system_logs_api(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHING_ADMIN_ROLE_NAME", "admin")
    storage = _mount_storage(monkeypatch, tmp_path)
    _seed_users(storage)
    _seed_logs(storage)

    admin_user = CurrentUser(user_id="authing:admin-1")

    summary = asyncio.run(admin_logs.get_admin_system_log_summary(current_user=admin_user))
    assert summary.totalLogs == 1
    assert summary.llmCallCount == 1

    listed = asyncio.run(
        admin_logs.get_admin_system_logs(
            category="llm_call",
            eventName=None,
            ownerId=None,
            taskId=None,
            requestId=None,
            traceId=None,
            provider=None,
            success=None,
            dateFrom=None,
            dateTo=None,
            q=None,
            page=1,
            pageSize=20,
            current_user=admin_user,
        )
    )
    assert listed.total == 1
    assert listed.items[0].logId == "log-1"

    detail = asyncio.run(admin_logs.get_admin_system_log_detail(log_id="log-1", current_user=admin_user))
    assert detail.item.logId == "log-1"
    assert isinstance(detail.payload, dict)
    assert detail.payload.get("foo") == "bar"


def test_admin_system_logs_forbidden(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHING_ADMIN_ROLE_NAME", "admin")
    storage = _mount_storage(monkeypatch, tmp_path)
    _seed_users(storage)
    _seed_logs(storage)

    non_admin = CurrentUser(user_id="authing:user-1")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            admin_logs.get_admin_system_log_summary(
                current_user=non_admin,
            )
        )
    assert exc_info.value.status_code == 403
