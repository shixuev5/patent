"""智能检索共享上下文及角色专用工具构建包装。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.ai_search.src.execution_state import (
    DEFAULT_EXECUTION_POLICY,
    build_execution_todos,
    normalize_execution_plan,
    resolve_plan_step,
)
from agents.ai_search.src.main_agent.tools import build_main_agent_tools
from agents.ai_search.src.runtime import write_stream_event
from agents.ai_search.src.state import (
    PHASE_AWAITING_HUMAN_DECISION,
    get_ai_search_meta,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from agents.ai_search.src.subagents.close_reader.tools import build_close_reader_tools
from agents.ai_search.src.subagents.coarse_screener.tools import build_coarse_screener_tools
from agents.ai_search.src.subagents.feature_comparer.tools import build_feature_comparer_tools
from agents.ai_search.src.subagents.plan_prober.tools import build_plan_prober_tools
from agents.ai_search.src.subagents.query_executor.tools import build_query_executor_tools
from agents.ai_search.src.subagents.search_elements.tools import build_search_elements_tools
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
    return sum(int(signature.get(key) or 0) for key in (
        "limitation_gap_count",
        "coverage_gap_count",
        "follow_up_hint_count",
        "weak_evidence_count",
    ))


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
            {
                "type": str(event_type or "").strip(),
                "payload": payload or {},
            },
        )

    def notify_snapshot_changed(self, runtime: Any | None, *, reason: str = "") -> None:
        self.emit_stream_event(runtime, "snapshot.changed", {"reason": str(reason or "").strip()})

    def update_task_phase(self, phase: str, *, runtime: Any | None = None, **ai_search_updates: Any) -> None:
        task = self.storage.get_task(self.task_id)
        metadata = merge_ai_search_meta(task, current_phase=phase, **ai_search_updates)
        self.storage.update_task(
            self.task_id,
            metadata=metadata,
            status=phase_to_task_status(phase),
            progress=phase_progress(phase),
            current_step=phase_step(phase),
        )
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
            "phase_key": str(item.get("phase_key") or base.get("phase_key") or "").strip(),
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

    def _current_todos(self, task: Any | None = None) -> List[Dict[str, Any]]:
        current_task = task or self.storage.get_task(self.task_id)
        meta = get_ai_search_meta(current_task)
        raw = meta.get("todos") if isinstance(meta.get("todos"), list) else []
        return [self._normalized_todo(item) for item in raw if isinstance(item, dict)]

    def _todo_map(self, task: Any | None = None) -> Dict[str, Dict[str, Any]]:
        todos = self._current_todos(task)
        return {str(item.get("todo_id") or ""): item for item in todos if str(item.get("todo_id") or "").strip()}

    def current_todo(self) -> Optional[Dict[str, Any]]:
        task = self.storage.get_task(self.task_id)
        meta = get_ai_search_meta(task)
        current_task = str(meta.get("current_task") or "").strip()
        if not current_task:
            return None
        todo_map = self._todo_map(task)
        return todo_map.get(current_task)

    def replace_todos(self, todos: List[Dict[str, Any]], *, current_task: Any = _UNSET, runtime: Any | None = None) -> None:
        task = self.storage.get_task(self.task_id)
        normalized = [self._normalized_todo(item) for item in todos if isinstance(item, dict)]
        updates: Dict[str, Any] = {"todos": normalized}
        if current_task is not _UNSET:
            updates["current_task"] = current_task
        self.storage.update_task(self.task_id, metadata=merge_ai_search_meta(task, **updates))
        self.notify_snapshot_changed(runtime, reason="todos")

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
        task = self.storage.get_task(self.task_id)
        todos = self._current_todos(task)
        updated: List[Dict[str, Any]] = []
        now = utc_now_z()
        for item in todos:
            next_item = self._normalized_todo(item)
            if str(next_item.get("todo_id") or "").strip() == str(todo_id or "").strip():
                previous_status = str(next_item.get("status") or "pending").strip() or "pending"
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
                elif status == "paused":
                    next_item["completed_at"] = None
                elif status == "failed":
                    next_item["last_error"] = str(last_error or next_item.get("last_error") or "").strip()
                elif last_error is not None:
                    next_item["last_error"] = str(last_error or "").strip()
                if resume_from is not None and status != "completed":
                    next_item["resume_from"] = str(resume_from or "").strip()
                if state_updates:
                    next_state = next_item.get("state") if isinstance(next_item.get("state"), dict) else {}
                    next_item["state"] = {**next_state, **state_updates}
            updated.append(next_item)
        metadata_updates: Dict[str, Any] = {"todos": updated}
        if current_task is not _UNSET:
            metadata_updates["current_task"] = current_task
        self.storage.update_task(
            self.task_id,
            metadata=merge_ai_search_meta(task, **metadata_updates),
        )
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
        if target is None and resolved_todo_id:
            for item in self._current_todos():
                if str(item.get("phase_key") or "").strip() == resolved_todo_id:
                    target = item
                    break
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
        self.update_todo(
            todo_id,
            "failed",
            current_task=current_task,
            last_error=message,
            resume_from=resume_from,
            state_updates={"last_failed_at": utc_now_z()},
        )
        return json.dumps({"ok": False, "error": message, "resume_from": resume_from}, ensure_ascii=False)

    def find_message_by_question_id(self, question_id: str) -> Optional[Dict[str, Any]]:
        for item in reversed(self.storage.list_ai_search_messages(self.task_id)):
            if str(item.get("question_id") or "") == str(question_id):
                return item
        return None

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
        summaries: List[Dict[str, Any]] = []
        for item in self.storage.list_ai_search_messages(self.task_id):
            if str(item.get("kind") or "") != "execution_step_summary":
                continue
            if int(item.get("plan_version") or 0) != int(plan_version):
                continue
            metadata = item.get("metadata")
            if isinstance(metadata, dict) and (not sub_plan_id or str(metadata.get("sub_plan_id") or "").strip() == str(sub_plan_id or "").strip()):
                summaries.append(metadata)
        return summaries

    def build_execution_step_directive(self, plan_version: int) -> Dict[str, Any]:
        plan = self.storage.get_ai_search_plan(self.task_id, int(plan_version)) or {}
        execution_spec = plan.get("execution_spec_json") if isinstance(plan.get("execution_spec_json"), dict) else {}
        normalized_plan = normalize_execution_plan(execution_spec, self.current_search_elements(plan_version))
        current_todo = self.current_todo()
        if not current_todo:
            return {
                "plan_version": int(plan_version),
                "current_todo": None,
                "current_step": None,
                "query_blueprints": [],
                "history": [],
                "search_elements_snapshot": normalized_plan.get("search_elements_snapshot") or {},
                "gap_context": self.latest_gap_context(),
                "execution_policy": normalized_plan.get("execution_policy") or {},
            }
        sub_plan_id = str(current_todo.get("sub_plan_id") or "").strip()
        step_id = str(current_todo.get("step_id") or "").strip()
        sub_plan, step = resolve_plan_step(normalized_plan, sub_plan_id, step_id)
        query_refs = {
            str(ref or "").strip()
            for ref in (step.get("query_blueprint_refs") or [])
            if str(ref or "").strip()
        }
        query_blueprints = [
            item
            for item in (sub_plan.get("query_blueprints") or [])
            if isinstance(item, dict) and str(item.get("batch_id") or "").strip() in query_refs
        ]
        summaries = self.list_execution_step_summaries(int(plan_version), sub_plan_id=sub_plan_id)
        return {
            "plan_version": int(plan_version),
            "current_todo": current_todo,
            "current_step": step,
            "current_sub_plan": sub_plan,
            "execution_policy": normalized_plan.get("execution_policy") or {},
            "query_blueprints": query_blueprints,
            "history": summaries,
            "search_elements_snapshot": normalized_plan.get("search_elements_snapshot") or {},
            "gap_context": self.latest_gap_context(),
        }

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

    def latest_gap_context(self) -> Dict[str, Any]:
        return {
            "close_read_result": self.latest_message_metadata("close_read_result"),
            "feature_compare_result": self.latest_message_metadata("feature_compare_result"),
        }

    def reset_execution_control(
        self,
        plan_version: Optional[int] = None,
        *,
        clear_human_decision: bool = False,
        runtime: Any | None = None,
    ) -> None:
        version = int(plan_version or self.active_plan_version() or 0)
        progress = self.evaluate_gap_progress_payload(version)
        task = self.storage.get_task(self.task_id)
        updates: Dict[str, Any] = {
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
        if clear_human_decision:
            updates["human_decision_reason"] = None
            updates["human_decision_summary"] = None
        self.storage.update_task(self.task_id, metadata=merge_ai_search_meta(task, **updates))
        self.notify_snapshot_changed(runtime, reason="execution_control")

    def evaluate_exhaustion_payload(self, plan_version: Optional[int] = None) -> Dict[str, Any]:
        version = int(plan_version or self.active_plan_version() or 0)
        task = self.storage.get_task(self.task_id)
        meta = get_ai_search_meta(task)
        progress = self.evaluate_gap_progress_payload(version)
        policy = self.execution_policy(version)
        summaries = self.list_execution_step_summaries(version) if version > 0 else []
        processed_count = min(int(meta.get("processed_execution_summary_count") or 0), len(summaries))
        current_round_summaries = summaries[processed_count:]
        new_unique_candidates = sum(int(item.get("new_unique_candidates") or 0) for item in current_round_summaries if isinstance(item, dict))

        selected_count = int(progress.get("selected_count") or 0)
        readiness = str(progress.get("readiness") or "unknown").strip() or "unknown"
        recommended_action = str(progress.get("recommended_action") or "").strip()
        gap_signature = _gap_signature(progress)

        previous_selected = meta.get("last_selected_count")
        previous_readiness = meta.get("last_readiness")
        previous_gap_signature = meta.get("last_gap_signature") if isinstance(meta.get("last_gap_signature"), dict) else None

        has_previous_baseline = previous_selected is not None or previous_readiness is not None or previous_gap_signature is not None
        readiness_improved = has_previous_baseline and _readiness_rank(readiness) > _readiness_rank(previous_readiness)
        gap_improved = has_previous_baseline and _gap_signature_score(gap_signature) < _gap_signature_score(previous_gap_signature or {})
        selected_grew = has_previous_baseline and selected_count > int(previous_selected or 0)

        is_no_progress = bool(
            new_unique_candidates == 0
            or (
                has_previous_baseline
                and not selected_grew
                and not readiness_improved
                and not gap_improved
            )
            or recommended_action == "replan_search"
        )
        no_progress_round_count = (
            int(meta.get("no_progress_round_count") or 0) + 1
            if is_no_progress
            else 0
        )
        execution_round_count = int(meta.get("execution_round_count") or 0) + 1

        triggered_limit = ""
        if selected_count >= int(policy.get("max_selected_documents") or DEFAULT_EXECUTION_POLICY["max_selected_documents"]):
            triggered_limit = "max_selected_documents"
        elif no_progress_round_count >= int(policy.get("max_no_progress_rounds") or DEFAULT_EXECUTION_POLICY["max_no_progress_rounds"]):
            triggered_limit = "max_no_progress_rounds"
        elif execution_round_count >= int(policy.get("max_rounds") or DEFAULT_EXECUTION_POLICY["max_rounds"]):
            triggered_limit = "max_rounds"

        decision_reason = ""
        if triggered_limit == "max_selected_documents":
            decision_reason = "selected_documents_limit_reached"
        elif triggered_limit == "max_no_progress_rounds":
            decision_reason = "no_progress_limit_reached"
        elif triggered_limit == "max_rounds":
            decision_reason = "round_limit_reached"

        summary_parts = [
            f"已完成 {execution_round_count} 轮检索评估",
            f"连续无进展 {no_progress_round_count} 轮" if is_no_progress or no_progress_round_count > 0 else "",
            f"当前已选对比文献 {selected_count} 篇",
            f"本轮新增唯一候选 {new_unique_candidates} 篇",
            f"当前建议动作：{recommended_action or 'unknown'}",
        ]
        decision_summary = "；".join(part for part in summary_parts if part)

        should_request_decision = bool(triggered_limit) and bool(policy.get("decision_on_exhaustion", DEFAULT_EXECUTION_POLICY["decision_on_exhaustion"]))
        return {
            "plan_version": version,
            "execution_policy": policy,
            "is_no_progress": is_no_progress,
            "triggered_limit": triggered_limit,
            "decision_reason": decision_reason,
            "decision_summary": decision_summary,
            "should_request_decision": should_request_decision,
            "new_unique_candidates": new_unique_candidates,
            "execution_round_count": execution_round_count,
            "no_progress_round_count": no_progress_round_count,
            "selected_count": selected_count,
            "readiness": readiness,
            "recommended_action": recommended_action,
            "gap_signature": gap_signature,
            "processed_execution_summary_count": len(summaries),
        }

    def commit_round_evaluation(self, plan_version: Optional[int] = None, *, runtime: Any | None = None) -> Dict[str, Any]:
        version = int(plan_version or self.active_plan_version() or 0)
        payload = self.evaluate_exhaustion_payload(version)
        task = self.storage.get_task(self.task_id)
        updates = {
            "execution_round_count": int(payload.get("execution_round_count") or 0),
            "no_progress_round_count": int(payload.get("no_progress_round_count") or 0),
            "last_selected_count": int(payload.get("selected_count") or 0),
            "last_readiness": str(payload.get("readiness") or "unknown").strip() or "unknown",
            "last_gap_signature": payload.get("gap_signature") if isinstance(payload.get("gap_signature"), dict) else {},
            "last_new_unique_candidates": int(payload.get("new_unique_candidates") or 0),
            "last_recommended_action": str(payload.get("recommended_action") or "").strip() or None,
            "processed_execution_summary_count": int(payload.get("processed_execution_summary_count") or 0),
            "last_exhaustion_reason": str(payload.get("decision_reason") or "").strip() or None,
            "last_exhaustion_summary": str(payload.get("decision_summary") or "").strip() or None,
        }
        self.storage.update_task(self.task_id, metadata=merge_ai_search_meta(task, **updates))
        self.notify_snapshot_changed(runtime, reason="execution_round")
        return payload

    def enter_human_decision(
        self,
        *,
        reason: str,
        summary: str,
        runtime: Any | None = None,
    ) -> None:
        plan_version = int(self.active_plan_version() or 0)
        selected_count = len(self.storage.list_ai_search_documents(self.task_id, plan_version, stages=["selected"])) if plan_version > 0 else 0
        current = self.current_todo()
        if current:
            self.update_todo(
                str(current.get("todo_id") or "").strip(),
                "paused",
                current_task=None,
                resume_from="awaiting_human_decision",
                last_error=str(summary or "").strip(),
                runtime=runtime,
            )
        task = self.storage.get_task(self.task_id)
        self.storage.update_task(
            self.task_id,
            metadata=merge_ai_search_meta(
                task,
                current_phase=PHASE_AWAITING_HUMAN_DECISION,
                current_task=None,
                selected_document_count=selected_count,
                human_decision_reason=str(reason or "").strip() or None,
                human_decision_summary=str(summary or "").strip() or None,
                last_exhaustion_reason=str(reason or "").strip() or None,
                last_exhaustion_summary=str(summary or "").strip() or None,
            ),
            status=phase_to_task_status(PHASE_AWAITING_HUMAN_DECISION),
            progress=phase_progress(PHASE_AWAITING_HUMAN_DECISION),
            current_step=phase_step(PHASE_AWAITING_HUMAN_DECISION),
        )
        self.notify_snapshot_changed(runtime, reason="human_decision")

    def build_gap_strategy_seed_payload(self, plan_version: Optional[int] = None) -> Dict[str, Any]:
        version = int(plan_version or self.active_plan_version() or 0)
        gap_context = self.latest_gap_context()
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
        for values in (close_read_result.get("follow_up_hints"), feature_compare_result.get("follow_up_search_hints")):
            if not isinstance(values, list):
                continue
            for item in values:
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
            suggested_keywords = []
            for value in item.get("suggested_keywords") or []:
                text = str(value or "").strip()
                if text and text not in suggested_keywords:
                    suggested_keywords.append(text)
            suggested_pivots = []
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
        version = int(plan_version or self.active_plan_version() or 0)
        gap_context = self.latest_gap_context()
        close_read_result = gap_context.get("close_read_result") if isinstance(gap_context.get("close_read_result"), dict) else {}
        feature_compare_result = gap_context.get("feature_compare_result") if isinstance(gap_context.get("feature_compare_result"), dict) else {}

        limitation_gaps = close_read_result.get("limitation_gaps") if isinstance(close_read_result.get("limitation_gaps"), list) else []
        coverage_gaps = feature_compare_result.get("coverage_gaps") if isinstance(feature_compare_result.get("coverage_gaps"), list) else []
        document_assessments = close_read_result.get("document_assessments") if isinstance(close_read_result.get("document_assessments"), list) else []
        follow_up_hints = []
        for values in (
            close_read_result.get("follow_up_hints"),
            feature_compare_result.get("follow_up_search_hints"),
        ):
            if not isinstance(values, list):
                continue
            for item in values:
                text = str(item or "").strip()
                if text and text not in follow_up_hints:
                    follow_up_hints.append(text)

        readiness = str(feature_compare_result.get("creativity_readiness") or "").strip().lower()
        selected_count = len(self.storage.list_ai_search_documents(self.task_id, version, stages=["selected"])) if version > 0 else 0
        weak_evidence_count = 0
        for item in document_assessments:
            if not isinstance(item, dict):
                continue
            confidence = float(item.get("confidence") or 0.0)
            sufficiency = str(item.get("evidence_sufficiency") or "").strip().lower()
            if confidence < 0.55 or sufficiency in {"partial", "insufficient", "weak"}:
                weak_evidence_count += 1
        has_material_gap = bool(limitation_gaps or coverage_gaps or follow_up_hints or weak_evidence_count > 0)

        if readiness in {"ready", "sufficient", "enough"} and not has_material_gap and selected_count > 0:
            recommended_action = "complete_execution"
            should_continue_search = False
        elif has_material_gap:
            recommended_action = "replan_search"
            should_continue_search = True
        elif selected_count > 0:
            recommended_action = "feature_comparison"
            should_continue_search = False
        else:
            recommended_action = "replan_draft_plan"
            should_continue_search = True

        return {
            "plan_version": version,
            "current_phase": self.current_phase(),
            "selected_count": selected_count,
            "readiness": readiness or "unknown",
            "limitation_gap_count": len(limitation_gaps),
            "coverage_gap_count": len(coverage_gaps),
            "follow_up_hint_count": len(follow_up_hints),
            "weak_evidence_count": weak_evidence_count,
            "should_continue_search": should_continue_search,
            "recommended_action": recommended_action,
            "gap_context": gap_context,
        }

    def build_main_agent_tools(self) -> List[Any]:
        return build_main_agent_tools(self)

    def build_search_elements_tools(self) -> List[Any]:
        return build_search_elements_tools(self)

    def build_query_executor_tools(self) -> List[Any]:
        return build_query_executor_tools(self)

    def build_plan_prober_tools(self) -> List[Any]:
        return build_plan_prober_tools(self)

    def build_coarse_screener_tools(self) -> List[Any]:
        return build_coarse_screener_tools(self)

    def build_close_reader_tools(self) -> List[Any]:
        return build_close_reader_tools(self)

    def build_feature_comparer_tools(self) -> List[Any]:
        return build_feature_comparer_tools(self)

    def execution_todos_from_plan(self, plan_version: int, execution_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        normalized = normalize_execution_plan(execution_spec, self.current_search_elements(plan_version))
        return build_execution_todos(plan_version, normalized)
