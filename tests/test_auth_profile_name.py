from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.routes import auth as auth_routes
from backend.storage import User
from backend.storage.sqlite_storage import SQLiteTaskStorage


def _mount_storage(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "auth_profile_name_test.db")
    monkeypatch.setattr(auth_routes, "task_manager", SimpleNamespace(storage=storage))
    return storage


def test_authing_exchange_generates_unique_name_when_missing(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    storage.upsert_authing_user(
        User(
            owner_id="authing:other",
            authing_sub="other",
            name="用户abcdef",
            email="other@example.com",
        )
    )

    uuid_values = iter(
        [
            SimpleNamespace(hex="abcdef00000000000000000000000000"),
            SimpleNamespace(hex="12345600000000000000000000000000"),
        ]
    )
    monkeypatch.setattr(auth_routes.uuid, "uuid4", lambda: next(uuid_values))
    monkeypatch.setattr(auth_routes, "_verify_authing_id_token", lambda _token: {"sub": "sub-1"})
    monkeypatch.setattr(auth_routes, "_issue_token", lambda _owner_id: ("test-token", 0))

    response = asyncio.run(
        auth_routes.exchange_authing_token(
            payload=auth_routes.AuthingTokenExchangeRequest(idToken="fake-id-token")
        )
    )
    assert response.user.name == "用户123456"

    saved = storage.get_user_by_owner_id("authing:sub-1")
    assert saved is not None
    assert saved.name == "用户123456"


def test_authing_exchange_keeps_existing_name(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    storage.upsert_authing_user(
        User(
            owner_id="authing:sub-2",
            authing_sub="sub-2",
            name="本地名称",
            email="u2@example.com",
        )
    )

    monkeypatch.setattr(auth_routes, "_verify_authing_id_token", lambda _token: {"sub": "sub-2", "name": "Authing 名称"})
    monkeypatch.setattr(auth_routes, "_issue_token", lambda _owner_id: ("test-token", 0))

    response = asyncio.run(
        auth_routes.exchange_authing_token(
            payload=auth_routes.AuthingTokenExchangeRequest(idToken="fake-id-token")
        )
    )
    assert response.user.name == "本地名称"

    saved = storage.get_user_by_owner_id("authing:sub-2")
    assert saved is not None
    assert saved.name == "本地名称"
