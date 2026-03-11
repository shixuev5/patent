from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend import admin_auth
from backend.models import CurrentUser
from backend.routes import admin_usage
from backend.storage import User
from backend.storage.sqlite_storage import SQLiteTaskStorage


def _mount_storage(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "admin_usage_test.db")
    manager = SimpleNamespace(storage=storage)
    monkeypatch.setattr(admin_auth, "task_manager", manager)
    monkeypatch.setattr(admin_usage, "task_manager", manager)
    return storage


def _seed_users(storage: SQLiteTaskStorage):
    storage.upsert_authing_user(
        User(
            owner_id="authing:admin-1",
            authing_sub="admin-1",
            role="admin",
            name="管理员",
            raw_profile={},
        )
    )
    storage.upsert_authing_user(
        User(
            owner_id="authing:user-1",
            authing_sub="user-1",
            role="member",
            name="用户甲",
            raw_profile={},
        )
    )
    storage.upsert_authing_user(
        User(
            owner_id="authing:user-2",
            authing_sub="user-2",
            role="member",
            name="用户乙",
            raw_profile={},
        )
    )
    storage.upsert_authing_user(
        User(
            owner_id="authing:admin-by-role-field",
            authing_sub="admin-by-role-field",
            role="admin",
            name="角色管理员",
            raw_profile={},
        )
    )


def _seed_usage_rows(storage: SQLiteTaskStorage):
    now_iso = datetime.now().isoformat()
    storage.upsert_task_llm_usage(
        {
            "task_id": "task-1",
            "owner_id": "authing:user-1",
            "task_type": "patent_analysis",
            "task_status": "completed",
            "prompt_tokens": 120,
            "completion_tokens": 80,
            "total_tokens": 200,
            "reasoning_tokens": 30,
            "llm_call_count": 2,
            "estimated_cost_cny": 0.4,
            "price_missing": False,
            "model_breakdown_json": {"qwen3.5-flash": {"totalTokens": 200}},
            "first_usage_at": now_iso,
            "last_usage_at": now_iso,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
    )
    storage.upsert_task_llm_usage(
        {
            "task_id": "task-2",
            "owner_id": "authing:user-2",
            "task_type": "office_action_reply",
            "task_status": "failed",
            "prompt_tokens": 60,
            "completion_tokens": 40,
            "total_tokens": 100,
            "reasoning_tokens": 0,
            "llm_call_count": 1,
            "estimated_cost_cny": 0,
            "price_missing": True,
            "model_breakdown_json": {"unknown-model": {"totalTokens": 100}},
            "first_usage_at": now_iso,
            "last_usage_at": now_iso,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
    )


def test_admin_role_check_from_raw_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHING_ADMIN_ROLE_NAME", "admin")
    storage = _mount_storage(monkeypatch, tmp_path)
    _seed_users(storage)

    assert admin_auth.is_admin_owner("authing:admin-1") is True
    assert admin_auth.is_admin_owner("authing:admin-by-role-field") is True
    assert admin_auth.is_admin_owner("authing:user-1") is False

    with pytest.raises(HTTPException) as exc_info:
        admin_auth.ensure_admin_owner("authing:user-1")
    assert exc_info.value.status_code == 403


def test_admin_dashboard_and_table(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHING_ADMIN_ROLE_NAME", "admin")
    storage = _mount_storage(monkeypatch, tmp_path)
    _seed_users(storage)
    _seed_usage_rows(storage)

    anchor = datetime.now().date().isoformat()
    admin_user = CurrentUser(user_id="authing:admin-1")

    access = asyncio.run(admin_usage.get_admin_access(current_user=admin_user))
    assert access.isAdmin is True

    dashboard = asyncio.run(
        admin_usage.get_admin_usage_dashboard(
            rangeType="day",
            anchor=anchor,
            topN=10,
            current_user=admin_user,
        )
    )
    assert dashboard.overview.totalTasks == 2
    assert dashboard.overview.totalUsers == 2
    assert dashboard.overview.totalTokens == 300
    assert dashboard.priceMissing is True

    task_table = asyncio.run(
        admin_usage.get_admin_usage_table(
            rangeType="day",
            anchor=anchor,
            scope="task",
            q=None,
            taskType=None,
            status=None,
            model="qwen3.5-flash",
            page=1,
            pageSize=20,
            sortBy="lastUsageAt",
            sortOrder="desc",
            current_user=admin_user,
        )
    )
    assert task_table.total == 1
    assert task_table.items[0]["taskId"] == "task-1"
    assert task_table.items[0]["userName"] == "用户甲"

    task_table_by_name = asyncio.run(
        admin_usage.get_admin_usage_table(
            rangeType="day",
            anchor=anchor,
            scope="task",
            q="用户乙",
            taskType=None,
            status=None,
            model=None,
            page=1,
            pageSize=20,
            sortBy="lastUsageAt",
            sortOrder="desc",
            current_user=admin_user,
        )
    )
    assert task_table_by_name.total == 1
    assert task_table_by_name.items[0]["taskId"] == "task-2"

    all_table = asyncio.run(
        admin_usage.get_admin_usage_table(
            rangeType="day",
            anchor=anchor,
            scope="all",
            q=None,
            taskType=None,
            status=None,
            model=None,
            page=1,
            pageSize=20,
            sortBy="taskCount",
            sortOrder="desc",
            current_user=admin_user,
        )
    )
    assert all_table.scope == "all"
    assert all_table.total == 1
    assert all_table.items[0]["taskCount"] == 2


def test_admin_usage_forbidden_for_non_admin(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHING_ADMIN_ROLE_NAME", "admin")
    storage = _mount_storage(monkeypatch, tmp_path)
    _seed_users(storage)
    _seed_usage_rows(storage)

    non_admin = CurrentUser(user_id="authing:user-1")
    anchor = datetime.now().date().isoformat()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            admin_usage.get_admin_usage_dashboard(
                rangeType="day",
                anchor=anchor,
                topN=10,
                current_user=non_admin,
            )
        )
    assert exc_info.value.status_code == 403
