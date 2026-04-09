"""主控代理的编排工具。"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from langchain.tools import ToolRuntime
from langgraph.types import interrupt

from agents.ai_search.src.execution_state import normalize_execution_plan
from agents.ai_search.src.main_agent.schemas import SearchPlanExecutionSpecInput
from agents.ai_search.src.runtime import extract_json_object
from agents.ai_search.src.state import (
    PHASE_AWAITING_HUMAN_DECISION,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_CLOSE_READ,
    PHASE_COMPLETED,
    PHASE_COARSE_SCREEN,
    PHASE_DRAFTING_PLAN,
    PHASE_EXECUTE_SEARCH,
    PHASE_FEATURE_COMPARISON,
    get_ai_search_meta,
)
from agents.ai_search.src.subagents.search_elements.normalize import normalize_search_elements_payload
from backend.time_utils import utc_now_z


def build_main_agent_tools(context: Any) -> List[Any]:
    def _phase_from_todo(todo: Dict[str, Any]) -> str:
        phase_key = str(todo.get("phase_key") or "").strip()
        if phase_key == PHASE_COARSE_SCREEN:
            return PHASE_COARSE_SCREEN
        if phase_key == PHASE_CLOSE_READ:
            return PHASE_CLOSE_READ
        if phase_key == PHASE_FEATURE_COMPARISON:
            return PHASE_FEATURE_COMPARISON
        return PHASE_EXECUTE_SEARCH

    def read_todos() -> str:
        """读取当前任务清单。"""
        task = context.storage.get_task(context.task_id)
        meta = get_ai_search_meta(task)
        todos = context._current_todos(task)
        return json.dumps(
            {
                "todos": todos,
                "current_task": meta.get("current_task"),
            },
            ensure_ascii=False,
        )

    def write_todos(payload_json: str, runtime: ToolRuntime = None) -> str:
        """写入当前任务清单。"""
        task = context.storage.get_task(context.task_id)
        payload = extract_json_object(payload_json)
        raw_todos = payload.get("todos") if isinstance(payload.get("todos"), list) else []
        existing_by_id = context._todo_map(task)
        todos: List[Dict[str, Any]] = []
        for item in raw_todos:
            if not isinstance(item, dict):
                continue
            todo_id = str(item.get("todo_id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not todo_id or not title:
                continue
            todos.append(context._normalized_todo(item, existing=existing_by_id.get(todo_id)))
        context.update_task_phase(
            PHASE_DRAFTING_PLAN,
            runtime=runtime,
            todos=todos,
            current_task=str(payload.get("current_task") or "").strip() or None,
        )
        return "todos updated"

    def get_search_elements(plan_version: int = 0) -> str:
        """读取当前结构化检索要素。"""
        return json.dumps(
            {
                "plan_version": int(plan_version or context.active_plan_version() or 0),
                "search_elements": context.current_search_elements(plan_version),
            },
            ensure_ascii=False,
        )

    def get_gap_context() -> str:
        """读取最新的 limitation coverage、gap 和 creativity readiness 上下文。"""
        return json.dumps(context.latest_gap_context(), ensure_ascii=False)

    def evaluate_gap_progress(plan_version: int = 0) -> str:
        """根据最新 gap/readiness 状态给出下一步建议。"""
        return json.dumps(context.evaluate_gap_progress_payload(plan_version), ensure_ascii=False)

    def get_planner_draft() -> str:
        """读取最近一次 planner 提交的正式计划草案。"""
        return json.dumps(context.current_planner_draft(), ensure_ascii=False)

    def start_plan_drafting(runtime: ToolRuntime = None) -> str:
        """显式进入 draft plan 阶段。"""
        context.clear_planner_draft(runtime=runtime)
        context.update_task_phase(
            PHASE_DRAFTING_PLAN,
            runtime=runtime,
            current_task=None,
            human_decision_reason=None,
            human_decision_summary=None,
        )
        return "phase switched to drafting_plan"

    def save_search_plan(
        review_markdown: str,
        execution_spec: SearchPlanExecutionSpecInput,
        search_elements_snapshot: Dict[str, Any] | None = None,
        runtime: ToolRuntime = None,
    ) -> str:
        """持久化检索计划草案。"""
        search_elements_snapshot = normalize_search_elements_payload(search_elements_snapshot or {})
        normalized_plan = normalize_execution_plan(execution_spec.model_dump(mode="python"), search_elements_snapshot)
        search_scope = normalized_plan.get("search_scope") if isinstance(normalized_plan.get("search_scope"), dict) else {}
        search_scope = {
            "objective": str(search_scope.get("objective") or search_elements_snapshot.get("objective") or "").strip(),
            "applicants": search_scope.get("applicants") if isinstance(search_scope.get("applicants"), list) else search_elements_snapshot.get("applicants") or [],
            "filing_date": search_scope.get("filing_date") or search_elements_snapshot.get("filing_date"),
            "priority_date": search_scope.get("priority_date") or search_elements_snapshot.get("priority_date"),
            "languages": search_scope.get("languages") if isinstance(search_scope.get("languages"), list) else [],
            "databases": search_scope.get("databases") if isinstance(search_scope.get("databases"), list) else [],
            "excluded_items": search_scope.get("excluded_items") if isinstance(search_scope.get("excluded_items"), list) else [],
        }
        latest_plan = context.storage.get_ai_search_plan(context.task_id)
        if latest_plan:
            context.storage.update_ai_search_plan(
                context.task_id,
                int(latest_plan["plan_version"]),
                status="superseded",
                superseded_at=utc_now_z(),
            )
        plan_version = context.storage.get_next_ai_search_plan_version(context.task_id)
        context.storage.create_ai_search_plan(
            {
                "task_id": context.task_id,
                "plan_version": plan_version,
                "status": "draft",
                "review_markdown": review_markdown,
                "execution_spec_json": {
                    "search_scope": search_scope,
                    "constraints": normalized_plan.get("constraints") or {},
                    "execution_policy": normalized_plan.get("execution_policy") or {},
                    "sub_plans": normalized_plan.get("sub_plans") or [],
                },
            }
        )
        current_todo = context.current_todo()
        if current_todo:
            context.update_todo(
                str(current_todo.get("todo_id") or "").strip(),
                "paused",
                current_task=None,
                resume_from="await_plan_confirmation",
            )
        context.update_task_phase(
            PHASE_DRAFTING_PLAN,
            runtime=runtime,
            active_plan_version=plan_version,
            pending_confirmation_plan_version=None,
        )
        context.clear_planner_draft(runtime=runtime)
        return json.dumps({"plan_version": plan_version}, ensure_ascii=False)

    def ask_user_question(
        prompt: str,
        reason: str,
        expected_answer_shape: str,
        runtime: ToolRuntime = None,
    ) -> str:
        """创建追问并挂起。"""
        task = context.storage.get_task(context.task_id)
        meta = get_ai_search_meta(task)
        question_id = str(meta.get("pending_question_id") or "").strip()
        payload: Dict[str, Any]
        if question_id:
            existing = context.find_message_by_question_id(question_id)
            metadata = existing.get("metadata") if existing else None
            payload = metadata if isinstance(metadata, dict) else {}
            if not payload:
                payload = {
                    "question_id": question_id,
                    "prompt": prompt,
                    "reason": reason,
                    "expected_answer_shape": expected_answer_shape,
                }
        else:
            question_id = uuid.uuid4().hex[:12]
            payload = {
                "question_id": question_id,
                "prompt": prompt,
                "reason": reason,
                "expected_answer_shape": expected_answer_shape,
            }
            context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": context.task_id,
                    "role": "assistant",
                    "kind": "question",
                    "content": prompt,
                    "stream_status": "completed",
                    "question_id": question_id,
                    "metadata": payload,
                }
            )
            context.update_task_phase(PHASE_AWAITING_USER_ANSWER, runtime=runtime, pending_question_id=question_id)
        answer = interrupt(payload)
        context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime, pending_question_id=None)
        return str(answer or "").strip()

    def request_plan_confirmation(
        plan_version: int,
        plan_summary: str,
        confirmation_label: str = "实施此计划",
        runtime: ToolRuntime = None,
    ) -> str:
        """请求用户确认计划。"""
        task = context.storage.get_task(context.task_id)
        meta = get_ai_search_meta(task)
        pending_plan_version = meta.get("pending_confirmation_plan_version")
        payload = {
            "plan_version": int(plan_version),
            "plan_summary": plan_summary,
            "confirmation_label": confirmation_label,
        }
        if int(pending_plan_version or 0) != int(plan_version):
            context.storage.update_ai_search_plan(context.task_id, int(plan_version), status="awaiting_confirmation")
            context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": context.task_id,
                    "plan_version": int(plan_version),
                    "role": "assistant",
                    "kind": "plan_confirmation",
                    "content": plan_summary,
                    "stream_status": "completed",
                    "metadata": payload,
                }
            )
            context.update_task_phase(
                PHASE_AWAITING_PLAN_CONFIRMATION,
                runtime=runtime,
                pending_confirmation_plan_version=int(plan_version),
            )
        confirmation = interrupt(payload)
        confirmed = False
        if isinstance(confirmation, dict):
            confirmed = bool(confirmation.get("confirmed"))
        elif isinstance(confirmation, str):
            confirmed = confirmation.strip().lower() in {"true", "yes", "confirmed", "ok"}
        if confirmed:
            context.storage.update_ai_search_plan(context.task_id, int(plan_version), status="confirmed", confirmed_at=utc_now_z())
            plan = context.storage.get_ai_search_plan(context.task_id, int(plan_version)) or {}
            execution_spec = plan.get("execution_spec_json") if isinstance(plan.get("execution_spec_json"), dict) else {}
            todos = context.execution_todos_from_plan(int(plan_version), execution_spec)
            context.update_task_phase(
                PHASE_DRAFTING_PLAN,
                runtime=runtime,
                pending_confirmation_plan_version=None,
                current_task=None,
                todos=todos,
            )
        else:
            context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime, pending_confirmation_plan_version=None)
        return "confirmed" if confirmed else "not_confirmed"

    def begin_execution(plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """标记执行阶段开始。"""
        version = int(plan_version or context.active_plan_version() or 0)
        if version <= 0:
            return "missing_plan"
        todo = context.first_pending_todo(phase_key=PHASE_EXECUTE_SEARCH) or context.first_pending_todo()
        if not todo:
            return "no_pending_todos"
        phase = _phase_from_todo(todo)
        todo_id = str(todo.get("todo_id") or "").strip()
        context.update_task_phase(phase, runtime=runtime, active_plan_version=version, current_task=todo_id)
        context.update_todo(
            todo_id,
            "in_progress",
            current_task=todo_id,
            resume_from="run_execution_step.load",
            state_updates={"plan_version": version},
        )
        return json.dumps({"plan_version": version, "todo_id": todo_id}, ensure_ascii=False)

    def start_execution_step(todo_id: str = "", plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """开始指定或下一条步骤级执行 todo。"""
        version = int(plan_version or context.active_plan_version() or 0)
        target = context._todo_map().get(str(todo_id or "").strip()) if str(todo_id or "").strip() else None
        if target is None:
            current = context.current_todo()
            target = context.next_pending_todo(str(current.get("todo_id") or "").strip() if current else "")
        if target is None:
            return "no_pending_todo"
        target_id = str(target.get("todo_id") or "").strip()
        phase = _phase_from_todo(target)
        context.update_task_phase(phase, runtime=runtime, active_plan_version=version, current_task=target_id)
        context.update_todo(
            target_id,
            "in_progress",
            current_task=target_id,
            resume_from="run_execution_step.load",
            state_updates={"plan_version": version},
        )
        return json.dumps({"todo_id": target_id, "phase": phase}, ensure_ascii=False)

    def complete_execution_step(
        todo_id: str = "",
        next_todo_id: str = "",
        next_action: str = "",
        plan_version: int = 0,
        runtime: ToolRuntime = None,
    ) -> str:
        """完成当前步骤级 todo，并按建议推进下一步。"""
        version = int(plan_version or context.active_plan_version() or 0)
        current = context._todo_map().get(str(todo_id or "").strip()) if str(todo_id or "").strip() else context.current_todo()
        if not current:
            return "missing_current_todo"
        current_id = str(current.get("todo_id") or "").strip()
        context.update_todo(current_id, "completed", current_task=None)
        action = str(next_action or "").strip() or "start_next_step"
        if action == "enter_coarse_screen":
            return start_coarse_screen(version, runtime)
        if action == "enter_close_read":
            return start_close_read(version, runtime)
        if action == "enter_feature_comparison":
            return start_feature_comparison(version, runtime)
        target = context._todo_map().get(str(next_todo_id or "").strip()) if str(next_todo_id or "").strip() else context.next_pending_todo(current_id)
        if target:
            return start_execution_step(str(target.get("todo_id") or "").strip(), version, runtime)
        return json.dumps({"completed_todo_id": current_id, "next_action": "none"}, ensure_ascii=False)

    def pause_execution_for_replan(reason: str = "", plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """挂起当前步骤并切回计划重审。"""
        version = int(plan_version or context.active_plan_version() or 0)
        current = context.current_todo()
        if not current:
            return "missing_current_todo"
        round_evaluation = context.commit_round_evaluation(version, runtime=runtime)
        if bool(round_evaluation.get("should_request_decision")):
            summary = str(round_evaluation.get("decision_summary") or str(reason or "").strip()).strip() or "自动检索已停止，需要人工决策。"
            context.enter_human_decision(
                reason=str(round_evaluation.get("decision_reason") or "no_progress_limit_reached").strip(),
                summary=summary,
                runtime=runtime,
            )
            context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": context.task_id,
                    "plan_version": version or None,
                    "role": "assistant",
                    "kind": "chat",
                    "content": summary,
                    "stream_status": "completed",
                    "metadata": {"reason": round_evaluation.get("decision_reason"), "kind": "human_decision"},
                }
            )
            return json.dumps(
                {
                    "todo_id": str(current.get("todo_id") or "").strip(),
                    "reason": round_evaluation.get("decision_reason"),
                    "phase": PHASE_AWAITING_HUMAN_DECISION,
                },
                ensure_ascii=False,
            )
        todo_id = str(current.get("todo_id") or "").strip()
        context.update_todo(
            todo_id,
            "paused",
            current_task=None,
            last_error=str(reason or "").strip(),
            resume_from="await_plan_confirmation",
            state_updates={"replan_requested_at": utc_now_z()},
        )
        if version > 0:
            context.storage.update_ai_search_plan(context.task_id, version, status="superseded", superseded_at=utc_now_z())
        context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime, pending_confirmation_plan_version=None, current_task=None)
        return json.dumps({"todo_id": todo_id, "reason": str(reason or "").strip()}, ensure_ascii=False)

    def start_coarse_screen(plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """显式进入 coarse screen 阶段。"""
        version = int(plan_version or context.active_plan_version() or 0)
        candidate_count = len(context.storage.list_ai_search_documents(context.task_id, version))
        if candidate_count <= 0:
            return "no_candidates_for_coarse_screen"
        context.update_task_phase(PHASE_COARSE_SCREEN, runtime=runtime, active_plan_version=version, current_task=None)
        return json.dumps({"plan_version": version, "candidate_count": candidate_count}, ensure_ascii=False)

    def start_close_read(plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """显式进入 close read 阶段。"""
        version = int(plan_version or context.active_plan_version() or 0)
        shortlisted_count = len(context.storage.list_ai_search_documents(context.task_id, version, stages=["shortlisted"]))
        if shortlisted_count <= 0:
            return "no_shortlisted_documents"
        context.update_task_phase(PHASE_CLOSE_READ, runtime=runtime, active_plan_version=version, current_task=None)
        return json.dumps({"plan_version": version, "shortlisted_count": shortlisted_count}, ensure_ascii=False)

    def start_feature_comparison(plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """显式进入特征对比分析阶段。"""
        version = int(plan_version or context.active_plan_version() or 0)
        selected_count = len(context.storage.list_ai_search_documents(context.task_id, version, stages=["selected"]))
        if selected_count <= 0:
            return "no_selected_documents"
        context.update_task_phase(
            PHASE_FEATURE_COMPARISON,
            runtime=runtime,
            active_plan_version=version,
            current_task=None,
        )
        return json.dumps({"plan_version": version, "selected_count": selected_count}, ensure_ascii=False)

    def get_execution_state(plan_version: int = 0) -> str:
        """读取当前执行态。"""
        version = int(plan_version or context.active_plan_version() or 0)
        plan = context.storage.get_ai_search_plan(context.task_id, version) or {}
        task = context.storage.get_task(context.task_id)
        meta = get_ai_search_meta(task)
        todos = context._current_todos(task)
        current_todo = context.current_todo() or {}
        return json.dumps(
            {
                "plan_version": version,
                "phase": str(meta.get("current_phase") or ""),
                "todos": todos,
                "current_task": meta.get("current_task"),
                "recovery": {
                    "resume_from": current_todo.get("resume_from"),
                    "attempt_count": int(current_todo.get("attempt_count") or 0),
                    "last_error": current_todo.get("last_error") or "",
                    "current_todo_state": current_todo.get("state") if isinstance(current_todo.get("state"), dict) else {},
                },
                "plan": plan.get("execution_spec_json") if isinstance(plan.get("execution_spec_json"), dict) else {},
                "search_elements": context.current_search_elements(version),
                "candidate_count": len(context.storage.list_ai_search_documents(context.task_id, version)) if version > 0 else 0,
                "selected_count": len(context.storage.list_ai_search_documents(context.task_id, version, stages=["selected"])) if version > 0 else 0,
            },
            ensure_ascii=False,
        )

    def list_documents(plan_version: int = 0, stage: str = "") -> str:
        """列出候选、shortlisted 或 selected 文献。"""
        version = int(plan_version or context.active_plan_version() or 0)
        stages = [stage] if stage.strip() else None
        docs = context.storage.list_ai_search_documents(context.task_id, version, stages=stages)
        return json.dumps({"count": len(docs), "items": docs}, ensure_ascii=False)

    def complete_execution(
        summary: str = "",
        plan_version: int = 0,
        force_from_decision: bool = False,
        runtime: ToolRuntime = None,
    ) -> str:
        """结束执行阶段并更新汇总状态。"""
        version = int(plan_version or context.active_plan_version() or 0)
        current_phase = context.current_phase()
        if current_phase == PHASE_FEATURE_COMPARISON and not force_from_decision:
            progress = context.evaluate_gap_progress_payload(version)
            if str(progress.get("recommended_action") or "") == "replan_search":
                return json.dumps(
                    {
                        "blocked": True,
                        "reason": "gap_replan_required",
                        "recommended_action": progress.get("recommended_action"),
                        "should_continue_search": progress.get("should_continue_search"),
                    },
                    ensure_ascii=False,
                )
        selected_count = len(context.storage.list_ai_search_documents(context.task_id, version, stages=["selected"])) if version > 0 else 0
        if summary.strip():
            context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": context.task_id,
                    "plan_version": version or None,
                    "role": "assistant",
                    "kind": "chat",
                    "content": summary.strip(),
                    "stream_status": "completed",
                    "metadata": {},
                }
            )
        context.update_task_phase(
            PHASE_COMPLETED,
            runtime=runtime,
            active_plan_version=version or None,
            selected_document_count=selected_count,
            human_decision_reason=None if force_from_decision else None,
            human_decision_summary=None if force_from_decision else None,
            current_task=None,
        )
        return json.dumps({"selected_count": selected_count}, ensure_ascii=False)

    return [
        read_todos,
        write_todos,
        get_search_elements,
        get_gap_context,
        evaluate_gap_progress,
        get_planner_draft,
        start_plan_drafting,
        save_search_plan,
        ask_user_question,
        request_plan_confirmation,
        begin_execution,
        start_execution_step,
        complete_execution_step,
        pause_execution_for_replan,
        start_coarse_screen,
        start_close_read,
        start_feature_comparison,
        get_execution_state,
        list_documents,
        complete_execution,
    ]
