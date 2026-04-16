from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.error_handlers import register_exception_handlers
from backend.routes import account as account_routes
from backend.storage.errors import StorageRateLimitedError


def test_internal_wechat_delivery_claim_returns_503_with_retry_after(monkeypatch):
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

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "7"
    assert response.json() == {
        "detail": {
            "code": "STORAGE_UNAVAILABLE",
            "message": "存储服务暂不可用，请稍后重试。",
        }
    }
