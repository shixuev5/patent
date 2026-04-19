from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.error_handlers import register_exception_handlers
from backend.routes import account as account_routes
from backend.storage.errors import StorageRateLimitedError


def test_internal_wechat_delivery_claim_degrades_to_empty_result_when_storage_rate_limited(monkeypatch):
    class _RateLimitedStorage:
        def claim_wechat_delivery_jobs(self, limit: int = 1):
            raise StorageRateLimitedError("D1 rate limited", retry_after_seconds=7)

    monkeypatch.setattr(account_routes, "task_manager", SimpleNamespace(storage=_RateLimitedStorage()))
    monkeypatch.setattr(account_routes.settings, "WECHAT_INTEGRATION_ENABLED", True)
    monkeypatch.setattr(account_routes.settings, "INTERNAL_GATEWAY_TOKEN", "internal-test-token")

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(account_routes.router)

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/internal/wechat/delivery-jobs/claim",
        headers={"X-Internal-Gateway-Token": "internal-test-token"},
        json={"limit": 1},
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


def test_internal_wechat_runtime_snapshot_degrades_to_empty_result_when_storage_rate_limited(monkeypatch):
    class _RateLimitedStorage:
        def list_active_wechat_bindings(self):
            raise StorageRateLimitedError("D1 rate limited", retry_after_seconds=5)

        def list_pending_wechat_login_sessions(self):
            raise AssertionError("should not continue reading after rate limit")

    monkeypatch.setattr(account_routes, "task_manager", SimpleNamespace(storage=_RateLimitedStorage()))
    monkeypatch.setattr(account_routes.settings, "WECHAT_INTEGRATION_ENABLED", True)
    monkeypatch.setattr(account_routes.settings, "INTERNAL_GATEWAY_TOKEN", "internal-test-token")

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(account_routes.router)

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/api/internal/wechat/runtime-snapshot",
        headers={"X-Internal-Gateway-Token": "internal-test-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "activeBindings": [],
        "pendingLoginSessions": [],
    }
