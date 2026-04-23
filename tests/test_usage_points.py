from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend import usage
from backend.time_utils import utc_now
from backend.storage import Task, TaskStatus, TaskType
from backend.storage import SQLiteTaskStorage


def _mount_storage(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "usage_points_test.db")
    monkeypatch.setattr(usage, "task_manager", SimpleNamespace(storage=storage))
    monkeypatch.delenv("MAX_DAILY_POINTS_GUEST", raising=False)
    monkeypatch.delenv("MAX_DAILY_POINTS_AUTHING", raising=False)
    return storage


def _create_task(
    storage: SQLiteTaskStorage,
    owner_id: str,
    task_type: str,
    task_id: str,
    status: TaskStatus = TaskStatus.PENDING,
):
    now = utc_now()
    storage.create_task(
        Task(
            id=task_id,
            owner_id=owner_id,
            task_type=task_type,
            status=status,
            created_at=now,
            updated_at=now,
        )
    )


def test_guest_usage_uses_point_budget(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    owner_id = "guest_abc"
    _create_task(storage, owner_id, TaskType.PATENT_ANALYSIS.value, "t-g-1")
    _create_task(storage, owner_id, TaskType.AI_REPLY.value, "t-g-2")

    result = usage._get_user_usage(owner_id, task_type=TaskType.PATENT_ANALYSIS.value)
    assert result.authType == "guest"
    assert result.dailyPointLimit == 3.0
    assert result.usedPoints == 3.0
    assert result.remainingPoints == 0.0
    assert result.createdToday.analysisCount == 1
    assert result.createdToday.replyCount == 1
    assert result.createdToday.totalCount == 2
    assert result.canCreateRequestedTask is False


def test_authing_usage_uses_higher_default_budget(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    owner_id = "authing:sub-001"
    _create_task(storage, owner_id, TaskType.PATENT_ANALYSIS.value, "t-a-1")
    _create_task(storage, owner_id, TaskType.AI_REPLY.value, "t-a-2")
    _create_task(storage, owner_id, TaskType.AI_REPLY.value, "t-a-3")

    result = usage._get_user_usage(owner_id)
    assert result.authType == "authing"
    assert result.dailyPointLimit == 10.0
    assert result.usedPoints == 5.0
    assert result.remainingPoints == 5.0


def test_usage_query_can_create_requested_task(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    owner_id = "guest_qwe"
    _create_task(storage, owner_id, TaskType.PATENT_ANALYSIS.value, "t-q-1")
    _create_task(storage, owner_id, TaskType.PATENT_ANALYSIS.value, "t-q-2")

    analysis_usage = usage._get_user_usage(owner_id, task_type=TaskType.PATENT_ANALYSIS.value)
    reply_usage = usage._get_user_usage(owner_id, task_type=TaskType.AI_REPLY.value)

    assert analysis_usage.remainingPoints == 1.0
    assert analysis_usage.canCreateRequestedTask is True
    assert reply_usage.canCreateRequestedTask is False


def test_enforce_daily_quota_raises_structured_429(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    owner_id = "guest_limit"
    _create_task(storage, owner_id, TaskType.AI_REPLY.value, "t-l-1")
    _create_task(storage, owner_id, TaskType.AI_REPLY.value, "t-l-2")

    with pytest.raises(HTTPException) as exc_info:
        usage._enforce_daily_quota(owner_id, task_type=TaskType.PATENT_ANALYSIS.value)

    exc = exc_info.value
    assert getattr(exc, "status_code", None) == 429
    detail = getattr(exc, "detail", {})
    assert detail.get("code") == "DAILY_POINTS_EXCEEDED"
    assert detail.get("authType") == "guest"
    assert detail.get("requiredPoints") == 1.0
    assert detail.get("remainingPoints") == 0.0
    assert detail.get("shouldPromptLogin") is True


def test_failed_or_cancelled_tasks_refund_points(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    owner_id = "guest_refund"
    _create_task(storage, owner_id, TaskType.PATENT_ANALYSIS.value, "t-r-1", status=TaskStatus.COMPLETED)
    _create_task(storage, owner_id, TaskType.AI_REPLY.value, "t-r-2", status=TaskStatus.FAILED)
    _create_task(storage, owner_id, TaskType.AI_REPLY.value, "t-r-3", status=TaskStatus.CANCELLED)

    result = usage._get_user_usage(owner_id, task_type=TaskType.AI_REPLY.value)
    assert result.usedPoints == 1.0
    assert result.remainingPoints == 2.0
    assert result.canCreateRequestedTask is True


def test_ai_search_paused_task_still_occupies_points(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    owner_id = "guest_ai_search"
    _create_task(storage, owner_id, TaskType.AI_SEARCH.value, "t-s-1", status=TaskStatus.PAUSED)

    result = usage._get_user_usage(owner_id, task_type=TaskType.AI_SEARCH.value)

    assert result.usedPoints == 1.5
    assert result.remainingPoints == 1.5
    assert result.createdToday.searchCount == 1
    assert result.canCreateRequestedTask is True


def test_usage_aggregation_keeps_deleted_tasks_in_created_counts(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    owner_id = "guest_deleted"
    _create_task(storage, owner_id, TaskType.PATENT_ANALYSIS.value, "t-d-1", status=TaskStatus.COMPLETED)
    _create_task(storage, owner_id, TaskType.AI_REPLY.value, "t-d-2", status=TaskStatus.PROCESSING)
    storage.delete_task("t-d-2")

    result = usage._get_user_usage(owner_id, task_type=TaskType.PATENT_ANALYSIS.value)

    assert result.createdToday.analysisCount == 1
    assert result.createdToday.replyCount == 1
    assert result.createdToday.totalCount == 2
    assert result.usedPoints == 3.0
    assert result.remainingPoints == 0.0


def test_usage_aggregation_ignores_failed_and_cancelled_for_occupied_counts(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    owner_id = "guest_terminal_mix"
    _create_task(storage, owner_id, TaskType.AI_REPLY.value, "t-m-1", status=TaskStatus.FAILED)
    _create_task(storage, owner_id, TaskType.AI_REPLY.value, "t-m-2", status=TaskStatus.CANCELLED)
    _create_task(storage, owner_id, TaskType.AI_REPLY.value, "t-m-3", status=TaskStatus.PENDING)

    result = usage._get_user_usage(owner_id, task_type=TaskType.AI_REPLY.value)

    assert result.createdToday.replyCount == 3
    assert result.usedPoints == 2.0
    assert result.remainingPoints == 1.0
    assert result.canCreateRequestedTask is False
