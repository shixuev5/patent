from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes.health import router
from config import VERSION


def test_root_route_returns_service_status():
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.json() == {
        "status": "正常",
        "service": "AI 分析 API",
        "version": VERSION,
        "health": "/api/health",
    }


def test_health_route_returns_status():
    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "正常"
    assert payload["version"] == VERSION
    assert payload["timestamp"]
