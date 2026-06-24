from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.models import (
    AccountMonthTargetUpsertRequest,
    AccountNotificationSettingsUpdateRequest,
    AccountProfileUpdateRequest,
    CurrentUser,
)
from backend.routes import account
from backend.storage import Task, TaskStatus, TaskType, User
from backend.storage import SQLiteTaskStorage
from backend.time_utils import utc_now
from fastapi import HTTPException


def _mount_storage(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'month_target_test.db')
    monkeypatch.setattr(account, 'task_manager', SimpleNamespace(storage=storage))
    return storage


def _create_completed_task(
    storage: SQLiteTaskStorage,
    *,
    owner_id: str,
    task_id: str,
    task_type: str,
    completed_at: datetime,
    status: TaskStatus = TaskStatus.COMPLETED,
):
    storage.create_task(
        Task(
            id=task_id,
            owner_id=owner_id,
            task_type=task_type,
            status=status,
            title=task_id,
            created_at=completed_at - timedelta(days=1),
            updated_at=completed_at,
            completed_at=completed_at,
        )
    )


def test_month_target_carry_forward_chain(monkeypatch, tmp_path):
    _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='u-1')

    empty = asyncio.run(account.get_account_month_target(year=2026, month=3, current_user=user))
    assert empty.targetCount == 0
    assert empty.source == 'empty'

    march_saved = asyncio.run(
        account.put_account_month_target(
            payload=AccountMonthTargetUpsertRequest(year=2026, month=3, targetCount=10),
            current_user=user,
        )
    )
    assert march_saved.targetCount == 10
    assert march_saved.source == 'explicit'

    april = asyncio.run(account.get_account_month_target(year=2026, month=4, current_user=user))
    assert april.targetCount == 10
    assert april.source == 'carried'

    asyncio.run(
        account.put_account_month_target(
            payload=AccountMonthTargetUpsertRequest(year=2026, month=6, targetCount=12),
            current_user=user,
        )
    )
    july = asyncio.run(account.get_account_month_target(year=2026, month=7, current_user=user))
    assert july.targetCount == 12
    assert july.source == 'carried'


def test_dashboard_includes_target_and_source(monkeypatch, tmp_path):
    _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='u-2')

    asyncio.run(
        account.put_account_month_target(
            payload=AccountMonthTargetUpsertRequest(year=2026, month=2, targetCount=8),
            current_user=user,
        )
    )

    dashboard = asyncio.run(account.get_account_dashboard(year=2026, month=3, current_user=user))
    assert dashboard.monthTarget == 8
    assert dashboard.monthTargetSource == 'carried'
    assert dashboard.year == 2026
    assert dashboard.month == 3


def test_dashboard_uses_settlement_period_and_completed_analysis_only(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='u-dashboard-period')

    asyncio.run(
        account.put_account_month_target(
            payload=AccountMonthTargetUpsertRequest(year=2026, month=4, targetCount=6),
            current_user=user,
        )
    )

    _create_completed_task(
        storage,
        owner_id=user.user_id,
        task_id='analysis-in-period-1',
        task_type=TaskType.PATENT_ANALYSIS.value,
        completed_at=datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc),
    )
    _create_completed_task(
        storage,
        owner_id=user.user_id,
        task_id='analysis-in-period-2',
        task_type=TaskType.PATENT_ANALYSIS.value,
        completed_at=datetime(2026, 4, 27, 9, 0, tzinfo=timezone.utc),
    )
    _create_completed_task(
        storage,
        owner_id=user.user_id,
        task_id='analysis-outside-period',
        task_type=TaskType.PATENT_ANALYSIS.value,
        completed_at=datetime(2026, 4, 28, 9, 0, tzinfo=timezone.utc),
    )
    _create_completed_task(
        storage,
        owner_id=user.user_id,
        task_id='reply-in-period',
        task_type=TaskType.AI_REPLY.value,
        completed_at=datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc),
    )

    dashboard = asyncio.run(account.get_account_dashboard(year=2026, month=4, current_user=user))

    assert dashboard.periodStart == '2026-03-27'
    assert dashboard.periodEnd == '2026-04-27'
    assert dashboard.periodLabel == '2026年4月结案周期'
    assert dashboard.targetMetricType == 'patent_analysis'
    assert dashboard.countBasis == 'completed_at'
    assert dashboard.monthTarget == 6
    assert dashboard.workMonth.totalCount == 2
    assert dashboard.workMonth.analysisCount == 2
    assert len(dashboard.dailySeries) == 32
    daily_map = {item.date: item.totalCreated for item in dashboard.dailySeries}
    assert daily_map['2026-03-27'] == 1
    assert daily_map['2026-04-10'] == 0
    assert daily_map['2026-04-27'] == 1
    assert [item.totalCreated for item in dashboard.weeklySeries] == [1, 0, 0, 1]


def test_settlement_period_handles_cross_year_boundary(monkeypatch, tmp_path):
    _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='u-period-cross-year')

    dashboard = asyncio.run(account.get_account_dashboard(year=2027, month=1, current_user=user))

    assert dashboard.periodStart == '2026-12-29'
    assert dashboard.periodEnd == '2027-01-26'
    assert dashboard.periodLabel == '2027年1月结案周期'
    assert len(dashboard.dailySeries) == 29


def test_profile_sanitizes_none_like_text(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:sub-1')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='sub-1',
            name='None',
            nickname='null',
            email='  user@example.com  ',
            phone='undefined',
            picture='',
        )
    )

    profile = asyncio.run(account.get_account_profile(current_user=user))
    assert profile.name is None
    assert profile.nickname is None
    assert profile.phone is None
    assert profile.picture is None
    assert profile.email == 'user@example.com'


def test_put_account_profile_updates_name_and_picture(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:sub-2')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='sub-2',
            name='旧名字',
            nickname='旧昵称',
            email='old@example.com',
            picture='https://unit.test/api/account/profile/avatar/local/old.png',
        )
    )

    updated = asyncio.run(
        account.put_account_profile(
            payload=AccountProfileUpdateRequest(
                name='新名字',
                picture='https://unit.test/api/account/profile/avatar/local/new.png',
            ),
            current_user=user,
        )
    )
    assert updated.name == '新名字'
    assert updated.picture == 'https://unit.test/api/account/profile/avatar/local/new.png'

    fetched = storage.get_user_by_owner_id(user.user_id)
    assert fetched is not None
    assert fetched.name == '新名字'
    assert fetched.picture == 'https://unit.test/api/account/profile/avatar/local/new.png'


def test_put_account_profile_rejects_empty_name(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:empty-name-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='empty-name-user',
            name='旧名字',
            email='empty@example.com',
            picture='https://example.com/old.png',
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            account.put_account_profile(
                payload=AccountProfileUpdateRequest(
                    name='',
                    picture='https://example.com/new.png',
                ),
                current_user=user,
            )
        )
    assert exc_info.value.status_code == 400


def test_put_account_profile_rejects_duplicate_name(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user_a = CurrentUser(user_id='authing:user-a')
    user_b = CurrentUser(user_id='authing:user-b')
    storage.upsert_authing_user(
        User(
            owner_id=user_a.user_id,
            authing_sub='user-a',
            name='重复名',
            email='a@example.com',
        )
    )
    storage.upsert_authing_user(
        User(
            owner_id=user_b.user_id,
            authing_sub='user-b',
            name='原名称',
            email='b@example.com',
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            account.put_account_profile(
                payload=AccountProfileUpdateRequest(
                    name='重复名',
                    picture='https://example.com/new.png',
                ),
                current_user=user_b,
            )
        )
    assert exc_info.value.status_code == 409


def test_get_account_notification_settings_requires_authing_user(monkeypatch, tmp_path):
    _mount_storage(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(account.get_account_notification_settings(current_user=CurrentUser(user_id='guest-user')))

    assert exc_info.value.status_code == 403


def test_account_notification_settings_roundtrip(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:notify-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='notify-user',
            name='通知用户',
            email='login@example.com',
        )
    )

    initial = asyncio.run(account.get_account_notification_settings(current_user=user))
    assert initial.notificationEmailEnabled is False
    assert initial.workNotificationEmail is None
    assert initial.personalNotificationEmail is None

    saved = asyncio.run(
        account.put_account_notification_settings(
            payload=AccountNotificationSettingsUpdateRequest(
                notificationEmailEnabled=True,
                workNotificationEmail='worker@example.com',
                personalNotificationEmail='home@example.com',
            ),
            current_user=user,
        )
    )
    assert saved.notificationEmailEnabled is True
    assert saved.workNotificationEmail == 'worker@example.com'
    assert saved.personalNotificationEmail == 'home@example.com'

    fetched = storage.get_user_by_owner_id(user.user_id)
    assert fetched is not None
    assert fetched.notification_email_enabled is True
    assert fetched.work_notification_email == 'worker@example.com'
    assert fetched.personal_notification_email == 'home@example.com'


def test_account_notification_settings_require_at_least_one_email_when_enabled(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:no-email-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='no-email-user',
            name='无邮箱用户',
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            account.put_account_notification_settings(
                payload=AccountNotificationSettingsUpdateRequest(
                    notificationEmailEnabled=True,
                    workNotificationEmail='',
                    personalNotificationEmail='',
                ),
                current_user=user,
            )
        )

    assert exc_info.value.status_code == 400


def test_account_notification_settings_reject_invalid_email(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:bad-email-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='bad-email-user',
            name='坏邮箱用户',
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            account.put_account_notification_settings(
                payload=AccountNotificationSettingsUpdateRequest(
                    notificationEmailEnabled=True,
                    workNotificationEmail='not-an-email',
                    personalNotificationEmail='',
                ),
                current_user=user,
            )
        )

    assert exc_info.value.status_code == 400


def test_account_notification_settings_allow_duplicate_addresses(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    user = CurrentUser(user_id='authing:dup-email-user')
    storage.upsert_authing_user(
        User(
            owner_id=user.user_id,
            authing_sub='dup-email-user',
            name='重复邮箱用户',
        )
    )

    saved = asyncio.run(
        account.put_account_notification_settings(
            payload=AccountNotificationSettingsUpdateRequest(
                notificationEmailEnabled=True,
                workNotificationEmail='same@example.com',
                personalNotificationEmail='same@example.com',
            ),
            current_user=user,
        )
    )

    assert saved.workNotificationEmail == 'same@example.com'
    assert saved.personalNotificationEmail == 'same@example.com'
