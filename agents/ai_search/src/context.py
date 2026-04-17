"""AI Search runtime context backed by run/batch/result tables."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.ai_search.src.exceptions import ExecutionQueueTakeoverRequested
from agents.ai_search.src.execution_state import (
    DEFAULT_EXECUTION_POLICY,
    normalize_execution_plan,
)
from agents.ai_search.src.orchestration.action_runtime import (
    current_pending_action,
    open_pending_action,
    resolve_pending_action,
)
from agents.ai_search.src.orchestration.execution_runtime import (
    build_gap_progress,
    complete_session,
)
from agents.ai_search.src.main_agent.schemas import SearchPlanExecutionSpecInput
from agents.ai_search.src.main_agent.tools import build_main_agent_tools
from agents.ai_search.src.runtime import write_stream_event
from agents.ai_search.src.state import (
    PHASE_AWAITING_HUMAN_DECISION,
    PHASE_CLOSE_READ,
    PHASE_COARSE_SCREEN,
    PHASE_COLLECTING_REQUIREMENTS,
    PHASE_DRAFTING_PLAN,
    PHASE_EXECUTE_SEARCH,
    PHASE_FEATURE_COMPARISON,
    get_ai_search_meta,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from backend.time_utils import utc_now_z
from backend.utils import _build_r2_storage

_UNSET = object()


def _gap_signature(progress: Dict[str, Any]) -> Dict[str, int]:
    return {
        "limitation_gap_count": int(progress.get("limitation_gap_count") or 0),
        "coverage_gap_count": int(progress.get("coverage_gap_count") or 0),
        "follow_up_hint_count": int(progress.get("follow_up_hint_count") or 0),
        "weak_evidence_count": int(progress.get("weak_evidence_count") or 0),
    }


def _gap_signature_score(signature: Dict[str, Any]) -> int:
    if not isinstance(signature, dict):
        return 0
    return sum(
        int(signature.get(key) or 0)
        for key in ("limitation_gap_count", "coverage_gap_count", "follow_up_hint_count", "weak_evidence_count")
    )


def _readiness_rank(value: Any) -> int:
    mapping = {
        "unknown": 0,
        "needs_more_evidence": 1,
        "insufficient": 1,
        "partial": 2,
        "developing": 2,
        "ready": 3,
        "sufficient": 3,
        "enough": 3,
    }
    return mapping.get(str(value or "").strip().lower(), 0)


def _load_json_bytes(raw: Optional[bytes]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


class AiSearchAgentContext:
    def __init__(self, storage: Any, task_id: str) -> None:
        self.storage = storage
        self.task_id = task_id

    def emit_stream_event(self, runtime: Any | None, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        write_stream_event(
            getattr(runtime, "stream_writer", None) if runtime is not None else None,
            {"type": str(event_type or "").strip(), "payload": payload or {}},
        )

    def notify_snapshot_changed(self, runtime: Any | None, *, reason: str = "") -> None:
        self.emit_stream_event(runtime, "snapshot.changed", {"reason": str(reason or "").strip()})

    def _source_patent_summary(self) -> Dict[str, str]:
        payload = self.load_source_patent_data()
        bibliographic = payload.get("bibliographic_data") if isinstance(payload.get("bibliographic_data"), dict) else {}
        publication_number = str(
            bibliographic.get("publication_number") or bibliographic.get("application_number") or ""
        ).strip()
        invention_title = str(bibliographic.get("invention_title") or bibliographic.get("title") or "").strip()
        applicants = bibliographic.get("applicants") if isinstance(bibliographic.get("applicants"), list) else []
        applicant_names = [
            str(item.get("name") or item.get("applicant_name") or "").strip()
            for item in applicants
            if isinstance(item, dict)
        ]
        return {
            "publication_number": publication_number,
            "invention_title": invention_title,
            "primary_applicant": next((name for name in applicant_names if name), ""),
        }

    def _run_state(self, run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        state = run.get("human_decision_state") if isinstance(run, dict) else None
        return dict(state) if isinstance(state, dict) else {}

    def active_plan_version(self) -> int:
        task = self.storage.get_task(self.task_id)
        meta = get_ai_search_meta(task)
        version = int(meta.get("active_plan_version") or 0)
        if version > 0:
            return version
        latest = self.storage.get_ai_search_plan(self.task_id)
        return int(latest.get("plan_version") or 0) if latest else 0

    def current_phase(self) -> str:
        task = self.storage.get_task(self.task_id)
        meta = get_ai_search_meta(task)
        return str(meta.get("current_phase") or "").strip()

    def target_plan_version(self) -> int:
        current = self.current_planner_draft()
        version = int(current.get("plan_version") or current.get("target_plan_version") or 0)
        if version > 0:
            return version
        active_version = int(self.active_plan_version() or 0)
        if active_version > 0 and self.current_phase() == "drafting_plan":
            return active_version
        return int(self.storage.get_next_ai_search_plan_version(self.task_id) or 1)

    def active_run(self, plan_version: Optional[int] = None) -> Optional[Dict[str, Any]]:
        version = int(plan_version or self.active_plan_version() or 0)
        return self.storage.get_ai_search_run(self.task_id, plan_version=version) if version > 0 else self.storage.get_ai_search_run(self.task_id)

    def active_run_id(self, plan_version: Optional[int] = None) -> str:
        run = self.active_run(plan_version)
        return str(run.get("run_id") or "").strip() if run else ""

    def ensure_run(self, plan_version: int, *, phase: str) -> Dict[str, Any]:
        run = self.storage.get_ai_search_run(self.task_id, plan_version=int(plan_version))
        if run:
            self.storage.update_ai_search_run(
                self.task_id,
                str(run.get("run_id") or ""),
                phase=phase,
                status=phase_to_task_status(phase),
            )
            return self.storage.get_ai_search_run(self.task_id, plan_version=int(plan_version)) or run
        run_id = uuid.uuid4().hex
        self.storage.create_ai_search_run(
            {
                "run_id": run_id,
                "task_id": self.task_id,
                "plan_version": int(plan_version),
                "phase": phase,
                "status": phase_to_task_status(phase),
                "selected_document_count": 0,
                "human_decision_state": {},
            }
        )
        return self.storage.get_ai_search_run(self.task_id, run_id=run_id) or {}

    def update_task_phase(self, phase: str, *, runtime: Any | None = None, **ai_search_updates: Any) -> None:
        task = self.storage.get_task(self.task_id)
        summary_updates = dict(ai_search_updates)
        run_id = str(summary_updates.pop("run_id", "") or "").strip() or self.active_run_id(summary_updates.get("active_plan_version"))
        active_todo_id = summary_updates.pop("current_task", _UNSET)
        active_batch_id = summary_updates.pop("active_batch_id", _UNSET)
        metadata = merge_ai_search_meta(task, current_phase=phase, **summary_updates)
        self.storage.update_task(
            self.task_id,
            metadata=metadata,
            status=phase_to_task_status(phase),
            progress=phase_progress(phase),
            current_step=phase_step(phase),
        )
        if run_id:
            run_updates: Dict[str, Any] = {"phase": phase, "status": phase_to_task_status(phase)}
            if active_todo_id is not _UNSET:
                run_updates["active_retrieval_todo_id"] = active_todo_id
            if active_batch_id is not _UNSET:
                run_updates["active_batch_id"] = active_batch_id
            if "selected_document_count" in summary_updates:
                run_updates["selected_document_count"] = int(summary_updates.get("selected_document_count") or 0)
            self.storage.update_ai_search_run(self.task_id, run_id, **run_updates)
        self.emit_stream_event(runtime, "phase.changed", {"phase": phase})
        self.notify_snapshot_changed(runtime, reason="phase")

    def _normalized_todo(self, item: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        base = existing if isinstance(existing, dict) else {}
        state = base.get("state") if isinstance(base.get("state"), dict) else {}
        incoming_state = item.get("state") if isinstance(item.get("state"), dict) else {}
        return {
            "todo_id": str(item.get("todo_id") or base.get("todo_id") or "").strip(),
            "sub_plan_id": str(item.get("sub_plan_id") or base.get("sub_plan_id") or "").strip(),
            "step_id": str(item.get("step_id") or base.get("step_id") or "").strip(),
            "phase_key": str(item.get("phase_key") or base.get("phase_key") or "execute_search").strip() or "execute_search",
            "title": str(item.get("title") or base.get("title") or "").strip(),
            "status": str(item.get("status") or base.get("status") or "pending").strip() or "pending",
            "description": str(item.get("description") or base.get("description") or "").strip(),
            "started_at": item.get("started_at") or base.get("started_at"),
            "completed_at": item.get("completed_at") or base.get("completed_at"),
            "attempt_count": int(item.get("attempt_count") or base.get("attempt_count") or 0),
            "last_error": str(item.get("last_error") or base.get("last_error") or "").strip(),
            "resume_from": str(item.get("resume_from") or base.get("resume_from") or "").strip(),
            "state": {**state, **incoming_state},
        }

    def _current_todos(self, _task: Any | None = None) -> List[Dict[str, Any]]:
        run_id = self.active_run_id()
        if not run_id:
            task = self.storage.get_task(self.task_id)
            meta = get_ai_search_meta(task)
            raw = meta.get("draft_todos") if isinstance(meta.get("draft_todos"), list) else []
            return [self._normalized_todo(item) for item in raw if isinstance(item, dict)]
        return [self._normalized_todo(item) for item in self.storage.list_ai_search_retrieval_todos(run_id)]

    def _todo_map(self, task: Any | None = None) -> Dict[str, Dict[str, Any]]:
        return {str(item.get("todo_id") or ""): item for item in self._current_todos(task) if str(item.get("todo_id") or "").strip()}

    def current_todo(self) -> Optional[Dict[str, Any]]:
        run = self.active_run()
        if not run:
            return None
        todo_id = str(run.get("active_retrieval_todo_id") or "").strip()
        if not todo_id:
            return None
        return self._todo_map().get(todo_id)

    def replace_todos(self, todos: List[Dict[str, Any]], *, current_task: Any = _UNSET, runtime: Any | None = None) -> None:
        run = self.active_run()
        normalized = [self._normalized_todo(item) for item in todos if isinstance(item, dict)]
        if run:
            self.storage.replace_ai_search_retrieval_todos(str(run.get("run_id") or ""), self.task_id, int(run.get("plan_version") or 0), normalized)
            run_updates: Dict[str, Any] = {}
            if current_task is not _UNSET:
                run_updates["active_retrieval_todo_id"] = current_task
            if run_updates:
                self.storage.update_ai_search_run(self.task_id, str(run.get("run_id") or ""), **run_updates)
        else:
            task = self.storage.get_task(self.task_id)
            updates: Dict[str, Any] = {"draft_todos": normalized}
            if current_task is not _UNSET:
                updates["draft_current_task"] = current_task
            self.storage.update_task(self.task_id, metadata=merge_ai_search_meta(task, **updates))
        self.notify_snapshot_changed(runtime, reason="todos")

    def append_todos(self, todos: List[Dict[str, Any]], *, current_task: Any = _UNSET, runtime: Any | None = None) -> None:
        existing = self._current_todos()
        existing_ids = {str(item.get("todo_id") or "").strip() for item in existing}
        merged = list(existing)
        for item in todos:
            if not isinstance(item, dict):
                continue
            todo_id = str(item.get("todo_id") or "").strip()
            if not todo_id or todo_id in existing_ids:
                continue
            existing_ids.add(todo_id)
            merged.append(item)
        self.replace_todos(merged, current_task=current_task, runtime=runtime)

    def update_todo(
        self,
        todo_id: str,
        status: str,
        *,
        current_task: Any = _UNSET,
        resume_from: Optional[str] = None,
        last_error: Optional[str] = None,
        state_updates: Optional[Dict[str, Any]] = None,
        runtime: Any | None = None,
    ) -> None:
        run = self.active_run()
        if not run:
            return
        current = self.storage.get_ai_search_retrieval_todo(str(run.get("run_id") or ""), str(todo_id or "").strip())
        if not current:
            return
        next_item = self._normalized_todo(current)
        previous_status = str(next_item.get("status") or "pending").strip() or "pending"
        now = utc_now_z()
        next_item["status"] = status
        if status == "in_progress":
            if previous_status != "in_progress":
                next_item["attempt_count"] = int(next_item.get("attempt_count") or 0) + 1
            next_item["started_at"] = next_item.get("started_at") or now
            next_item["completed_at"] = None
        elif status == "completed":
            next_item["completed_at"] = next_item.get("completed_at") or now
            next_item["last_error"] = ""
            next_item["resume_from"] = "completed"
        elif status == "failed":
            next_item["last_error"] = str(last_error or next_item.get("last_error") or "").strip()
        elif last_error is not None:
            next_item["last_error"] = str(last_error or "").strip()
        if resume_from is not None and status != "completed":
            next_item["resume_from"] = str(resume_from or "").strip()
        if state_updates:
            next_state = next_item.get("state") if isinstance(next_item.get("state"), dict) else {}
            next_item["state"] = {**next_state, **state_updates}
        self.storage.update_ai_search_retrieval_todo(
            str(run.get("run_id") or ""),
            str(todo_id or "").strip(),
            status=next_item["status"],
            attempt_count=int(next_item.get("attempt_count") or 0),
            last_error=next_item.get("last_error"),
            resume_from=next_item.get("resume_from"),
            state_json=next_item.get("state") or {},
            started_at=next_item.get("started_at"),
            completed_at=next_item.get("completed_at"),
        )
        if current_task is not _UNSET:
            self.storage.update_ai_search_run(self.task_id, str(run.get("run_id") or ""), active_retrieval_todo_id=current_task)
        self.notify_snapshot_changed(runtime, reason="todos")

    def update_todos(
        self,
        todo_id: str,
        status: str,
        *,
        current_task: Any = _UNSET,
        resume_from: Optional[str] = None,
        last_error: Optional[str] = None,
        state_updates: Optional[Dict[str, Any]] = None,
        runtime: Any | None = None,
    ) -> None:
        resolved_todo_id = str(todo_id or "").strip()
        target = self._todo_map().get(resolved_todo_id)
        if target is None:
            target = self.current_todo()
        if target is None:
            return
        self.update_todo(
            str(target.get("todo_id") or "").strip(),
            status,
            current_task=current_task,
            resume_from=resume_from,
            last_error=last_error,
            state_updates=state_updates,
            runtime=runtime,
        )

    def first_pending_todo(self, *, phase_key: str = "") -> Optional[Dict[str, Any]]:
        for todo in self._current_todos():
            if str(todo.get("status") or "").strip() != "pending":
                continue
            if phase_key and str(todo.get("phase_key") or "").strip() != str(phase_key or "").strip():
                continue
            return todo
        return None

    def next_pending_todo(self, current_todo_id: str = "", *, phase_key: str = "") -> Optional[Dict[str, Any]]:
        seen_current = not current_todo_id
        for todo in self._current_todos():
            if not seen_current:
                if str(todo.get("todo_id") or "").strip() == str(current_todo_id or "").strip():
                    seen_current = True
                continue
            if str(todo.get("todo_id") or "").strip() == str(current_todo_id or "").strip():
                continue
            if str(todo.get("status") or "").strip() != "pending":
                continue
            if phase_key and str(todo.get("phase_key") or "").strip() != str(phase_key or "").strip():
                continue
            return todo
        return None

    def record_todo_failure(self, todo_id: str, error: str, *, current_task: str, resume_from: str) -> str:
        message = str(error or "unknown_error").strip() or "unknown_error"
        if str(todo_id or "").strip():
            self.update_todo(
                todo_id,
                "failed",
                current_task=current_task,
                last_error=message,
                resume_from=resume_from,
                state_updates={"last_failed_at": utc_now_z()},
            )
            run = self.active_run()
            if run:
                failed_todo = self.storage.get_ai_search_retrieval_todo(str(run.get("run_id") or ""), str(todo_id or "").strip()) or {}
                self.create_pending_action(
                    "resume",
                    {
                        "todo_id": str(todo_id or "").strip(),
                        "resume_from": str(resume_from or "").strip(),
                        "last_error": message,
                        "attempt_count": int(failed_todo.get("attempt_count") or 0),
                        "checkpoint_ref": self._current_checkpoint_ref(),
                    },
                    run_id=str(run.get("run_id") or ""),
                    plan_version=int(run.get("plan_version") or 0),
                    source="execution_resume",
                )
        return json.dumps({"ok": False, "error": message, "resume_from": resume_from}, ensure_ascii=False)

    def _current_checkpoint_ref(self) -> Dict[str, Any] | None:
        task = self.storage.get_task(self.task_id)
        meta = get_ai_search_meta(task)
        thread_id = str(meta.get("thread_id") or "").strip()
        if not thread_id:
            return None
        checkpoints = self.storage.list_ai_search_checkpoints(thread_id, limit=50)
        checkpoint_ns = ""
        checkpoint_id = ""
        for item in checkpoints:
            checkpoint_ns = str(item.get("checkpoint_ns") or "").strip()
            if checkpoint_ns and not checkpoint_ns.startswith("tools:"):
                checkpoint_id = str(item.get("checkpoint_id") or "").strip()
                break
        if not checkpoint_ns:
            return {"thread_id": thread_id}
        payload: Dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}
        if checkpoint_id:
            payload["checkpoint_id"] = checkpoint_id
        return payload

    def current_pending_action(self) -> Optional[Dict[str, Any]]:
        return current_pending_action(self)

    def create_pending_action(
        self,
        action_type: str,
        payload: Dict[str, Any],
        *,
        run_id: str = "",
        plan_version: int = 0,
        source: str = "agent_prompted",
        runtime: Any | None = None,
    ) -> Dict[str, Any]:
        return open_pending_action(
            self,
            action_type=action_type,
            source=source,
            payload=payload,
            run_id=run_id,
            plan_version=plan_version,
            runtime=runtime,
        )

    def resolve_pending_action(
        self,
        action_type: str = "",
        *,
        resolution: Optional[Dict[str, Any]] = None,
        runtime: Any | None = None,
    ) -> Optional[Dict[str, Any]]:
        return resolve_pending_action(
            self,
            expected_action_type=str(action_type or "").strip(),
            resolution=resolution,
            runtime=runtime,
        )

    def find_message_by_question_id(self, question_id: str) -> Optional[Dict[str, Any]]:
        for item in reversed(self.storage.list_ai_search_messages(self.task_id)):
            if str(item.get("question_id") or "") == str(question_id):
                return item
        return None

    def current_planner_draft(self) -> Dict[str, Any]:
        task = self.storage.get_task(self.task_id)
        meta = get_ai_search_meta(task)
        draft = meta.get("planner_draft")
        return dict(draft) if isinstance(draft, dict) else {}

    def clear_planner_draft(self, *, runtime: Any | None = None) -> None:
        task = self.storage.get_task(self.task_id)
        self.storage.update_task(self.task_id, metadata=merge_ai_search_meta(task, planner_draft=None))
        self.notify_snapshot_changed(runtime, reason="planner_draft")

    def _persist_planner_draft(
        self,
        *,
        current: Dict[str, Any],
        review_markdown: Any = _UNSET,
        execution_spec: Any = _UNSET,
        probe_findings: Any = _UNSET,
        draft_status: Any = _UNSET,
        finalized_at: Any = _UNSET,
        runtime: Any | None = None,
    ) -> Dict[str, Any]:
        task = self.storage.get_task(self.task_id)
        draft_version = int(current.get("draft_version") or 0) + 1
        next_execution_spec = current.get("execution_spec") if isinstance(current.get("execution_spec"), dict) else {}
        next_probe_findings = current.get("probe_findings") if isinstance(current.get("probe_findings"), dict) else None
        next_status = str(current.get("draft_status") or "drafting").strip() or "drafting"
        next_finalized_at = current.get("finalized_at")
        if review_markdown is not _UNSET:
            current["review_markdown"] = str(review_markdown or "").strip()
        if execution_spec is not _UNSET:
            next_execution_spec = execution_spec if isinstance(execution_spec, dict) else {}
        if probe_findings is not _UNSET:
            next_probe_findings = probe_findings if isinstance(probe_findings, dict) and probe_findings else None
        if draft_status is not _UNSET:
            next_status = str(draft_status or "").strip() or next_status
        if finalized_at is not _UNSET:
            next_finalized_at = finalized_at
        draft = {
            "draft_id": str(current.get("draft_id") or uuid.uuid4().hex[:12]),
            "draft_version": draft_version,
            "plan_version": int(current.get("plan_version") or self.target_plan_version() or 0) or None,
            "phase": self.current_phase() or "drafting_plan",
            "review_markdown": str(current.get("review_markdown") or "").strip(),
            "execution_spec": next_execution_spec,
            "probe_findings": next_probe_findings,
            "draft_status": next_status,
            "finalized_at": next_finalized_at,
            "committed_at": utc_now_z(),
        }
        self.storage.update_task(self.task_id, metadata=merge_ai_search_meta(task, planner_draft=draft))
        self.notify_snapshot_changed(runtime, reason="planner_draft")
        return draft

    def save_planner_review_markdown(self, review_markdown: str, *, runtime: Any | None = None) -> Dict[str, Any]:
        return self._persist_planner_draft(
            current=self.current_planner_draft(),
            review_markdown=review_markdown,
            draft_status="drafting",
            finalized_at=None,
            runtime=runtime,
        )

    def save_planner_execution_overview(
        self,
        *,
        search_scope: Dict[str, Any],
        constraints: Dict[str, Any],
        execution_policy: Dict[str, Any],
        probe_findings: Optional[Dict[str, Any]] = None,
        runtime: Any | None = None,
    ) -> Dict[str, Any]:
        current = self.current_planner_draft()
        current_spec = current.get("execution_spec") if isinstance(current.get("execution_spec"), dict) else {}
        next_spec = {
            "search_scope": search_scope if isinstance(search_scope, dict) else {},
            "constraints": constraints if isinstance(constraints, dict) else {},
            "execution_policy": execution_policy if isinstance(execution_policy, dict) else (current_spec.get("execution_policy") or DEFAULT_EXECUTION_POLICY),
            "sub_plans": current_spec.get("sub_plans") if isinstance(current_spec.get("sub_plans"), list) else [],
        }
        return self._persist_planner_draft(
            current=current,
            execution_spec=next_spec,
            probe_findings=probe_findings,
            draft_status="drafting",
            finalized_at=None,
            runtime=runtime,
        )

    def save_planner_probe_findings(
        self,
        probe_findings: Optional[Dict[str, Any]],
        *,
        runtime: Any | None = None,
    ) -> Dict[str, Any]:
        return self._persist_planner_draft(
            current=self.current_planner_draft(),
            probe_findings=probe_findings,
            draft_status="drafting",
            finalized_at=None,
            runtime=runtime,
        )

    def append_planner_sub_plan(self, sub_plan: Dict[str, Any], *, runtime: Any | None = None) -> Dict[str, Any]:
        current = self.current_planner_draft()
        current_spec = current.get("execution_spec") if isinstance(current.get("execution_spec"), dict) else {}
        current_sub_plans = [item for item in (current_spec.get("sub_plans") or []) if isinstance(item, dict)]
        normalized_sub_plan = sub_plan if isinstance(sub_plan, dict) else {}
        sub_plan_id = str(normalized_sub_plan.get("sub_plan_id") or "").strip()
        next_sub_plans: List[Dict[str, Any]] = []
        replaced = False
        for item in current_sub_plans:
            if sub_plan_id and str(item.get("sub_plan_id") or "").strip() == sub_plan_id:
                next_sub_plans.append(normalized_sub_plan)
                replaced = True
            else:
                next_sub_plans.append(item)
        if not replaced:
            next_sub_plans.append(normalized_sub_plan)
        next_spec = {
            "search_scope": current_spec.get("search_scope") if isinstance(current_spec.get("search_scope"), dict) else {},
            "constraints": current_spec.get("constraints") if isinstance(current_spec.get("constraints"), dict) else {},
            "execution_policy": current_spec.get("execution_policy") if isinstance(current_spec.get("execution_policy"), dict) else DEFAULT_EXECUTION_POLICY,
            "sub_plans": next_sub_plans,
        }
        return self._persist_planner_draft(
            current=current,
            execution_spec=next_spec,
            draft_status="drafting",
            finalized_at=None,
            runtime=runtime,
        )

    def finalize_planner_draft(self, *, runtime: Any | None = None) -> Dict[str, Any]:
        current = self.current_planner_draft()
        review_markdown = str(current.get("review_markdown") or "").strip()
        if not review_markdown:
            raise ValueError("planner draft 缺少 review_markdown。")
        execution_spec = current.get("execution_spec") if isinstance(current.get("execution_spec"), dict) else {}
        normalized = SearchPlanExecutionSpecInput.model_validate(execution_spec).model_dump(mode="python")
        return self._persist_planner_draft(
            current=current,
            execution_spec=normalized,
            draft_status="finalized",
            finalized_at=utc_now_z(),
            runtime=runtime,
        )

    def _search_scope_from_execution_spec(self, execution_spec: Dict[str, Any]) -> Dict[str, Any]:
        return execution_spec.get("search_scope") if isinstance(execution_spec.get("search_scope"), dict) else {}

    def _sub_plans_from_execution_spec(self, execution_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw = execution_spec.get("sub_plans") if isinstance(execution_spec.get("sub_plans"), list) else []
        return [item for item in raw if isinstance(item, dict)]

    def _aggregated_search_elements_from_execution_spec(self, execution_spec: Dict[str, Any]) -> Dict[str, Any]:
        search_scope = self._search_scope_from_execution_spec(execution_spec)
        aggregated: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for sub_plan in self._sub_plans_from_execution_spec(execution_spec):
            for item in sub_plan.get("search_elements") or []:
                if not isinstance(item, dict):
                    continue
                element_name = str(item.get("element_name") or item.get("feature") or item.get("name") or "").strip()
                block_id = str(item.get("block_id") or "").strip().upper()
                signature = f"{block_id}:{element_name}".strip(":")
                if not element_name or signature in seen:
                    continue
                seen.add(signature)
                aggregated.append(item)
        return {
            "status": "complete" if aggregated else "needs_answer",
            "objective": str(search_scope.get("objective") or "").strip(),
            "applicants": search_scope.get("applicants") if isinstance(search_scope.get("applicants"), list) else [],
            "filing_date": search_scope.get("filing_date"),
            "priority_date": search_scope.get("priority_date"),
            "search_elements": aggregated,
            "sub_plans": self._sub_plans_from_execution_spec(execution_spec),
        }

    def current_search_elements(self, plan_version: Optional[int] = None) -> Dict[str, Any]:
        version = int(plan_version or self.active_plan_version() or 0)
        if version > 0:
            plan = self.storage.get_ai_search_plan(self.task_id, version) or {}
            execution_spec = plan.get("execution_spec_json")
            if isinstance(execution_spec, dict):
                payload = self._aggregated_search_elements_from_execution_spec(execution_spec)
                if payload.get("search_elements"):
                    return payload
        for item in reversed(self.storage.list_ai_search_messages(self.task_id)):
            if str(item.get("kind") or "") != "search_elements_update":
                continue
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                return metadata
        return {}

    def latest_message_metadata(self, kind: str) -> Dict[str, Any]:
        for item in reversed(self.storage.list_ai_search_messages(self.task_id)):
            if str(item.get("kind") or "") != str(kind or ""):
                continue
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                return metadata
        return {}

    def current_execution_message_queue(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        resolved_run_id = str(run_id or self.active_run_id() or "").strip()
        if not resolved_run_id:
            return []
        return self.storage.list_ai_search_execution_queue_messages(
            self.task_id,
            resolved_run_id,
            statuses=["pending"],
        )

    def append_execution_message_queue(self, content: str) -> Optional[Dict[str, Any]]:
        resolved_content = str(content or "").strip()
        run = self.active_run()
        run_id = str(run.get("run_id") or "").strip() if isinstance(run, dict) else ""
        if not resolved_content or not run_id:
            return None
        ordinal = self.storage.get_next_ai_search_execution_queue_ordinal(self.task_id, run_id)
        queue_message_id = uuid.uuid4().hex
        created_at = utc_now_z()
        created = self.storage.create_ai_search_execution_queue_message(
            {
                "queue_message_id": queue_message_id,
                "task_id": self.task_id,
                "run_id": run_id,
                "content": resolved_content,
                "ordinal": ordinal,
                "status": "pending",
                "created_at": created_at,
                "updated_at": created_at,
            }
        )
        if not created:
            return None
        return self.storage.get_ai_search_execution_queue_message(queue_message_id)

    def delete_execution_message_queue(self, queue_message_id: str) -> bool:
        item = self.storage.get_ai_search_execution_queue_message(queue_message_id)
        if not item:
            return False
        if str(item.get("task_id") or "").strip() != self.task_id:
            return False
        if str(item.get("status") or "").strip() != "pending":
            return False
        return self.storage.update_ai_search_execution_queue_message(
            queue_message_id,
            status="deleted",
        )

    def _build_execution_queue_takeover_prompt(self, queued_messages: List[Dict[str, Any]]) -> str:
        lines = [
            "用户在执行阶段补充了新的调整要求。不要继续当前执行分支，把这些消息合并纳入接下来的重规划。",
            "这不是新会话；需要继承当前已经落库的检索、粗筛、精读或对比结果。",
            "",
            "执行中补充消息（按发送顺序）:",
        ]
        for index, item in enumerate(queued_messages, start=1):
            content = str(item.get("content") or "").strip()
            if content:
                lines.append(f"{index}. {content}")
        lines.extend(
            [
                "",
                "现在请立即回到 drafting_plan，先读取最新 planning/execution context，再基于这些补充要求调整检索计划。",
            ]
        )
        return "\n".join(lines).strip()

    def consume_execution_message_queue_for_takeover(self, *, runtime: Any | None = None) -> Optional[ExecutionQueueTakeoverRequested]:
        run = self.active_run()
        run_id = str(run.get("run_id") or "").strip() if isinstance(run, dict) else ""
        if not run_id:
            return None
        queued_messages = self.current_execution_message_queue(run_id)
        if not queued_messages:
            return None
        current_todo = self.current_todo()
        current_todo_id = str(current_todo.get("todo_id") or "").strip() if isinstance(current_todo, dict) else ""
        current_status = str(current_todo.get("status") or "").strip() if isinstance(current_todo, dict) else ""
        if current_todo_id and current_status == "in_progress":
            self.update_todo(
                current_todo_id,
                "paused",
                current_task=None,
                resume_from="execution_queue_takeover",
                state_updates={"queued_takeover": True},
                runtime=runtime,
            )
        for item in queued_messages:
            queue_message_id = str(item.get("queue_message_id") or "").strip()
            if queue_message_id:
                self.storage.update_ai_search_execution_queue_message(queue_message_id, status="consuming")
        for item in queued_messages:
            self.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": self.task_id,
                    "plan_version": int(run.get("plan_version") or 0) or None,
                    "role": "user",
                    "kind": "chat",
                    "content": str(item.get("content") or "").strip(),
                    "stream_status": "completed",
                    "metadata": {
                        "queuedDuringExecution": True,
                        "queuedMessageId": str(item.get("queue_message_id") or "").strip() or None,
                        "runId": run_id,
                    },
                }
            )
        for item in queued_messages:
            queue_message_id = str(item.get("queue_message_id") or "").strip()
            if queue_message_id:
                self.storage.update_ai_search_execution_queue_message(
                    queue_message_id,
                    status="consumed",
                    consumed_at=utc_now_z(),
                )
        self.clear_planner_draft(runtime=runtime)
        self.update_task_phase(
            "drafting_plan",
            runtime=runtime,
            active_plan_version=int(run.get("plan_version") or 0) or None,
            run_id=run_id,
            current_task=None,
            active_batch_id=None,
        )
        self.notify_snapshot_changed(runtime, reason="execution_message_queue")
        return ExecutionQueueTakeoverRequested(
            queued_messages=queued_messages,
            takeover_prompt=self._build_execution_queue_takeover_prompt(queued_messages),
        )

    def load_source_patent_data(self) -> Dict[str, Any]:
        task = self.storage.get_task(self.task_id)
        meta = get_ai_search_meta(task)
        source_task_id = str(meta.get("source_task_id") or "").strip()
        if not source_task_id:
            return {}
        source_task = self.storage.get_task(source_task_id)
        if not source_task:
            return {}
        patent_path = Path(str(getattr(source_task, "output_dir", "") or "")) / "patent.json"
        if patent_path.exists() and patent_path.is_file():
            try:
                payload = json.loads(patent_path.read_text(encoding="utf-8"))
            except Exception:
                payload = None
            if isinstance(payload, dict):
                return payload
        metadata = source_task.metadata if isinstance(source_task.metadata, dict) else {}
        output_files = metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {}
        patent_r2_key = str(output_files.get("patent_r2_key") or "").strip()
        if not patent_r2_key:
            return {}
        try:
            r2_storage = _build_r2_storage()
            payload = _load_json_bytes(r2_storage.get_bytes(patent_r2_key))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def list_execution_step_summaries(self, plan_version: int, *, sub_plan_id: str = "") -> List[Dict[str, Any]]:
        run_id = self.active_run_id(plan_version)
        if not run_id:
            return []
        return self.storage.list_ai_search_execution_summaries(run_id, sub_plan_id=sub_plan_id)

    def latest_execution_summary_for_todo(self, plan_version: int, todo_id: str) -> Optional[Dict[str, Any]]:
        target_todo_id = str(todo_id or "").strip()
        if not target_todo_id:
            return None
        for item in reversed(self.list_execution_step_summaries(plan_version)):
            if str(item.get("todo_id") or "").strip() == target_todo_id:
                return item
        return None

    def execution_plan_json(self, plan_version: Optional[int] = None) -> Dict[str, Any]:
        version = int(plan_version or self.active_plan_version() or 0)
        if version <= 0:
            return {}
        plan = self.storage.get_ai_search_plan(self.task_id, version) or {}
        return plan.get("execution_spec_json") if isinstance(plan.get("execution_spec_json"), dict) else {}

    def execution_policy(self, plan_version: Optional[int] = None) -> Dict[str, Any]:
        execution_plan = self.execution_plan_json(plan_version)
        if not execution_plan:
            return dict(DEFAULT_EXECUTION_POLICY)
        normalized = normalize_execution_plan(execution_plan, self.current_search_elements(plan_version))
        policy = normalized.get("execution_policy") if isinstance(normalized.get("execution_policy"), dict) else {}
        return {**DEFAULT_EXECUTION_POLICY, **policy}

    def latest_gap_context(self, plan_version: Optional[int] = None) -> Dict[str, Any]:
        run_id = self.active_run_id(plan_version)
        if not run_id:
            return {"close_read_result": {}, "feature_compare_result": {}}
        close_read_result = self.storage.get_latest_ai_search_close_read_result(run_id) or {}
        feature_compare_result = self.storage.get_ai_search_feature_comparison(self.task_id, run_id) or {}
        return {"close_read_result": close_read_result, "feature_compare_result": feature_compare_result}

    def latest_agent_markdown_message(
        self,
        source_agent: str,
        *,
        plan_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_source_agent = str(source_agent or "").strip()
        target_plan_version = int(plan_version or 0)
        for item in reversed(self.storage.list_ai_search_messages(self.task_id)):
            if str(item.get("role") or "").strip() != "assistant":
                continue
            if str(item.get("kind") or "").strip() != "chat":
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if str(metadata.get("source_agent") or "").strip() != normalized_source_agent:
                continue
            if target_plan_version > 0 and int(item.get("plan_version") or 0) != target_plan_version:
                continue
            return item
        return {}

    def latest_agent_markdown_content(
        self,
        source_agent: str,
        *,
        plan_version: Optional[int] = None,
    ) -> str:
        return str(
            self.latest_agent_markdown_message(
                source_agent,
                plan_version=plan_version,
            ).get("content")
            or ""
        ).strip()

    def reset_execution_control(
        self,
        plan_version: Optional[int] = None,
        *,
        clear_human_decision: bool = False,
        runtime: Any | None = None,
    ) -> None:
        run = self.active_run(plan_version)
        if not run:
            return
        version = int(run.get("plan_version") or 0)
        progress = build_gap_progress(self, version)
        state = self._run_state(run)
        state.update(
            {
                "execution_round_count": 0,
                "no_progress_round_count": 0,
                "last_selected_count": int(progress.get("selected_count") or 0),
                "last_readiness": str(progress.get("readiness") or "unknown").strip() or "unknown",
                "last_gap_signature": _gap_signature(progress),
                "last_new_unique_candidates": 0,
                "last_recommended_action": str(progress.get("recommended_action") or "").strip() or None,
                "processed_execution_summary_count": len(self.list_execution_step_summaries(version)) if version > 0 else 0,
                "last_exhaustion_reason": None,
                "last_exhaustion_summary": None,
            }
        )
        if clear_human_decision:
            state["human_decision_reason"] = None
            state["human_decision_summary"] = None
        self.storage.update_ai_search_run(self.task_id, str(run.get("run_id") or ""), human_decision_state=state)
        self.notify_snapshot_changed(runtime, reason="execution_control")

    def build_gap_strategy_seed_payload(self, plan_version: Optional[int] = None) -> Dict[str, Any]:
        version = int(plan_version or self.active_plan_version() or 0)
        gap_context = self.latest_gap_context(version)
        close_read_result = gap_context.get("close_read_result") if isinstance(gap_context.get("close_read_result"), dict) else {}
        feature_compare_result = gap_context.get("feature_compare_result") if isinstance(gap_context.get("feature_compare_result"), dict) else {}
        raw_gaps: List[Dict[str, Any]] = []
        for source_name, items in (
            ("close_read", close_read_result.get("limitation_gaps")),
            ("feature_compare", feature_compare_result.get("coverage_gaps")),
        ):
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                raw_gaps.append({"source": source_name, **item})
        follow_up_hints: List[str] = []
        for item in feature_compare_result.get("follow_up_search_hints") or []:
            text = str(item or "").strip()
            if text and text not in follow_up_hints:
                follow_up_hints.append(text)
        targeted_gaps: List[Dict[str, Any]] = []
        seed_batch_specs: List[Dict[str, Any]] = []
        for index, item in enumerate(raw_gaps, start=1):
            claim_id = str(item.get("claim_id") or "").strip()
            limitation_id = str(item.get("limitation_id") or "").strip()
            gap_type = str(item.get("gap_type") or item.get("source") or "coverage_gap").strip() or "coverage_gap"
            summary = str(item.get("gap_summary") or item.get("reason") or "").strip()
            suggested_keywords: List[str] = []
            for value in item.get("suggested_keywords") or []:
                text = str(value or "").strip()
                if text and text not in suggested_keywords:
                    suggested_keywords.append(text)
            suggested_pivots: List[str] = []
            for value in item.get("suggested_pivots") or []:
                text = str(value or "").strip()
                if text and text not in suggested_pivots:
                    suggested_pivots.append(text)
            if not suggested_keywords and summary:
                suggested_keywords.append(summary[:80])
            targeted_gap = {
                "claim_id": claim_id,
                "limitation_id": limitation_id,
                "gap_type": gap_type,
                "gap_summary": summary,
                "suggested_keywords": suggested_keywords,
                "suggested_pivots": suggested_pivots,
                "source": str(item.get("source") or "").strip(),
            }
            targeted_gaps.append(targeted_gap)
            seed_batch_specs.append(
                {
                    "batch_id": f"gap-{index}",
                    "goal": f"补强 {claim_id or 'unknown-claim'} {limitation_id or 'unknown-limitation'} 的现有技术证据",
                    "gap_type": gap_type,
                    "claim_id": claim_id,
                    "limitation_id": limitation_id,
                    "seed_terms": suggested_keywords,
                    "pivot_terms": suggested_pivots,
                }
            )
        planning_mode = "gap_replan" if targeted_gaps or follow_up_hints else "initial_plan"
        return {
            "plan_version": version,
            "planning_mode": planning_mode,
            "targeted_gaps": targeted_gaps,
            "follow_up_hints": follow_up_hints,
            "seed_batch_specs": seed_batch_specs,
            "gap_context": gap_context,
        }

    def evaluate_gap_progress_payload(self, plan_version: Optional[int] = None) -> Dict[str, Any]:
        return build_gap_progress(self, int(plan_version or self.active_plan_version() or 0))

    def build_main_agent_tools(self) -> List[Any]:
        return build_main_agent_tools()

    def build_search_elements_tools(self) -> List[Any]:
        from agents.ai_search.src.subagents.search_elements.tools import build_search_elements_tools

        return build_search_elements_tools()

    def build_planner_tools(self) -> List[Any]:
        from agents.ai_search.src.subagents.planner.tools import build_planner_tools

        return build_planner_tools()

    def build_query_executor_tools(self) -> List[Any]:
        from agents.ai_search.src.subagents.query_executor.tools import build_query_executor_tools

        return build_query_executor_tools()

    def build_plan_prober_tools(self) -> List[Any]:
        from agents.ai_search.src.subagents.plan_prober.tools import build_plan_prober_tools

        return build_plan_prober_tools()

    def build_coarse_screener_tools(self) -> List[Any]:
        from agents.ai_search.src.subagents.coarse_screener.tools import build_coarse_screener_tools

        return build_coarse_screener_tools()

    def build_close_reader_tools(self) -> List[Any]:
        from agents.ai_search.src.subagents.close_reader.tools import build_close_reader_tools

        return build_close_reader_tools()

    def build_feature_comparer_tools(self) -> List[Any]:
        from agents.ai_search.src.subagents.feature_comparer.tools import build_feature_comparer_tools

        return build_feature_comparer_tools()

    def execution_todos_from_plan(self, plan_version: int, execution_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        from agents.ai_search.src.execution_state import build_execution_todos

        normalized = normalize_execution_plan(execution_spec, self.current_search_elements(plan_version))
        return build_execution_todos(plan_version, normalized)
