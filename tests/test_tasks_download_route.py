from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.routes import tasks as tasks_route
from backend.storage.models import TaskStatus, TaskType
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage import SQLiteTaskStorage


def test_download_result_returns_ai_search_zip(monkeypatch, tmp_path) -> None:
    storage = SQLiteTaskStorage(tmp_path / "tasks_download.db")
    manager = PipelineTaskManager(storage)
    monkeypatch.setattr(tasks_route, "task_manager", manager)
    monkeypatch.setattr(tasks_route, "emit_system_log", lambda **kwargs: None)

    task = manager.create_task(
        owner_id="guest_ai_search",
        task_type=TaskType.AI_SEARCH.value,
        title="检索会话",
    )
    bundle_path = tmp_path / "ai_search_result_bundle.zip"
    bundle_path.write_bytes(b"PK\x03\x04")
    storage.update_task(
        task.id,
        status=TaskStatus.COMPLETED.value,
        metadata={"output_files": {"bundle_zip": str(bundle_path)}},
    )

    response = asyncio.run(
        tasks_route.download_result(task.id, SimpleNamespace(user_id="guest_ai_search"))
    )

    assert response.media_type == "application/zip"
    assert response.filename == "AI 检索结果_检索会话.zip"
