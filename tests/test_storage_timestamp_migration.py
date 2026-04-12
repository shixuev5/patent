from __future__ import annotations

from backend.scripts.migrate_storage_timestamps_to_utc import main as migrate_timestamps_main
from backend.storage import SQLiteTaskStorage


def test_summarize_admin_tasks_uses_shanghai_natural_day(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "tasks.db")
    monkeypatch.setattr(
        "backend.storage.repositories.tasks.local_recent_day_window_to_utc",
        lambda days: {
            1: ("2026-03-20T16:00:00Z", "2026-03-21T16:00:00Z"),
            7: ("2026-03-14T16:00:00Z", "2026-03-21T16:00:00Z"),
            30: ("2026-02-20T16:00:00Z", "2026-03-21T16:00:00Z"),
        }[days],
    )

    with storage._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO tasks (id, owner_id, task_type, pn, title, status, progress, created_at, updated_at, completed_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-in-window",
                "owner-1",
                "patent_analysis",
                None,
                "in-window",
                "completed",
                100,
                "2026-03-20T16:30:00Z",
                "2026-03-20T16:35:00Z",
                "2026-03-20T16:35:00Z",
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO tasks (id, owner_id, task_type, pn, title, status, progress, created_at, updated_at, completed_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-before-window",
                "owner-1",
                "patent_analysis",
                None,
                "before-window",
                "completed",
                100,
                "2026-03-20T15:59:59Z",
                "2026-03-20T16:05:00Z",
                "2026-03-20T16:05:00Z",
                None,
            ),
        )
        conn.commit()

    result = storage.summarize_admin_tasks()
    row = result["taskTypeWindows"][0]

    assert row["taskType"] == "patent_analysis"
    assert row["count1d"] == 1
    assert row["count7d"] == 2
    assert row["count30d"] == 2


def test_migration_script_dry_run_and_apply_for_sqlite(tmp_path):
    db_path = tmp_path / "tasks.db"
    checkpoint_path = tmp_path / "checkpoint.json"
    storage = SQLiteTaskStorage(db_path)

    with storage._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO tasks (id, owner_id, task_type, pn, title, status, progress, created_at, updated_at, completed_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-task",
                "owner-1",
                "patent_analysis",
                None,
                "legacy",
                "completed",
                100,
                "2026-03-20T15:03:08.990401",
                "2026-03-20T15:04:08.990401",
                "2026-03-20T15:05:08.990401",
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_llm_usage (
                task_id, owner_id, task_type, task_status,
                prompt_tokens, completion_tokens, total_tokens, reasoning_tokens,
                llm_call_count, estimated_cost_cny, price_missing, model_breakdown_json,
                first_usage_at, last_usage_at, currency, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-task",
                "owner-1",
                "patent_analysis",
                "completed",
                1,
                2,
                3,
                0,
                1,
                0.0,
                0,
                "{}",
                "2026-03-20T15:03:08.990401",
                "2026-03-20T15:05:08.990401",
                "CNY",
                "2026-03-20T15:03:08.990401",
                "2026-03-20T15:05:08.990401",
            ),
        )
        conn.commit()

    dry_run_exit = migrate_timestamps_main(
        [
            "--backend",
            "sqlite",
            "--sqlite-path",
            str(db_path),
            "--checkpoint-file",
            str(checkpoint_path),
            "--dry-run",
        ]
    )
    assert dry_run_exit == 0

    with storage._get_connection() as conn:
        task_row = conn.execute("SELECT created_at FROM tasks WHERE id = ?", ("legacy-task",)).fetchone()
        usage_row = conn.execute("SELECT last_usage_at FROM task_llm_usage WHERE task_id = ?", ("legacy-task",)).fetchone()
    assert task_row["created_at"] == "2026-03-20T15:03:08.990401"
    assert usage_row["last_usage_at"] == "2026-03-20T15:05:08.990401"

    checkpoint_path.unlink(missing_ok=True)
    apply_exit = migrate_timestamps_main(
        [
            "--backend",
            "sqlite",
            "--sqlite-path",
            str(db_path),
            "--checkpoint-file",
            str(checkpoint_path),
            "--apply",
        ]
    )
    assert apply_exit == 0

    with storage._get_connection() as conn:
        task_row = conn.execute(
            "SELECT created_at, updated_at, completed_at FROM tasks WHERE id = ?",
            ("legacy-task",),
        ).fetchone()
        usage_row = conn.execute(
            "SELECT first_usage_at, last_usage_at, created_at, updated_at FROM task_llm_usage WHERE task_id = ?",
            ("legacy-task",),
        ).fetchone()

    assert task_row["created_at"] == "2026-03-20T07:03:08.990401Z"
    assert task_row["updated_at"] == "2026-03-20T07:04:08.990401Z"
    assert task_row["completed_at"] == "2026-03-20T07:05:08.990401Z"
    assert usage_row["first_usage_at"] == "2026-03-20T15:03:08.990401Z"
    assert usage_row["last_usage_at"] == "2026-03-20T15:05:08.990401Z"
    assert usage_row["created_at"] == "2026-03-20T15:03:08.990401Z"
    assert usage_row["updated_at"] == "2026-03-20T15:05:08.990401Z"

    rerun_exit = migrate_timestamps_main(
        [
            "--backend",
            "sqlite",
            "--sqlite-path",
            str(db_path),
            "--checkpoint-file",
            str(checkpoint_path),
            "--apply",
        ]
    )
    assert rerun_exit == 0
