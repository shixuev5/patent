from __future__ import annotations

import asyncio
import io
from pathlib import Path
from threading import Event

import pytest
from fastapi import HTTPException, UploadFile

from backend.models import CurrentUser
from backend.routes import tasks as tasks_route
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage.sqlite_storage import SQLiteTaskStorage
from config import settings


class _ImmediateDoneTask:
    def add_done_callback(self, callback):
        callback(self)


def _fake_create_task(coro):
    coro.close()
    return _ImmediateDoneTask()


def _mount_task_manager(monkeypatch, tmp_path: Path) -> PipelineTaskManager:
    storage = SQLiteTaskStorage(tmp_path / "task_actions.db")
    manager = PipelineTaskManager(storage=storage)
    monkeypatch.setattr(tasks_route, "task_manager", manager)
    monkeypatch.setattr(tasks_route, "emit_system_log", lambda **kwargs: None)
    monkeypatch.setattr(tasks_route, "_enforce_daily_quota", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks_route.asyncio, "create_task", _fake_create_task)
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
    tasks_route.RUNNING_TASKS.clear()
    tasks_route.PATENT_CHECKPOINTERS.clear()
    tasks_route.AI_REVIEW_CHECKPOINTERS.clear()
    tasks_route.OAR_CHECKPOINTERS.clear()
    return manager


def _write_input_file(path: Path, content: bytes = b"test") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def _upload(filename: str, content: bytes = b"test") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


def test_cancel_ai_reply_task_keeps_inputs(monkeypatch, tmp_path: Path) -> None:
    manager = _mount_task_manager(monkeypatch, tmp_path)
    task = manager.create_task(owner_id="authing:user-1", task_type="ai_reply", title="oa")
    manager.start_task(task.id)
    office_action_path = _write_input_file(tmp_path / "uploads" / task.id / "office_action" / "office_action_oa.pdf", b"oa")
    manager.storage.update_task(
        task.id,
        metadata={
            "task_type": "ai_reply",
            "input_files": [
                {
                    "file_type": "office_action",
                    "original_name": "oa.pdf",
                    "stored_path": office_action_path,
                }
            ],
        },
    )
    runtime = Event()
    tasks_route.RUNNING_TASKS[task.id] = runtime

    response = asyncio.run(tasks_route.cancel_task(task.id, CurrentUser(user_id="authing:user-1")))

    refreshed = manager.get_task(task.id)
    assert response.status == "cancelled"
    assert refreshed is not None
    assert refreshed.status.value == "cancelled"
    assert runtime.is_set()
    assert refreshed.metadata["input_files"][0]["stored_path"] == office_action_path
    assert Path(office_action_path).exists()


def test_cancel_task_is_idempotent_for_cancelled_status(monkeypatch, tmp_path: Path) -> None:
    manager = _mount_task_manager(monkeypatch, tmp_path)
    task = manager.create_task(owner_id="authing:user-2", task_type="ai_reply", title="oa")
    manager.cancel_task(task.id, "任务已取消")

    response = asyncio.run(tasks_route.cancel_task(task.id, CurrentUser(user_id="authing:user-2")))

    assert response.status == "cancelled"
    refreshed = manager.get_task(task.id)
    assert refreshed is not None
    assert refreshed.status.value == "cancelled"


def test_cancel_task_rejects_completed_task(monkeypatch, tmp_path: Path) -> None:
    manager = _mount_task_manager(monkeypatch, tmp_path)
    task = manager.create_task(owner_id="authing:user-3", task_type="ai_reply", title="oa")
    manager.complete_task(task.id, output_files={"pdf": "dummy.pdf"})

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(tasks_route.cancel_task(task.id, CurrentUser(user_id="authing:user-3")))

    assert exc_info.value.status_code == 409


def test_retry_ai_reply_task_copies_input_files(monkeypatch, tmp_path: Path) -> None:
    manager = _mount_task_manager(monkeypatch, tmp_path)
    source_task = manager.create_task(owner_id="authing:user-4", task_type="ai_reply", title="oa")
    office_action_path = _write_input_file(tmp_path / "uploads" / source_task.id / "office_action" / "office_action_oa.pdf", b"oa")
    response_path = _write_input_file(tmp_path / "uploads" / source_task.id / "office_action" / "response_reply.docx", b"reply")
    manager.storage.update_task(
        source_task.id,
        metadata={
            "task_type": "ai_reply",
            "input_files": [
                {
                    "file_type": "office_action",
                    "original_name": "oa.pdf",
                    "stored_path": office_action_path,
                },
                {
                    "file_type": "response",
                    "original_name": "reply.docx",
                    "stored_path": response_path,
                },
            ],
        },
    )
    manager.cancel_task(source_task.id, "任务已取消")

    response = asyncio.run(tasks_route.retry_task(source_task.id, CurrentUser(user_id="authing:user-4")))

    retried = manager.get_task(response.taskId)
    assert retried is not None
    assert retried.metadata["retry_of"] == source_task.id
    copied_inputs = retried.metadata["input_files"]
    assert len(copied_inputs) == 2
    assert copied_inputs[0]["stored_path"] != office_action_path
    assert copied_inputs[1]["stored_path"] != response_path
    assert Path(copied_inputs[0]["stored_path"]).read_bytes() == b"oa"
    assert Path(copied_inputs[1]["stored_path"]).read_bytes() == b"reply"


def test_retry_ai_reply_task_rejects_missing_input_files(monkeypatch, tmp_path: Path) -> None:
    manager = _mount_task_manager(monkeypatch, tmp_path)
    task = manager.create_task(owner_id="authing:user-5", task_type="ai_reply", title="oa")
    manager.storage.update_task(
        task.id,
        metadata={
            "task_type": "ai_reply",
            "input_files": [
                {
                    "file_type": "office_action",
                    "original_name": "oa.pdf",
                    "stored_path": str(tmp_path / "missing.pdf"),
                }
            ],
        },
    )
    manager.fail_task(task.id, "boom")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(tasks_route.retry_task(task.id, CurrentUser(user_id="authing:user-5")))

    assert exc_info.value.status_code == 409


def test_retry_patent_analysis_task_supports_pn_only(monkeypatch, tmp_path: Path) -> None:
    manager = _mount_task_manager(monkeypatch, tmp_path)
    task = manager.create_task(owner_id="authing:user-6", task_type="patent_analysis", pn="CN123456A", title="CN123456A")
    manager.storage.update_task(task.id, metadata={"task_type": "patent_analysis", "input_files": []})
    manager.fail_task(task.id, "boom")

    response = asyncio.run(tasks_route.retry_task(task.id, CurrentUser(user_id="authing:user-6")))

    retried = manager.get_task(response.taskId)
    assert retried is not None
    assert retried.pn == "CN123456A"
    assert retried.metadata["input_files"] == []


def test_retry_ai_review_task_supports_upload_copy(monkeypatch, tmp_path: Path) -> None:
    manager = _mount_task_manager(monkeypatch, tmp_path)
    task = manager.create_task(owner_id="authing:user-7", task_type="ai_review", pn="CN765432A", title="CN765432A")
    source_pdf = _write_input_file(tmp_path / "uploads" / task.id / "patent" / "source_original.pdf", b"pdf")
    manager.storage.update_task(
        task.id,
        metadata={
            "task_type": "ai_review",
            "input_files": [
                {
                    "file_type": "patent_pdf",
                    "original_name": "original.pdf",
                    "stored_path": source_pdf,
                    "sha256": "stale",
                }
            ],
        },
    )
    manager.fail_task(task.id, "boom")

    response = asyncio.run(tasks_route.retry_task(task.id, CurrentUser(user_id="authing:user-7")))

    retried = manager.get_task(response.taskId)
    assert retried is not None
    copied_input = retried.metadata["input_files"][0]
    assert copied_input["stored_path"] != source_pdf
    assert Path(copied_input["stored_path"]).read_bytes() == b"pdf"
    assert copied_input["sha256"]


def test_delete_rejects_running_task(monkeypatch, tmp_path: Path) -> None:
    manager = _mount_task_manager(monkeypatch, tmp_path)
    task = manager.create_task(owner_id="authing:user-8", task_type="ai_reply", title="oa")
    manager.start_task(task.id)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(tasks_route.delete_task(task.id, CurrentUser(user_id="authing:user-8")))

    assert exc_info.value.status_code == 409


def test_clear_tasks_deletes_only_terminal_tasks(monkeypatch, tmp_path: Path) -> None:
    manager = _mount_task_manager(monkeypatch, tmp_path)
    completed_task = manager.create_task(owner_id="authing:user-9", task_type="patent_analysis", pn="CN1", title="CN1")
    failed_task = manager.create_task(owner_id="authing:user-9", task_type="ai_reply", title="oa")
    running_task = manager.create_task(owner_id="authing:user-9", task_type="ai_review", pn="CN2", title="CN2")
    manager.complete_task(completed_task.id, output_files={"pdf": "dummy.pdf"})
    manager.fail_task(failed_task.id, "boom")
    manager.start_task(running_task.id)

    result = asyncio.run(tasks_route.clear_tasks(CurrentUser(user_id="authing:user-9")))

    assert result == {"deleted": 2, "skipped_running": 1}
    remaining_ids = {task.id for task in manager.list_tasks(owner_id="authing:user-9", limit=10)}
    assert completed_task.id not in remaining_ids
    assert failed_task.id not in remaining_ids
    assert manager.get_task(running_task.id) is not None
