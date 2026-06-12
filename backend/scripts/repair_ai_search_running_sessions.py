"""
Inspect and repair AI Search sessions stuck in running phase.

By default this script is read-only. Use --apply to write repairs.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import TYPE_CHECKING, Any, Iterable, Literal, Optional

from dotenv import load_dotenv

from backend.ai_search.agent_run_service import (
    SNAPSHOT_STALE_RUNNING_REPAIR_GRACE_SECONDS,
    STOP_SATISFIED_SUBSCRIBE_STALE_SECONDS,
)
from backend.storage import D1TaskStorage, PipelineTaskManager, SQLiteTaskStorage, TaskStatus, TaskType
from patent_agents.ai_search.src.runtime import AiSearchRuntimeContext
from patent_agents.ai_search.src.state import PHASE_IDLE, PHASE_RUNNING, get_ai_search_meta

if TYPE_CHECKING:
    from backend.ai_search.service import AiSearchService


BackendType = Literal["sqlite", "d1"]


def _make_storage(backend: BackendType, sqlite_path: Optional[str]) -> Any:
    if backend == "sqlite":
        return SQLiteTaskStorage(sqlite_path)
    return D1TaskStorage(
        account_id=os.getenv("D1_ACCOUNT_ID", "").strip(),
        database_id=os.getenv("D1_DATABASE_ID", "").strip(),
        api_token=os.getenv("D1_API_TOKEN", "").strip(),
        api_base_url=os.getenv("D1_API_BASE_URL", "https://api.cloudflare.com/client/v4").strip(),
    )


def _make_service(storage: Any) -> "AiSearchService":
    from backend.ai_search.service import AiSearchService

    service = AiSearchService()
    service.task_manager = PipelineTaskManager(storage)
    service._enforce_daily_quota = lambda *_args, **_kwargs: None
    return service


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _fetch_ai_search_tasks(
    storage: Any,
    *,
    limit: int,
    session_id: str = "",
    owner_id: str = "",
) -> list[Any]:
    where = ["deleted_at IS NULL", "task_type = ?"]
    params: list[Any] = [TaskType.AI_SEARCH.value]
    if session_id:
        where.append("id = ?")
        params.append(session_id)
    if owner_id:
        where.append("owner_id = ?")
        params.append(owner_id)
    rows = storage._fetchall(
        f"""
        SELECT *
        FROM tasks
        WHERE {' AND '.join(where)}
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        [*params, max(1, int(limit or 1))],
    )
    return [storage._row_to_task(row) for row in rows]


def _task_sample(task: Any, *, run_id: str = "", run: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    meta = get_ai_search_meta(task)
    return {
        "taskId": task.id,
        "ownerId": task.owner_id,
        "title": task.title,
        "phase": str(meta.get("current_phase") or PHASE_IDLE),
        "runId": run_id or str(meta.get("current_run_id") or ""),
        "runPhase": str((run or {}).get("phase") or ""),
        "runStatus": str((run or {}).get("status") or ""),
        "updatedAt": task.updated_at.isoformat() if getattr(task, "updated_at", None) else None,
    }


def _assess_running_task(service: AiSearchService, task: Any) -> dict[str, Any]:
    runner = service.agent_runs
    meta = get_ai_search_meta(task)
    phase = str(meta.get("current_phase") or PHASE_IDLE).strip() or PHASE_IDLE
    if phase != PHASE_RUNNING:
        return {
            **_task_sample(task),
            "wouldRepair": False,
            "reason": "not_running",
        }

    run_id = runner._active_run_id(task.id)
    run = service.storage.get_ai_search_run(task.id, run_id) if run_id else None
    sample = _task_sample(task, run_id=run_id, run=run)

    terminal_event = runner._latest_terminal_run_event(task.id, run_id)
    if terminal_event:
        event_type = str(terminal_event.get("event_type") or "").strip()
        reason = "terminal_failed_event" if event_type == "run.failed" else f"terminal_{event_type}"
        return {
            **sample,
            "wouldRepair": True,
            "reason": reason,
            "latestEventSeq": int(terminal_event.get("seq") or 0),
            "latestEventType": event_type,
        }

    if run:
        run_phase = str(run.get("phase") or "").strip()
        run_status = str(run.get("status") or "").strip()
        if run_phase and run_phase != PHASE_RUNNING:
            reason = "run_failed_phase" if run_phase == "failed" or run_status == TaskStatus.FAILED.value else "run_not_running_phase"
            return {**sample, "wouldRepair": True, "reason": reason}
        if run_status in {TaskStatus.CANCELLED.value, TaskStatus.COMPLETED.value}:
            return {**sample, "wouldRepair": True, "reason": f"run_{run_status}_status"}
        if run_status == TaskStatus.FAILED.value:
            return {**sample, "wouldRepair": True, "reason": "run_failed_status"}

    stop_event = runner._latest_stop_satisfied_event(task.id)
    if stop_event:
        stop_age_seconds = runner._event_age_seconds(stop_event)
        if stop_age_seconds >= STOP_SATISFIED_SUBSCRIBE_STALE_SECONDS:
            return {
                **sample,
                "wouldRepair": True,
                "reason": "stale_stop_satisfied",
                "latestEventSeq": int(stop_event.get("seq") or 0),
                "latestEventAgeSeconds": int(stop_age_seconds),
            }

    if run_id:
        plan_version = _safe_int(meta.get("active_plan_version") or (run or {}).get("plan_version") or 1, 1)
        runtime = AiSearchRuntimeContext(service.storage, task.id, run_id, plan_version)
        deadline_seconds = runner._run_deadline_seconds(runtime)
        latest_event = service.storage.get_latest_ai_search_stream_event(task.id)
        age_seconds = (
            runner._event_age_seconds(latest_event)
            if isinstance(latest_event, dict)
            else runner._timestamp_age_seconds((run or {}).get("created_at"))
        )
        if age_seconds >= deadline_seconds + SNAPSHOT_STALE_RUNNING_REPAIR_GRACE_SECONDS:
            return {
                **sample,
                "wouldRepair": True,
                "reason": "deadline_stale_running",
                "deadlineSeconds": deadline_seconds,
                "latestEventAgeSeconds": int(age_seconds),
            }

    return {**sample, "wouldRepair": False, "reason": "running_recent"}


def repair_running_sessions(
    storage: Any,
    *,
    apply: bool = False,
    limit: int = 500,
    session_id: str = "",
    owner_id: str = "",
    sample_limit: int = 50,
) -> dict[str, Any]:
    service = _make_service(storage)
    tasks = _fetch_ai_search_tasks(
        storage,
        limit=limit,
        session_id=str(session_id or "").strip(),
        owner_id=str(owner_id or "").strip(),
    )
    samples: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    scanned_running = 0
    would_repair = 0
    repaired = 0
    skipped = 0

    for task in tasks:
        try:
            assessment = _assess_running_task(service, task)
            if assessment.get("reason") == "not_running":
                continue
            scanned_running += 1
            if assessment.get("wouldRepair"):
                would_repair += 1
            else:
                skipped += 1

            result: Optional[dict[str, Any]] = None
            if apply and assessment.get("wouldRepair"):
                result = service.agent_runs.repair_stale_running_state(task.id, str(task.owner_id or ""))
                if result.get("repaired"):
                    repaired += 1
                else:
                    skipped += 1
                assessment["repairResult"] = result

            if len(samples) < max(0, int(sample_limit or 0)):
                samples.append(assessment)
        except Exception as exc:
            errors.append({"taskId": getattr(task, "id", ""), "error": str(exc)})

    return {
        "mode": "apply" if apply else "dry_run",
        "scannedTaskCount": len(tasks),
        "runningTaskCount": scanned_running,
        "wouldRepairCount": would_repair,
        "repairedCount": repaired,
        "skippedRunningCount": skipped,
        "errorCount": len(errors),
        "samples": samples,
        "errors": errors[: max(0, int(sample_limit or 0))],
    }


def _parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect or repair AI Search sessions stuck in running phase.")
    parser.add_argument("--backend", choices=("sqlite", "d1"), required=True)
    parser.add_argument("--sqlite-path", default=None)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--owner-id", default="")
    parser.add_argument("--sample-limit", type=int, default=50)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Write repairs to storage.")
    mode.add_argument("--dry-run", action="store_true", help="Only report what would be repaired. This is the default.")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Iterable[str]] = None) -> int:
    load_dotenv()
    args = _parse_args(argv)
    storage = _make_storage(args.backend, args.sqlite_path)
    report = repair_running_sessions(
        storage,
        apply=bool(args.apply),
        limit=max(1, int(args.limit or 1)),
        session_id=args.session_id,
        owner_id=args.owner_id,
        sample_limit=max(0, int(args.sample_limit or 0)),
    )
    report["backend"] = args.backend
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
