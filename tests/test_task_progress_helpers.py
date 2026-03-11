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


def test_live_progress_cache_owner_scope_and_payload() -> None:
    task_id = "task-cache-1"
    tasks_route._clear_live_progress(task_id)

    tasks_route._set_live_progress(
        task_id,
        owner_id="owner-1",
        task_type="patent_analysis",
        status="processing",
        progress=42,
        step="处理中",
        pn="CN123",
    )
    own_payload = tasks_route._get_live_progress(task_id, "owner-1")
    foreign_payload = tasks_route._get_live_progress(task_id, "owner-2")

    assert own_payload is not None
    assert own_payload.get("progress") == 42
    assert own_payload.get("step") == "处理中"
    assert foreign_payload is None

    tasks_route._clear_live_progress(task_id)
    assert tasks_route._get_live_progress(task_id, "owner-1") is None


def test_build_progress_data_supports_heartbeat_and_error_mapping() -> None:
    heartbeat = tasks_route._build_progress_data(
        task_id="abc12345",
        task_type="patent_analysis",
        progress=55,
        step="知识提取",
        status="processing",
        pn="CN123",
        heartbeat=True,
    )
    assert heartbeat["status"] == "processing"
    assert heartbeat["heartbeat"] is True

    failed = tasks_route._build_progress_data(
        task_id="abc12345",
        task_type="patent_analysis",
        progress=70,
        step="报告生成",
        status="failed",
        pn="CN123",
        error="限流失败",
    )
    assert failed["status"] == "error"
    assert failed["error"] == "限流失败"
