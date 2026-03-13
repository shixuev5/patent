from backend.storage.models import TaskStatus
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage.sqlite_storage import SQLiteTaskStorage


def test_sqlite_init_backfills_failed_and_cancelled_completed_at(tmp_path) -> None:
    db_path = tmp_path / "terminal_backfill.db"
    storage = SQLiteTaskStorage(db_path)
    manager = PipelineTaskManager(storage=storage)

    failed_task = manager.create_task()
    cancelled_task = manager.create_task()
    pending_task = manager.create_task()

    assert storage.update_task(failed_task.id, status=TaskStatus.FAILED.value)
    assert storage.update_task(cancelled_task.id, status=TaskStatus.CANCELLED.value)

    failed_before = storage.get_task(failed_task.id)
    cancelled_before = storage.get_task(cancelled_task.id)
    assert failed_before is not None and failed_before.completed_at is None
    assert cancelled_before is not None and cancelled_before.completed_at is None

    # 重新初始化会触发历史数据回填
    reopened = SQLiteTaskStorage(db_path)
    failed_after = reopened.get_task(failed_task.id)
    cancelled_after = reopened.get_task(cancelled_task.id)
    pending_after = reopened.get_task(pending_task.id)

    assert failed_after is not None
    assert cancelled_after is not None
    assert pending_after is not None
    assert failed_after.completed_at is not None
    assert cancelled_after.completed_at is not None
    assert failed_after.completed_at.isoformat() == failed_before.updated_at.isoformat()
    assert cancelled_after.completed_at.isoformat() == cancelled_before.updated_at.isoformat()
    assert pending_after.completed_at is None


def test_sqlite_init_backfill_does_not_override_existing_completed_at(tmp_path) -> None:
    db_path = tmp_path / "terminal_backfill_keep.db"
    storage = SQLiteTaskStorage(db_path)
    manager = PipelineTaskManager(storage=storage)
    task = manager.create_task()

    fixed_completed_at = "2025-01-02T03:04:05"
    assert storage.update_task(
        task.id,
        status=TaskStatus.CANCELLED.value,
        completed_at=fixed_completed_at,
    )

    reopened = SQLiteTaskStorage(db_path)
    after = reopened.get_task(task.id)
    assert after is not None
    assert after.completed_at is not None
    assert after.completed_at.isoformat() == fixed_completed_at

