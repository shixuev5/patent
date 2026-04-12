from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.models import CurrentUser
from backend.routes import auth as auth_routes
from backend.storage import SQLiteTaskStorage


def _mount_storage(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "auth_session_refresh_test.db")
    monkeypatch.setattr(auth_routes, "task_manager", SimpleNamespace(storage=storage))
    return storage


def test_guest_auth_refresh_rotates_token(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    auth = asyncio.run(auth_routes.create_guest_auth())
    old_hash = auth_routes._hash_refresh_token(auth.refresh_token)
    old_session = storage.get_refresh_session(old_hash)
    assert old_session is not None
    assert old_session.revoked_at is None

    refreshed = asyncio.run(
        auth_routes.refresh_auth_session(
            auth_routes.RefreshTokenRequest(refresh_token=auth.refresh_token)
        )
    )
    assert refreshed.user_id == auth.user_id
    assert refreshed.auth_type == "guest"
    assert refreshed.refresh_token != auth.refresh_token
    assert refreshed.access_token

    old_after = storage.get_refresh_session(old_hash)
    assert old_after is not None
    assert old_after.revoked_at is not None
    assert old_after.replaced_by_token_hash == auth_routes._hash_refresh_token(refreshed.refresh_token)


def test_logout_revokes_given_refresh_token(monkeypatch, tmp_path):
    storage = _mount_storage(monkeypatch, tmp_path)
    auth = asyncio.run(auth_routes.create_guest_auth())
    token_hash = auth_routes._hash_refresh_token(auth.refresh_token)
    session_before = storage.get_refresh_session(token_hash)
    assert session_before is not None
    assert session_before.revoked_at is None

    result = asyncio.run(
        auth_routes.logout_auth_session(
            payload=auth_routes.LogoutRequest(refresh_token=auth.refresh_token),
            current_user=CurrentUser(user_id=auth.user_id),
        )
    )
    assert result["success"] is True
    assert result["revoked_count"] == 1

    session_after = storage.get_refresh_session(token_hash)
    assert session_after is not None
    assert session_after.revoked_at is not None


def test_refresh_rejects_invalid_token(monkeypatch, tmp_path):
    _mount_storage(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            auth_routes.refresh_auth_session(
                auth_routes.RefreshTokenRequest(refresh_token="invalid-refresh-token")
            )
        )
    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "REFRESH_TOKEN_INVALID"
