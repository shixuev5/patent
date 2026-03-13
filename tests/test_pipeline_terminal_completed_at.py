from backend.storage.models import TaskStatus
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage.sqlite_storage import SQLiteTaskStorage


def test_fail_task_sets_completed_at(tmp_path) -> None:
    storage = SQLiteTaskStorage(tmp_path / "terminal_fail.db")
    manager = PipelineTaskManager(storage=storage)
    task = manager.create_task()

    assert manager.fail_task(task.id, "mock error")
    saved = manager.get_task(task.id)
    assert saved is not None
    assert saved.status == TaskStatus.FAILED
    assert saved.completed_at is not None


def test_cancel_task_sets_completed_at(tmp_path) -> None:
    storage = SQLiteTaskStorage(tmp_path / "terminal_cancel.db")
    manager = PipelineTaskManager(storage=storage)
    task = manager.create_task()

    assert manager.cancel_task(task.id, "manual cancel")
    saved = manager.get_task(task.id)
    assert saved is not None
    assert saved.status == TaskStatus.CANCELLED
    assert saved.completed_at is not None


def test_terminal_completed_at_is_not_overwritten_on_repeat_cancel(tmp_path) -> None:
    storage = SQLiteTaskStorage(tmp_path / "terminal_repeat.db")
    manager = PipelineTaskManager(storage=storage)
    task = manager.create_task()

    assert manager.cancel_task(task.id, "first cancel")
    first_saved = manager.get_task(task.id)
    assert first_saved is not None
    assert first_saved.completed_at is not None
    first_completed_at = first_saved.completed_at

    assert manager.cancel_task(task.id, "second cancel")
    second_saved = manager.get_task(task.id)
    assert second_saved is not None
    assert second_saved.completed_at == first_completed_at

