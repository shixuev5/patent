from __future__ import annotations

import os
import time

import pytest

from agents.common.search_clients.factory import SearchClientFactory
from agents.common.search_clients.zhihuiya import ZhihuiyaClient
from config import load_zhihuiya_accounts


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def _clear_zhihuiya_env(monkeypatch):
    for env_name in list(os.environ):
        if env_name.startswith("ZHIHUIYA_ACCOUNTS__"):
            monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv("ZHIHUIYA_USERNAME", raising=False)
    monkeypatch.delenv("ZHIHUIYA_PASSWORD", raising=False)


def _set_accounts_env(monkeypatch, accounts: list[tuple[str, str]]):
    _clear_zhihuiya_env(monkeypatch)
    for index, (username, password) in enumerate(accounts):
        monkeypatch.setenv(f"ZHIHUIYA_ACCOUNTS__{index}__USERNAME", username)
        monkeypatch.setenv(f"ZHIHUIYA_ACCOUNTS__{index}__PASSWORD", password)


def _reset_account_cooldowns():
    ZhihuiyaClient._account_cooldowns.clear()


def test_load_zhihuiya_accounts_supports_indexed_env_and_ignores_incomplete():
    accounts = load_zhihuiya_accounts(
        {
            "ZHIHUIYA_ACCOUNTS__0__USERNAME": "user-a@example.com",
            "ZHIHUIYA_ACCOUNTS__0__PASSWORD": "secret-a",
            "ZHIHUIYA_ACCOUNTS__2__USERNAME": "user-c@example.com",
            "ZHIHUIYA_ACCOUNTS__2__PASSWORD": "secret-c",
            "ZHIHUIYA_ACCOUNTS__3__USERNAME": "user-d@example.com",
            "ZHIHUIYA_ACCOUNTS__4__PASSWORD": "secret-e",
            "ZHIHUIYA_USERNAME": "legacy@example.com",
            "ZHIHUIYA_PASSWORD": "legacy-secret",
        }
    )

    assert accounts == [
        {"username": "user-a@example.com", "password": "secret-a"},
        {"username": "user-c@example.com", "password": "secret-c"},
    ]


def test_search_client_factory_returns_new_zhihuiya_instances(monkeypatch):
    _set_accounts_env(monkeypatch, [("user-a@example.com", "secret-a")])
    _reset_account_cooldowns()

    client_a = SearchClientFactory.get_client("zhihuiya")
    client_b = SearchClientFactory.get_client("zhihuiya")

    assert isinstance(client_a, ZhihuiyaClient)
    assert isinstance(client_b, ZhihuiyaClient)
    assert client_a is not client_b


def test_login_switches_to_next_account_after_failure(monkeypatch):
    _set_accounts_env(
        monkeypatch,
        [
            ("user-a@example.com", "secret-a"),
            ("user-b@example.com", "secret-b"),
        ],
    )
    _reset_account_cooldowns()
    client = ZhihuiyaClient()
    attempts = []

    monkeypatch.setattr("agents.common.search_clients.zhihuiya.random.shuffle", lambda items: None)
    monkeypatch.setattr(client, "_fetch_login_public_key", lambda: "public-key")

    def _fake_login_with_account(account, public_key_text):
        attempts.append(account["username"])
        if account["username"] == "user-a@example.com":
            raise RuntimeError("password incorrect")
        client.current_account = dict(account)
        client.token = "token-b"
        client.headers["Authorization"] = "Bearer token-b"

    monkeypatch.setattr(client, "_login_with_account", _fake_login_with_account)

    client._login()

    assert attempts == ["user-a@example.com", "user-b@example.com"]
    assert client.current_account == {
        "username": "user-b@example.com",
        "password": "secret-b",
    }
    assert ZhihuiyaClient._account_cooldowns["user-a@example.com"] > time.monotonic()


def test_login_skips_accounts_in_cooldown(monkeypatch):
    _set_accounts_env(
        monkeypatch,
        [
            ("user-a@example.com", "secret-a"),
            ("user-b@example.com", "secret-b"),
        ],
    )
    _reset_account_cooldowns()
    ZhihuiyaClient._mark_account_cooldown("user-a@example.com", "temporary failure")
    client = ZhihuiyaClient()
    attempts = []

    monkeypatch.setattr("agents.common.search_clients.zhihuiya.random.shuffle", lambda items: None)
    monkeypatch.setattr(client, "_fetch_login_public_key", lambda: "public-key")

    def _fake_login_with_account(account, public_key_text):
        attempts.append(account["username"])
        client.current_account = dict(account)
        client.token = "token-b"
        client.headers["Authorization"] = "Bearer token-b"

    monkeypatch.setattr(client, "_login_with_account", _fake_login_with_account)

    client._login()

    assert attempts == ["user-b@example.com"]


def test_login_raises_aggregated_error_when_all_accounts_fail(monkeypatch):
    _set_accounts_env(
        monkeypatch,
        [
            ("user-a@example.com", "secret-a"),
            ("user-b@example.com", "secret-b"),
        ],
    )
    _reset_account_cooldowns()
    client = ZhihuiyaClient()

    monkeypatch.setattr("agents.common.search_clients.zhihuiya.random.shuffle", lambda items: None)
    monkeypatch.setattr(client, "_fetch_login_public_key", lambda: "public-key")
    monkeypatch.setattr(
        client,
        "_login_with_account",
        lambda account, public_key_text: (_ for _ in ()).throw(RuntimeError("login failed")),
    )

    with pytest.raises(RuntimeError) as exc:
        client._login()

    assert "user-a@example.com: login failed" in str(exc.value)
    assert "user-b@example.com: login failed" in str(exc.value)


def test_post_request_reauth_switches_account_after_auth_failure(monkeypatch):
    _set_accounts_env(
        monkeypatch,
        [
            ("user-a@example.com", "secret-a"),
            ("user-b@example.com", "secret-b"),
        ],
    )
    _reset_account_cooldowns()
    client = ZhihuiyaClient()
    client.current_account = {
        "username": "user-a@example.com",
        "password": "secret-a",
    }
    client.token = "expired-token"
    client.headers["Authorization"] = "Bearer expired-token"
    request_auth_headers = []
    relogin_calls = []

    def _fake_login():
        relogin_calls.append("relogin")
        client.current_account = {
            "username": "user-b@example.com",
            "password": "secret-b",
        }
        client.token = "fresh-token"
        client.headers["Authorization"] = "Bearer fresh-token"

    def _fake_post(url, headers=None, json=None, timeout=None):
        request_auth_headers.append((headers or {}).get("Authorization"))
        if len(request_auth_headers) == 1:
            return _FakeResponse(
                {"message": "token expired"},
                status_code=401,
                text="token expired",
            )
        return _FakeResponse(
            {
                "status": True,
                "data": {
                    "patent_count": {"total_count": 1},
                    "patent_data": [],
                },
            }
        )

    monkeypatch.setattr(client, "_login", _fake_login)
    monkeypatch.setattr(client.session, "post", _fake_post)

    response = client._do_post_request("https://example.com/search", {"q": "battery"})

    assert request_auth_headers == ["Bearer expired-token", "Bearer fresh-token"]
    assert relogin_calls == ["relogin"]
    assert response["total"] == 1
    assert client.current_account == {
        "username": "user-b@example.com",
        "password": "secret-b",
    }
    assert ZhihuiyaClient._account_cooldowns["user-a@example.com"] > time.monotonic()
