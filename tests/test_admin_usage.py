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
            owner_id="authing:user-3",
            authing_sub="user-3",
            role="member",
            name="用户甲",
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
            "task_type": "ai_reply",
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
    assert all_table.summary.totalTasks == 2
    assert all_table.summary.totalUsers == 2


def test_admin_user_scope_aggregation_and_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHING_ADMIN_ROLE_NAME", "admin")
    storage = _mount_storage(monkeypatch, tmp_path)
    _seed_users(storage)
    _seed_usage_rows(storage)

    now_iso = datetime.now().isoformat()
    storage.upsert_task_llm_usage(
        {
            "task_id": "task-3",
            "owner_id": "authing:user-1",
            "task_type": "patent_analysis",
            "task_status": "completed",
            "prompt_tokens": 10,
            "completion_tokens": 40,
            "total_tokens": 50,
            "reasoning_tokens": 0,
            "llm_call_count": 3,
            "estimated_cost_cny": 0.2,
            "price_missing": False,
            "model_breakdown_json": {"qwen3.5-flash": {"totalTokens": 50}},
            "first_usage_at": now_iso,
            "last_usage_at": now_iso,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
    )
    storage.upsert_task_llm_usage(
        {
            "task_id": "task-4",
            "owner_id": "authing:user-3",
            "task_type": "patent_analysis",
            "task_status": "completed",
            "prompt_tokens": 20,
            "completion_tokens": 50,
            "total_tokens": 70,
            "reasoning_tokens": 0,
            "llm_call_count": 1,
            "estimated_cost_cny": 0.1,
            "price_missing": False,
            "model_breakdown_json": {"qwen3.5-flash": {"totalTokens": 70}},
            "first_usage_at": now_iso,
            "last_usage_at": now_iso,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
    )

    anchor = datetime.now().date().isoformat()
    admin_user = CurrentUser(user_id="authing:admin-1")

    user_table = asyncio.run(
        admin_usage.get_admin_usage_table(
            rangeType="day",
            anchor=anchor,
            scope="user",
            q=None,
            taskType=None,
            status=None,
            model=None,
            page=1,
            pageSize=20,
            sortBy="totalTokens",
            sortOrder="desc",
            current_user=admin_user,
        )
    )
    assert user_table.scope == "user"
    assert user_table.total == 3
    assert user_table.summary.totalTasks == 4
    assert user_table.summary.totalUsers == 3
    assert user_table.summary.totalTokens == 420
    assert user_table.summary.entityType == "user"

    user1_row = next(item for item in user_table.items if item["ownerId"] == "authing:user-1")
    assert user1_row["taskCount"] == 2
    assert user1_row["totalTokens"] == 250
    assert user1_row["llmCallCount"] == 5
    assert user1_row["estimatedCostCny"] == pytest.approx(0.6)

    same_name_rows = [item for item in user_table.items if item["userName"] == "用户甲"]
    assert len(same_name_rows) == 2
    assert {item["ownerId"] for item in same_name_rows} == {"authing:user-1", "authing:user-3"}

    searched_table = asyncio.run(
        admin_usage.get_admin_usage_table(
            rangeType="day",
            anchor=anchor,
            scope="user",
            q="authing:user-1",
            taskType=None,
            status=None,
            model=None,
            page=1,
            pageSize=20,
            sortBy="totalTokens",
            sortOrder="desc",
            current_user=admin_user,
        )
    )
    assert searched_table.total == 1
    assert searched_table.items[0]["ownerId"] == "authing:user-1"
    assert searched_table.summary.totalTasks == 4
    assert searched_table.summary.totalUsers == 3


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
                current_user=non_admin,
            )
        )
    assert exc_info.value.status_code == 403


def test_admin_usage_respects_utc8_day_boundary_and_returns_utc_timestamps(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHING_ADMIN_ROLE_NAME", "admin")
    storage = _mount_storage(monkeypatch, tmp_path)
    _seed_users(storage)

    rows = [
        ("task-before", "2026-03-19T15:59:59"),
        ("task-start", "2026-03-19T16:00:00"),
        ("task-end", "2026-03-20T15:59:59"),
        ("task-after", "2026-03-20T16:00:00"),
    ]
    for task_id, last_usage_at in rows:
        storage.upsert_task_llm_usage(
            {
                "task_id": task_id,
                "owner_id": "authing:user-1",
                "task_type": "patent_analysis",
                "task_status": "completed",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "reasoning_tokens": 0,
                "llm_call_count": 1,
                "estimated_cost_cny": 0.1,
                "price_missing": False,
                "model_breakdown_json": {"qwen3.5-flash": {"totalTokens": 15}},
                "first_usage_at": last_usage_at,
                "last_usage_at": last_usage_at,
                "created_at": last_usage_at,
                "updated_at": last_usage_at,
            }
        )

    admin_user = CurrentUser(user_id="authing:admin-1")

    dashboard = asyncio.run(
        admin_usage.get_admin_usage_dashboard(
            rangeType="day",
            anchor="2026-03-20",
            current_user=admin_user,
        )
    )
    assert dashboard.startAt == "2026-03-19T16:00:00Z"
    assert dashboard.endAt == "2026-03-20T16:00:00Z"
    assert dashboard.overview.totalTasks == 2

    task_table = asyncio.run(
        admin_usage.get_admin_usage_table(
            rangeType="day",
            anchor="2026-03-20",
            scope="task",
            q=None,
            taskType=None,
            status=None,
            model=None,
            page=1,
            pageSize=20,
            sortBy="lastUsageAt",
            sortOrder="asc",
            current_user=admin_user,
        )
    )
    assert [item["taskId"] for item in task_table.items] == ["task-start", "task-end"]
    assert task_table.items[0]["lastUsageAt"] == "2026-03-19T16:00:00Z"
    assert task_table.items[1]["lastUsageAt"] == "2026-03-20T15:59:59Z"


def test_admin_usage_resolve_time_window_uses_utc8_for_day_month_year():
    day_anchor, day_start, day_end, day_query_start, day_query_end = admin_usage._resolve_time_window("day", "2026-03-20")
    assert day_anchor == "2026-03-20"
    assert day_start.isoformat() == "2026-03-20T00:00:00+08:00"
    assert day_end.isoformat() == "2026-03-21T00:00:00+08:00"
    assert day_query_start == "2026-03-19T16:00:00.000000Z"
    assert day_query_end == "2026-03-20T16:00:00.000000Z"

    month_anchor, month_start, month_end, month_query_start, month_query_end = admin_usage._resolve_time_window("month", "2026-03")
    assert month_anchor == "2026-03"
    assert month_start.isoformat() == "2026-03-01T00:00:00+08:00"
    assert month_end.isoformat() == "2026-04-01T00:00:00+08:00"
    assert month_query_start == "2026-02-28T16:00:00.000000Z"
    assert month_query_end == "2026-03-31T16:00:00.000000Z"

    year_anchor, year_start, year_end, year_query_start, year_query_end = admin_usage._resolve_time_window("year", "2026")
    assert year_anchor == "2026"
    assert year_start.isoformat() == "2026-01-01T00:00:00+08:00"
    assert year_end.isoformat() == "2027-01-01T00:00:00+08:00"
    assert year_query_start == "2025-12-31T16:00:00.000000Z"
    assert year_query_end == "2026-12-31T16:00:00.000000Z"
