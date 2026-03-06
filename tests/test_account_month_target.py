from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.models import AccountMonthTargetUpsertRequest, CurrentUser
from backend.routes import account
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
