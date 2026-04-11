from __future__ import annotations

import asyncio
import importlib
import sys

from backend.storage import pipeline_adapter
from backend.system_logs import LazySystemLogStorageProxy


def test_importing_main_and_entering_lifespan_does_not_initialize_storage(monkeypatch):
    calls: list[str] = []

    def _fake_get_task_storage():
        calls.append("called")
        return object()

    monkeypatch.setattr(pipeline_adapter, "get_task_storage", _fake_get_task_storage)
    pipeline_adapter._lazy_pipeline_manager._manager = None

    for module_name in list(sys.modules):
        if module_name == "backend.main" or module_name == "backend.routes" or module_name.startswith("backend.routes."):
            sys.modules.pop(module_name, None)

    main_module = importlib.import_module("backend.main")

    assert calls == []

    configured: list[object] = []
    monkeypatch.setattr(main_module, "configure_system_log_storage", configured.append)
    monkeypatch.setattr(main_module, "set_system_log_db_persistence_ready", lambda ready: None)
    monkeypatch.setattr(main_module, "start_system_log_cleanup_loop", lambda: None)

    async def _stop_cleanup_loop():
        return None

    monkeypatch.setattr(main_module, "stop_system_log_cleanup_loop", _stop_cleanup_loop)

    async def _run_lifespan():
        async with main_module.lifespan(main_module.app):
            return None

    asyncio.run(_run_lifespan())

    assert calls == []
    assert len(configured) == 1
    assert isinstance(configured[0], LazySystemLogStorageProxy)

    pipeline_adapter._lazy_pipeline_manager._manager = None
