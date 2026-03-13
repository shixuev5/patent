from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend import admin_auth
from backend.models import CurrentUser
from backend.routes import admin_entities
from backend.storage import Task, TaskStatus, User
from backend.storage.sqlite_storage import SQLiteTaskStorage


def _mount_storage(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "admin_entities_api_test.db")
    manager = SimpleNamespace(storage=storage)
    monkeypatch.setattr(admin_auth, "task_manager", manager)
    monkeypatch.setattr(admin_entities, "task_manager", manager)
    return storage


def _seed_users(storage: SQLiteTaskStorage):
    storage.upsert_authing_user(
        User(
            owner_id="authing:admin-1",
            authing_sub="admin-1",
            role="admin",
            name="管理员",
            email="admin@example.com",
        )
    )
    storage.upsert_authing_user(
        User(
            owner_id="authing:user-1",
            authing_sub="user-1",
            role="member",
            name="张三",
            email="zhangsan@example.com",
        )
    )
    storage.upsert_authing_user(
        User(
            owner_id="authing:user-2",
            authing_sub="user-2",
            role="member",
            name="李四",
            email="lisi@example.com",
        )
    )


def _seed_tasks(storage: SQLiteTaskStorage):
    now = datetime.now()
    storage.create_task(
        Task(
            id="task-1",
            owner_id="authing:user-1",
            task_type="patent_analysis",
            title="AI 分析任务A",
            status=TaskStatus.COMPLETED,
            progress=100,
            current_step="done",
            created_at=now - timedelta(hours=12),
            updated_at=now,
            completed_at=now,
            metadata={"k": "v1"},
        )
    )
    storage.create_task(
        Task(
            id="task-2",
            owner_id="authing:user-2",
            task_type="ai_reply",
            title="答复任务B",
            status=TaskStatus.PROCESSING,
            progress=40,
            current_step="running",
            created_at=now - timedelta(days=3),
            updated_at=now - timedelta(hours=1),
            metadata={"k": "v2"},
        )
    )
    storage.create_task(
        Task(
            id="task-3",
            owner_id="guest_abc123",
            task_type="patent_analysis",
            title="访客任务C",
            status=TaskStatus.FAILED,
            progress=80,
            current_step="error",
            created_at=now - timedelta(days=10),
            updated_at=now - timedelta(days=10, hours=1),
            metadata={"k": "v3"},
        )
    )


def test_admin_entities_users_and_tasks(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHING_ADMIN_ROLE_NAME", "admin")
    storage = _mount_storage(monkeypatch, tmp_path)
    _seed_users(storage)
    _seed_tasks(storage)

    admin_user = CurrentUser(user_id="authing:admin-1")

    users = asyncio.run(
        admin_entities.get_admin_entity_users(
            q="张",
            role=None,
            page=1,
            pageSize=10,
            sortBy="taskCount",
            sortOrder="desc",
            current_user=admin_user,
        )
    )
    assert users.total >= 1
    assert users.items[0].userName == "张三"
    assert users.meta is None

    user_stats = asyncio.run(
        admin_entities.get_admin_entity_user_stats(
            current_user=admin_user,
        )
    ).userStats
    assert user_stats["totalUsers"] == 4
    assert user_stats["registeredUsers"] == 3
    stats = user_stats
    assert stats["activeUsers1d"] == 1
    assert stats["activeUsers7d"] == 2
    assert stats["activeUsers30d"] == 3
    assert 2 <= stats["newUsers1d"] <= 3
    assert stats["newUsers7d"] >= stats["newUsers1d"]
    assert stats["newUsers30d"] >= stats["newUsers7d"]
    assert stats["newUsers30d"] == 4

    tasks = asyncio.run(
        admin_entities.get_admin_entity_tasks(
            q=None,
            userName="李",
            taskType="ai_reply",
            status="processing",
            dateFrom=None,
            dateTo=None,
            page=1,
            pageSize=10,
            sortBy="updatedAt",
            sortOrder="desc",
            current_user=admin_user,
        )
    )
    assert tasks.total == 1
    assert tasks.items[0].taskId == "task-2"
    assert tasks.items[0].userName == "李四"
    assert isinstance(tasks.items[0].durationSeconds, int)
    assert tasks.items[0].durationSeconds >= 0
    assert tasks.meta is None

    task_stats = asyncio.run(
        admin_entities.get_admin_entity_task_stats(
            current_user=admin_user,
        )
    )
    windows = {item["taskType"]: item for item in task_stats.taskTypeWindows}
    assert windows["ai_reply"]["count1d"] == 0
    assert windows["ai_reply"]["count7d"] == 1
    assert windows["ai_reply"]["count30d"] == 1
    assert windows["patent_analysis"]["count1d"] == 1
    assert windows["patent_analysis"]["count7d"] == 1
    assert windows["patent_analysis"]["count30d"] == 2

    detail = asyncio.run(admin_entities.get_admin_entity_task_detail(task_id="task-1", current_user=admin_user))
    assert detail.item["taskId"] == "task-1"
    assert detail.item["userName"] == "张三"
    assert detail.item["metadata"] == {"k": "v1"}


def test_admin_entities_forbidden(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHING_ADMIN_ROLE_NAME", "admin")
    storage = _mount_storage(monkeypatch, tmp_path)
    _seed_users(storage)
    _seed_tasks(storage)
    non_admin = CurrentUser(user_id="authing:user-1")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            admin_entities.get_admin_entity_users(
                q=None,
                role=None,
                page=1,
                pageSize=10,
                sortBy="latestTaskAt",
                sortOrder="desc",
                current_user=non_admin,
            )
        )
    assert exc_info.value.status_code == 403
