from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.models import AccountMonthTargetUpsertRequest, AccountProfileUpdateRequest, CurrentUser
from backend.routes import account
from backend.storage import User
from backend.storage.sqlite_storage import SQLiteTaskStorage


def _mount_storage(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / 'month_target_test.db')
    monkeypatch.setattr(account, 'task_manager', SimpleNamespace(storage=storage))
    return storage


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
            picture='https://example.com/old.png',
        )
    )

    updated = asyncio.run(
        account.put_account_profile(
            payload=AccountProfileUpdateRequest(
                name='新名字',
                picture='https://example.com/new.png',
            ),
            current_user=user,
        )
    )
    assert updated.name == '新名字'
    assert updated.picture == 'https://example.com/new.png'

    fetched = storage.get_user_by_owner_id(user.user_id)
    assert fetched is not None
    assert fetched.name == '新名字'
    assert fetched.picture == 'https://example.com/new.png'
