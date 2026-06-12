from __future__ import annotations

from backend.ai_search.service import AiSearchService
from backend.scripts.repair_ai_search_running_sessions import repair_running_sessions
from backend.storage import PipelineTaskManager, SQLiteTaskStorage
from patent_agents.ai_search.src.state import PHASE_IDLE, PHASE_RUNNING, get_ai_search_meta


def _build_service(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_running_repair.db")
    service = AiSearchService()
    service.task_manager = PipelineTaskManager(storage)
    service._enforce_daily_quota = lambda *_args, **_kwargs: None
    return service, storage


def test_repair_running_sessions_dry_run_and_apply(tmp_path) -> None:
    service, storage = _build_service(tmp_path)
    created = service.create_session("guest_ai_search")
    run = service.agent_runs._ensure_run(created.sessionId)
    service.agent_runs._mark_running(created.sessionId)
    service.agent_runs._append_event(
        created.sessionId,
        "run.completed",
        {"phase": PHASE_IDLE, "completionReason": "test"},
        run_id=str(run["run_id"]),
    )

    dry_run_report = repair_running_sessions(storage, apply=False, limit=100)

    assert dry_run_report["mode"] == "dry_run"
    assert dry_run_report["runningTaskCount"] == 1
    assert dry_run_report["wouldRepairCount"] == 1
    assert dry_run_report["repairedCount"] == 0
    assert dry_run_report["samples"][0]["reason"] == "terminal_run.completed"
    assert get_ai_search_meta(storage.get_task(created.sessionId))["current_phase"] == PHASE_RUNNING

    apply_report = repair_running_sessions(storage, apply=True, limit=100)

    assert apply_report["mode"] == "apply"
    assert apply_report["repairedCount"] == 1
    assert apply_report["samples"][0]["repairResult"]["reason"] == "terminal_run.completed"
    assert get_ai_search_meta(storage.get_task(created.sessionId))["current_phase"] == PHASE_IDLE
