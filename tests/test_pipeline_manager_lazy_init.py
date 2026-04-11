from __future__ import annotations

import importlib
import sys

from backend.storage import pipeline_adapter


def test_get_pipeline_manager_defers_storage_initialization(monkeypatch):
    calls: list[str] = []

    def _fake_get_task_storage():
        calls.append("called")
        return object()

    monkeypatch.setattr(pipeline_adapter, "get_task_storage", _fake_get_task_storage)
    pipeline_adapter._lazy_pipeline_manager._manager = None

    manager = pipeline_adapter.get_pipeline_manager()

    assert calls == []

    _ = manager.storage

    assert calls == ["called"]
    assert manager is pipeline_adapter.get_pipeline_manager()

    pipeline_adapter._lazy_pipeline_manager._manager = None


def test_importing_routes_does_not_initialize_storage(monkeypatch):
    calls: list[str] = []

    def _fake_get_task_storage():
        calls.append("called")
        return object()

    monkeypatch.setattr(pipeline_adapter, "get_task_storage", _fake_get_task_storage)
    pipeline_adapter._lazy_pipeline_manager._manager = None

    for module_name in list(sys.modules):
        if module_name == "backend.routes" or module_name.startswith("backend.routes."):
            sys.modules.pop(module_name, None)

    routes_module = importlib.import_module("backend.routes")

    assert calls == []

    auth_module = importlib.import_module("backend.routes.auth")
    _ = auth_module.task_manager.storage

    assert calls == ["called"]
    assert hasattr(routes_module, "router")

    pipeline_adapter._lazy_pipeline_manager._manager = None
