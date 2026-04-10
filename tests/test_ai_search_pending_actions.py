from __future__ import annotations

from datetime import datetime

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.orchestration.action_runtime import (
    cancel_pending_action,
    current_pending_action,
    open_pending_action,
    resolve_pending_action,
)
from agents.ai_search.src.state import (
    PHASE_AWAITING_HUMAN_DECISION,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_EXECUTE_SEARCH,
    default_ai_search_meta,
    get_ai_search_meta,
    merge_ai_search_meta,
)
from backend.storage import Task, TaskStatus, TaskType
from backend.storage.sqlite_storage import SQLiteTaskStorage


def _build_context(tmp_path, *, phase: str = PHASE_EXECUTE_SEARCH) -> tuple[AiSearchAgentContext, SQLiteTaskStorage]:
    storage = SQLiteTaskStorage(tmp_path / "ai_search_pending_actions.db")
    now = datetime.now()
    task = Task(
        id="task-pending",
        owner_id="guest_ai_search",
        task_type=TaskType.AI_SEARCH.value,
        status=TaskStatus.PROCESSING,
        created_at=now,
        updated_at=now,
        metadata={"ai_search": default_ai_search_meta("thread-pending")},
    )
    storage.create_task(task)
    storage.update_task(
        task.id,
        metadata=merge_ai_search_meta(storage.get_task(task.id), current_phase=phase),
    )
    return AiSearchAgentContext(storage, task.id), storage


def test_open_pending_action_supersedes_previous_and_keeps_single_active(tmp_path):
    context, storage = _build_context(tmp_path, phase="drafting_plan")

    question = open_pending_action(
        context,
        action_type="question",
        source="agent_prompted",
        payload={"question_id": "q-1", "prompt": "补充核心特征"},
    )
    plan_confirmation = open_pending_action(
        context,
        action_type="plan_confirmation",
        source="plan_gate",
        payload={"plan_version": 2, "plan_summary": "计划摘要"},
        plan_version=2,
    )

    current = current_pending_action(context)
    superseded = storage.get_ai_search_pending_action_by_id(str(question.get("action_id") or ""))
    task = storage.get_task(context.task_id)
    meta = get_ai_search_meta(task)

    assert current is not None
    assert current["action_id"] == plan_confirmation["action_id"]
    assert current["action_type"] == "plan_confirmation"
    assert superseded is not None
    assert superseded["status"] == "superseded"
    assert superseded["superseded_by"] == plan_confirmation["action_id"]
    assert meta["current_phase"] == PHASE_AWAITING_PLAN_CONFIRMATION


def test_resolve_and_cancel_pending_action_update_lifecycle_fields(tmp_path):
    context, storage = _build_context(tmp_path, phase="drafting_plan")

    question = open_pending_action(
        context,
        action_type="question",
        source="agent_prompted",
        payload={"question_id": "q-2", "prompt": "需要更多限定"},
    )
    resolve_pending_action(
        context,
        expected_action_type="question",
        resolution={"answer": "限定条件 A"},
    )
    resolved = storage.get_ai_search_pending_action_by_id(str(question.get("action_id") or ""))

    decision = open_pending_action(
        context,
        action_type="human_decision",
        source="execution_exhaustion",
        payload={"reason": "no_progress_limit_reached"},
        plan_version=1,
    )
    cancel_pending_action(context)
    cancelled = storage.get_ai_search_pending_action_by_id(str(decision.get("action_id") or ""))
    task = storage.get_task(context.task_id)
    meta = get_ai_search_meta(task)

    assert resolved is not None
    assert resolved["status"] == "resolved"
    assert resolved["resolution"]["answer"] == "限定条件 A"
    assert resolved["resolved_at"]
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["resolved_at"]
    assert meta["current_phase"] == PHASE_AWAITING_HUMAN_DECISION


def test_record_todo_failure_creates_resume_pending_action(tmp_path):
    context, storage = _build_context(tmp_path, phase=PHASE_EXECUTE_SEARCH)

    run = context.ensure_run(1, phase=PHASE_EXECUTE_SEARCH)
    context.replace_todos(
        [
            {
                "todo_id": "plan_1:sub_plan_1:step_1",
                "sub_plan_id": "sub_plan_1",
                "step_id": "step_1",
                "title": "执行步骤 1",
                "description": "首轮召回",
                "status": "in_progress",
                "attempt_count": 1,
                "resume_from": "run_execution_step.commit",
            }
        ],
        current_task="plan_1:sub_plan_1:step_1",
    )
    context.update_task_phase(
        PHASE_EXECUTE_SEARCH,
        active_plan_version=1,
        run_id=str(run.get("run_id") or ""),
        current_task="plan_1:sub_plan_1:step_1",
    )

    context.record_todo_failure(
        "plan_1:sub_plan_1:step_1",
        "timeout",
        current_task="plan_1:sub_plan_1:step_1",
        resume_from="run_execution_step",
    )

    pending = current_pending_action(context)
    task = storage.get_task(context.task_id)
    meta = get_ai_search_meta(task)

    assert pending is not None
    assert pending["action_type"] == "resume"
    assert pending["source"] == "execution_resume"
    assert pending["plan_version"] == 1
    assert pending["payload"]["todo_id"] == "plan_1:sub_plan_1:step_1"
    assert pending["payload"]["resume_from"] == "run_execution_step"
    assert pending["payload"]["last_error"] == "timeout"
    assert pending["payload"]["attempt_count"] == 1
    assert pending["payload"]["checkpoint_ref"]["thread_id"] == "thread-pending"
    assert meta["current_phase"] == PHASE_EXECUTE_SEARCH
