from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.routes import auth as auth_routes
from backend.storage import User
from backend.storage import SQLiteTaskStorage


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


def test_authing_exchange_keeps_existing_role_when_claim_missing(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    storage.upsert_authing_user(
        User(
            owner_id="authing:sub-3",
            authing_sub="sub-3",
            role="admin",
            name="管理员",
            email="u3@example.com",
        )
    )

    monkeypatch.setattr(
        auth_routes,
        "_verify_authing_id_token",
        lambda _token: {"sub": "sub-3", "name": "Authing 名称"},
    )
    monkeypatch.setattr(auth_routes, "_issue_token", lambda _owner_id: ("test-token", 0))

    response = asyncio.run(
        auth_routes.exchange_authing_token(
            payload=auth_routes.AuthingTokenExchangeRequest(idToken="fake-id-token")
        )
    )
    assert response.user.name == "管理员"

    saved = storage.get_user_by_owner_id("authing:sub-3")
    assert saved is not None
    assert saved.role == "admin"


def test_authing_exchange_protects_profile_fields_but_updates_contact(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    storage.upsert_authing_user(
        User(
            owner_id="authing:sub-4",
            authing_sub="sub-4",
            role="admin",
            name="本地名称",
            nickname="本地昵称",
            email="old@example.com",
            phone="111111",
            picture="https://unit.test/old.png",
        )
    )

    monkeypatch.setattr(
        auth_routes,
        "_verify_authing_id_token",
        lambda _token: {
            "sub": "sub-4",
            "name": "Authing 名称",
            "nickname": "Authing 昵称",
            "email": "new@example.com",
            "phone_number": "222222",
            "picture": "https://unit.test/new.png",
            "roles": ["user"],
        },
    )
    monkeypatch.setattr(auth_routes, "_issue_token", lambda _owner_id: ("test-token", 0))

    response = asyncio.run(
        auth_routes.exchange_authing_token(
            payload=auth_routes.AuthingTokenExchangeRequest(idToken="fake-id-token")
        )
    )
    assert response.user.name == "本地名称"
    assert response.user.nickname == "本地昵称"
    assert response.user.picture == "https://unit.test/old.png"
    assert response.user.email == "new@example.com"
    assert response.user.phone == "222222"

    saved = storage.get_user_by_owner_id("authing:sub-4")
    assert saved is not None
    assert saved.role == "admin"
    assert saved.name == "本地名称"
    assert saved.nickname == "本地昵称"
    assert saved.picture == "https://unit.test/old.png"
    assert saved.email == "new@example.com"
    assert saved.phone == "222222"
