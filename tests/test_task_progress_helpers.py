from backend.routes import tasks as tasks_route


def test_should_persist_progress_update_with_throttle_window() -> None:
    assert tasks_route._should_persist_progress_update(
        previous_step="步骤A",
        next_step="步骤A",
        last_persist_at=0.0,
        now=1.0,
    )
    assert not tasks_route._should_persist_progress_update(
        previous_step="步骤A",
        next_step="步骤A",
        last_persist_at=10.0,
        now=12.0,
        throttle_seconds=3.0,
    )
    assert tasks_route._should_persist_progress_update(
        previous_step="步骤A",
        next_step="步骤A",
        last_persist_at=10.0,
        now=13.2,
        throttle_seconds=3.0,
    )
    assert tasks_route._should_persist_progress_update(
        previous_step="步骤A",
        next_step="步骤B",
        last_persist_at=11.0,
        now=11.1,
        throttle_seconds=3.0,
    )


def test_should_persist_progress_update_respects_minimum_throttle_guard() -> None:
    assert not tasks_route._should_persist_progress_update(
        previous_step="步骤A",
        next_step="步骤A",
        last_persist_at=1.0,
        now=1.05,
        throttle_seconds=0.0,
    )
    assert tasks_route._should_persist_progress_update(
        previous_step="步骤A",
        next_step="步骤A",
        last_persist_at=1.0,
        now=1.11,
        throttle_seconds=0.0,
    )
