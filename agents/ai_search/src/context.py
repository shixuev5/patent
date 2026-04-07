"""Shared AI Search context plus thin wrappers for role-specific tool builders."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.ai_search.src.execution_state import normalize_execution_plan
from agents.ai_search.src.main_agent.tools import build_main_agent_tools
from agents.ai_search.src.runtime import write_stream_event
from agents.ai_search.src.state import (
    get_ai_search_meta,
    get_ai_search_mode,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from agents.ai_search.src.subagents.claim_decomposer.tools import build_claim_decomposer_tools
from agents.ai_search.src.subagents.claim_search_strategist.tools import build_claim_search_strategist_tools
from agents.ai_search.src.subagents.close_reader.tools import build_close_reader_tools
from agents.ai_search.src.subagents.coarse_screener.tools import build_coarse_screener_tools
from agents.ai_search.src.subagents.feature_comparer.tools import build_feature_comparer_tools
from agents.ai_search.src.subagents.query_executor.tools import build_query_executor_tools
from agents.ai_search.src.subagents.search_elements.tools import build_search_elements_tools
from backend.time_utils import utc_now_z
from backend.utils import _build_r2_storage

_UNSET = object()


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
            "key": str(item.get("key") or base.get("key") or "").strip(),
            "title": str(item.get("title") or base.get("title") or "").strip(),
            "status": str(item.get("status") or base.get("status") or "pending").strip() or "pending",
            "details": str(item.get("details") or base.get("details") or "").strip(),
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
        return {str(item.get("key") or ""): item for item in todos if str(item.get("key") or "").strip()}

    def current_todo(self) -> Optional[Dict[str, Any]]:
        task = self.storage.get_task(self.task_id)
        meta = get_ai_search_meta(task)
        current_task = str(meta.get("current_task") or "").strip()
        if not current_task:
            return None
        return self._todo_map(task).get(current_task)

    def update_todos(
        self,
        task_key: str,
        status: str,
        *,
        current_task: Any = _UNSET,
        resume_from: Optional[str] = None,
        last_error: Optional[str] = None,
        state_updates: Optional[Dict[str, Any]] = None,
    ) -> None:
        task = self.storage.get_task(self.task_id)
        todos = self._current_todos(task)
        updated: List[Dict[str, Any]] = []
        now = utc_now_z()
        for item in todos:
            next_item = self._normalized_todo(item)
            if str(next_item.get("key") or "") == task_key:
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

    def record_todo_failure(self, task_key: str, error: str, *, current_task: str, resume_from: str) -> str:
        message = str(error or "unknown_error").strip() or "unknown_error"
        self.update_todos(
            task_key,
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

    def current_search_mode(self) -> str:
        task = self.storage.get_task(self.task_id)
        return get_ai_search_mode(task)

    def current_search_elements(self, plan_version: Optional[int] = None) -> Dict[str, Any]:
        version = int(plan_version or self.active_plan_version() or 0)
        if version > 0:
            plan = self.storage.get_ai_search_plan(self.task_id, version) or {}
            payload = plan.get("search_elements_json")
            if isinstance(payload, dict):
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

    def list_execution_summaries(self, plan_version: int) -> List[Dict[str, Any]]:
        summaries: List[Dict[str, Any]] = []
        for item in self.storage.list_ai_search_messages(self.task_id):
            if str(item.get("kind") or "") != "execution_summary":
                continue
            if int(item.get("plan_version") or 0) != int(plan_version):
                continue
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                summaries.append(metadata)
        return summaries

    def build_execution_directive(self, plan_version: int) -> Dict[str, Any]:
        plan = self.storage.get_ai_search_plan(self.task_id, int(plan_version)) or {}
        plan_json = plan.get("plan_json") if isinstance(plan.get("plan_json"), dict) else {}
        normalized_plan = normalize_execution_plan(plan_json, self.current_search_elements(plan_version))
        claim_strategy = self.latest_message_metadata("claim_search_strategy")
        summaries = self.list_execution_summaries(int(plan_version))
        return {
            "round_id": f"round-{len(summaries) + 1}",
            "plan_version": int(plan_version),
            "execution_policy": normalized_plan.get("execution_policy") or {},
            "lanes": normalized_plan.get("lanes") or [],
            "round_stop_rules": normalized_plan.get("round_stop_rules") or [],
            "screening_entry_rules": normalized_plan.get("screening_entry_rules") or [],
            "replan_rules": normalized_plan.get("replan_rules") or [],
            "previous_round_summaries": summaries,
            "search_elements_snapshot": normalized_plan.get("search_elements_snapshot") or {},
            "claim_search_strategy": claim_strategy,
            "gap_context": self.latest_gap_context(),
        }

    def execution_plan_json(self, plan_version: Optional[int] = None) -> Dict[str, Any]:
        version = int(plan_version or self.active_plan_version() or 0)
        if version <= 0:
            return {}
        plan = self.storage.get_ai_search_plan(self.task_id, version) or {}
        return plan.get("plan_json") if isinstance(plan.get("plan_json"), dict) else {}

    def latest_gap_context(self) -> Dict[str, Any]:
        return {
            "close_read_result": self.latest_message_metadata("close_read_result"),
            "feature_compare_result": self.latest_message_metadata("feature_compare_result"),
        }

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
            recommended_action = "replan_search_strategy"
            should_continue_search = True
        elif selected_count > 0:
            recommended_action = "generate_feature_table"
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

    def build_claim_decomposer_tools(self) -> List[Any]:
        return build_claim_decomposer_tools(self)

    def build_claim_search_strategist_tools(self) -> List[Any]:
        return build_claim_search_strategist_tools(self)

    def build_query_executor_tools(self) -> List[Any]:
        return build_query_executor_tools(self)

    def build_coarse_screener_tools(self) -> List[Any]:
        return build_coarse_screener_tools(self)

    def build_close_reader_tools(self) -> List[Any]:
        return build_close_reader_tools(self)

    def build_feature_comparer_tools(self) -> List[Any]:
        return build_feature_comparer_tools(self)
