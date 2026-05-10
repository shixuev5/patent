"""主控代理的高层编排工具。"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from langchain.tools import ToolRuntime
from langgraph.types import interrupt

from agents.ai_search.src.runtime_context import resolve_agent_context
from agents.ai_search.src.orchestration.action_runtime import open_pending_action
from agents.ai_search.src.orchestration.execution_runtime import (
    advance_workflow,
    build_execution_context,
    finalize_search_session,
)
from agents.ai_search.src.main_agent.planning_tools import build_planning_tools
from agents.ai_search.src.orchestration.phase_machine import enter_drafting_plan
from agents.ai_search.src.orchestration.planning_runtime import (
    build_planning_context,
)
from agents.ai_search.src.main_agent.specialist_tool import build_search_specialist_tool
from agents.ai_search.src.orchestration.session_views import build_session_context
from agents.ai_search.src.state import (
    ACTIVE_EXECUTION_PHASES,
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_HUMAN_DECISION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_CLOSE_READ,
    PHASE_COARSE_SCREEN,
    PHASE_DRAFTING_PLAN,
    PHASE_EXECUTE_SEARCH,
    PHASE_FEATURE_COMPARISON,
    PHASE_COMPLETED,
    allowed_main_agent_subagents,
    allowed_main_agent_tools,
    phase_to_task_status,
)


def build_main_agent_tools() -> List[Any]:
    def _workflow_context_payload(resolved_context: Any, plan_version: int = 0) -> Dict[str, Any]:
        phase = resolved_context.current_phase()
        version = int(plan_version or resolved_context.active_plan_version() or 0)
        payload: Dict[str, Any] = {
            "phase": phase,
            "active_plan_version": version or None,
            "session": build_session_context(resolved_context),
        }
        if phase in ACTIVE_EXECUTION_PHASES or phase == PHASE_COMPLETED:
            payload["context_kind"] = "execution"
            try:
                payload["execution"] = build_execution_context(resolved_context, version)
            except Exception as exc:
                payload["context_kind"] = "planning"
                payload["planning"] = build_planning_context(resolved_context, version)
                payload["execution_error"] = str(exc)
        else:
            payload["context_kind"] = "planning"
            payload["planning"] = build_planning_context(resolved_context, version)
        return payload

    def _workflow_options_payload(resolved_context: Any, plan_version: int = 0) -> Dict[str, Any]:
        phase = resolved_context.current_phase()
        version = int(plan_version or resolved_context.active_plan_version() or 0)
        actions: list[Dict[str, Any]] = []

        def add_action(name: str, *, kind: str = "tool", command: Dict[str, Any] | None = None, reason: str = "") -> None:
            actions.append(
                {
                    "name": name,
                    "kind": kind,
                    "command": command or {},
                    "reason": str(reason or "").strip() or None,
                }
            )

        if phase == PHASE_AWAITING_USER_ANSWER:
            add_action("wait_for_user_answer", kind="wait", reason="当前存在追问 interrupt，等待用户回答。")
        elif phase == PHASE_AWAITING_PLAN_CONFIRMATION:
            add_action("wait_for_plan_confirmation", kind="wait", reason="当前计划需要用户确认。")
            add_action("begin_execution_after_confirmation", command={"tool": "advance_workflow", "action": "begin_execution", "plan_version": version})
        elif phase == PHASE_AWAITING_HUMAN_DECISION:
            add_action("wait_for_human_decision", kind="wait", reason="当前需要用户选择继续检索或按当前结果结束。")
            add_action("continue_search_after_decision", command={"tool": "start_plan_drafting"})
            add_action("complete_current_results_after_decision", command={"tool": "finalize_search_session", "force_from_decision": True, "plan_version": version})
        elif phase == PHASE_DRAFTING_PLAN:
            planning = build_planning_context(resolved_context, version)
            if isinstance(planning.get("current_plan"), dict) and str((planning.get("current_plan") or {}).get("status") or "").strip() in {"draft", "awaiting_confirmation"}:
                current_plan = planning.get("current_plan") or {}
                add_action(
                    "request_plan_confirmation",
                    command={
                        "tool": "request_plan_confirmation",
                        "review_markdown": str(current_plan.get("review_markdown") or "").strip(),
                        "plan_version": int(current_plan.get("plan_version") or version or 0),
                    },
                )
            else:
                add_action("probe_search_semantic", command={"tool": "probe_search_semantic"})
                add_action("probe_search_boolean", command={"tool": "probe_search_boolean"})
                add_action("request_plan_confirmation", command={"tool": "request_plan_confirmation"})
                add_action("compile_confirmed_search_plan", command={"tool": "compile_confirmed_search_plan"})
                add_action("request_user_question", command={"tool": "request_user_question"})
        elif phase == PHASE_EXECUTE_SEARCH:
            execution = build_execution_context(resolved_context, version)
            current_todo = execution.get("current_todo") if isinstance(execution, dict) else None
            if current_todo:
                add_action("run_query_executor", command={"tool": "run_search_specialist", "specialist_type": "query-executor"})
                add_action("mark_step_completed", command={"tool": "advance_workflow", "action": "step_completed", "plan_version": version, "todo_id": str(current_todo.get("todo_id") or "").strip()})
                add_action("request_replan", command={"tool": "advance_workflow", "action": "request_replan", "plan_version": version, "todo_id": str(current_todo.get("todo_id") or "").strip()})
                add_action("enter_coarse_screen", command={"tool": "advance_workflow", "action": "step_completed", "plan_version": version, "todo_id": str(current_todo.get("todo_id") or "").strip(), "next_action": "enter_coarse_screen"})
            else:
                add_action("begin_execution", command={"tool": "advance_workflow", "action": "begin_execution", "plan_version": version})
                add_action("enter_coarse_screen", command={"tool": "advance_workflow", "action": "enter_coarse_screen", "plan_version": version})
        elif phase == PHASE_COARSE_SCREEN:
            add_action("run_coarse_screener", command={"tool": "run_search_specialist", "specialist_type": "coarse-screener"})
            add_action("enter_close_read", command={"tool": "advance_workflow", "action": "enter_close_read", "plan_version": version})
        elif phase == PHASE_CLOSE_READ:
            add_action("run_close_reader", command={"tool": "run_search_specialist", "specialist_type": "close-reader"})
            add_action("enter_feature_comparison", command={"tool": "advance_workflow", "action": "enter_feature_comparison", "plan_version": version})
        elif phase == PHASE_FEATURE_COMPARISON:
            add_action("run_feature_comparer", command={"tool": "run_search_specialist", "specialist_type": "feature-comparer"})
            add_action("start_replan", command={"tool": "start_plan_drafting"})
            add_action("request_human_decision", command={"tool": "request_human_decision", "plan_version": version})
            add_action("finalize_search_session", command={"tool": "finalize_search_session", "plan_version": version})
        elif phase == PHASE_COMPLETED:
            add_action("read_completed_context", kind="read", reason="会话已完成，只允许读取最终上下文。")
        else:
            add_action("read_context", kind="read")

        return {
            "phase": phase,
            "active_plan_version": version or None,
            "allowed_tools": sorted(allowed_main_agent_tools(phase)),
            "allowed_subagents": sorted(allowed_main_agent_subagents(phase)),
            "actions": actions,
            "recommended_next": actions[0] if actions else None,
        }

    def get_workflow_context(plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """按当前 phase 一次性读取主控决策所需上下文。"""
        resolved_context = resolve_agent_context(runtime)
        return json.dumps(_workflow_context_payload(resolved_context, plan_version), ensure_ascii=False)

    def get_workflow_options(plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """读取当前 phase 下可选的安全动作和推荐下一步。"""
        resolved_context = resolve_agent_context(runtime)
        return json.dumps(_workflow_options_payload(resolved_context, plan_version), ensure_ascii=False)

    def start_plan_drafting(runtime: ToolRuntime = None) -> str:
        """显式进入 draft plan 阶段。"""
        resolved_context = resolve_agent_context(runtime)
        return json.dumps(enter_drafting_plan(resolved_context, runtime=runtime), ensure_ascii=False)

    def request_user_question(
        prompt: str,
        reason: str,
        expected_answer_shape: str,
        runtime: ToolRuntime = None,
    ) -> str:
        """创建追问并挂起。"""
        resolved_context = resolve_agent_context(runtime)
        pending = resolved_context.storage.get_ai_search_pending_action(resolved_context.task_id, "question", status="pending")
        payload: Dict[str, Any]
        if pending and isinstance(pending.get("payload"), dict):
            payload = dict(pending.get("payload") or {})
            question_id = str(payload.get("question_id") or "").strip() or uuid.uuid4().hex[:12]
            payload.setdefault("question_id", question_id)
        else:
            question_id = uuid.uuid4().hex[:12]
            payload = {
                "question_id": question_id,
                "prompt": prompt,
                "reason": reason,
                "expected_answer_shape": expected_answer_shape,
            }
            resolved_context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": resolved_context.task_id,
                    "role": "assistant",
                    "kind": "question",
                    "content": prompt,
                    "stream_status": "completed",
                    "question_id": question_id,
                    "metadata": payload,
                }
            )
        open_pending_action(
            resolved_context,
            action_type="question",
            source="agent_prompted",
            payload=payload,
            run_id=resolved_context.active_run_id(),
            plan_version=resolved_context.active_plan_version(),
            runtime=runtime,
        )
        answer = interrupt(payload)
        resolved_context.resolve_pending_action(
            "question",
            resolution={"answer": str(answer or "").strip()},
            runtime=runtime,
        )
        resolved_context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime)
        return str(answer or "").strip()

    def request_plan_confirmation(
        review_markdown: str,
        confirmation_label: str = "实施此计划",
        runtime: ToolRuntime = None,
    ) -> str:
        """请求用户确认计划。"""
        resolved_context = resolve_agent_context(runtime)
        plan_summary = str(review_markdown or "").strip()
        if not plan_summary:
            raise ValueError("当前计划缺少 review_markdown，无法请求确认。")
        pending_action = resolved_context.storage.get_ai_search_pending_action(resolved_context.task_id, "plan_confirmation", status="pending")
        payload = {
            "plan_summary": plan_summary,
            "confirmation_label": confirmation_label,
        }
        pending_payload = pending_action.get("payload") if isinstance(pending_action, dict) else None
        if not isinstance(pending_payload, dict) or dict(pending_payload or {}) != payload:
            resolved_context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": resolved_context.task_id,
                    "role": "assistant",
                    "kind": "plan_confirmation",
                    "content": plan_summary,
                    "stream_status": "completed",
                    "metadata": payload,
                }
            )
        open_pending_action(
            resolved_context,
            action_type="plan_confirmation",
            source="plan_gate",
            payload=payload,
            run_id="",
            plan_version=0,
            runtime=runtime,
        )
        confirmation = interrupt(payload)
        confirmed = False
        if isinstance(confirmation, dict):
            confirmed = bool(confirmation.get("confirmed"))
        elif isinstance(confirmation, str):
            confirmed = confirmation.strip().lower() in {"true", "yes", "confirmed", "ok"}
        resolved_context.resolve_pending_action(
            "plan_confirmation",
            resolution={"decision": "confirmed" if confirmed else "rejected"},
            runtime=runtime,
        )
        if confirmed:
            resolved_context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime)
        else:
            resolved_context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime)
        return json.dumps({"decision": "confirmed" if confirmed else "not_confirmed"}, ensure_ascii=False)

    def request_human_decision(
        reason: str,
        summary: str,
        confirmation_label: str = "继续检索",
        completion_label: str = "结束当前结果",
        plan_version: int = 0,
        runtime: ToolRuntime = None,
    ) -> str:
        """创建人工决策 interrupt，并在恢复后返回用户选择。"""
        resolved_context = resolve_agent_context(runtime)
        resolved_plan_version = int(plan_version or resolved_context.active_plan_version() or 0)
        run = resolved_context.active_run(resolved_plan_version)
        run_state = resolved_context._run_state(run)
        selected_count = (
            len(resolved_context.storage.list_ai_search_documents(resolved_context.task_id, resolved_plan_version, stages=["selected"]))
            if resolved_plan_version > 0
            else 0
        )
        payload = {
            "available": True,
            "reason": str(reason or "").strip(),
            "summary": str(summary or "").strip(),
            "roundCount": int(run_state.get("execution_round_count") or 0),
            "noProgressRoundCount": int(run_state.get("no_progress_round_count") or 0),
            "selectedCount": selected_count,
            "recommendedActions": ["continue_search", "complete_current_results"],
            "confirmationLabel": str(confirmation_label or "").strip() or "继续检索",
            "completionLabel": str(completion_label or "").strip() or "结束当前结果",
        }
        pending_action = resolved_context.storage.get_ai_search_pending_action(resolved_context.task_id, "human_decision", status="pending")
        pending_payload = pending_action.get("payload") if isinstance(pending_action, dict) else None
        if not isinstance(pending_payload, dict) or dict(pending_payload or {}) != payload:
            resolved_context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": resolved_context.task_id,
                    "plan_version": resolved_plan_version or None,
                    "role": "assistant",
                    "kind": "chat",
                    "content": payload["summary"] or "自动检索已停止，需要人工决策。",
                    "stream_status": "completed",
                    "metadata": {"reason": payload["reason"], "kind": "human_decision"},
                }
            )
        if run:
            updated_state = {
                **run_state,
                "human_decision_reason": payload["reason"] or None,
                "human_decision_summary": payload["summary"] or None,
                "last_exhaustion_reason": payload["reason"] or None,
                "last_exhaustion_summary": payload["summary"] or None,
            }
            resolved_context.storage.update_ai_search_run(
                resolved_context.task_id,
                str(run.get("run_id") or ""),
                phase=PHASE_AWAITING_HUMAN_DECISION,
                status=phase_to_task_status(PHASE_AWAITING_HUMAN_DECISION),
                active_retrieval_todo_id=None,
                selected_document_count=selected_count,
                human_decision_state=updated_state,
            )
        pending_action = open_pending_action(
            resolved_context,
            action_type="human_decision",
            source="human_decision_gate",
            payload=payload,
            run_id=resolved_context.active_run_id(resolved_plan_version),
            plan_version=resolved_plan_version,
            runtime=runtime,
        )
        if not pending_action or not isinstance(pending_action, dict):
            raise ValueError("无法创建人工决策 action。")
        resolved_context.update_task_phase(
            PHASE_AWAITING_HUMAN_DECISION,
            runtime=runtime,
            active_plan_version=resolved_plan_version or None,
            run_id=resolved_context.active_run_id(resolved_plan_version),
            selected_document_count=selected_count,
            current_task=None,
        )
        decision = interrupt(payload)
        if isinstance(decision, dict):
            normalized_decision = str(decision.get("decision") or "").strip()
        else:
            normalized_decision = str(decision or "").strip()
        if normalized_decision not in {"continue_search", "complete_current_results"}:
            normalized_decision = "continue_search"
        resolved_context.resolve_pending_action(
            "human_decision",
            resolution={"decision": normalized_decision},
            runtime=runtime,
        )
        if normalized_decision == "continue_search":
            resolved_context.reset_execution_control(resolved_plan_version, clear_human_decision=True)
            resolved_context.update_task_phase(
                PHASE_DRAFTING_PLAN,
                runtime=runtime,
                active_plan_version=resolved_plan_version or None,
                run_id=resolved_context.active_run_id(resolved_plan_version),
                current_task=None,
            )
        return normalized_decision

    def advance_workflow_command(
        action: str,
        plan_version: int = 0,
        todo_id: str = "",
        next_todo_id: str = "",
        next_action: str = "",
        reason: str = "",
        runtime: ToolRuntime = None,
    ) -> str:
        """执行高层工作流推进动作。"""
        resolved_context = resolve_agent_context(runtime)
        return json.dumps(
            advance_workflow(
                resolved_context,
                action=action,
                plan_version=plan_version,
                todo_id=todo_id,
                next_todo_id=next_todo_id,
                next_action=next_action,
                reason=reason,
                runtime=runtime,
            ),
            ensure_ascii=False,
        )

    advance_workflow_command.__name__ = "advance_workflow"

    def finalize_search_session_command(
        summary: str = "",
        plan_version: int = 0,
        force_from_decision: bool = False,
        runtime: ToolRuntime = None,
    ) -> str:
        """完成当前检索轮并更新终态。"""
        resolved_context = resolve_agent_context(runtime)
        return json.dumps(
            finalize_search_session(
                resolved_context,
                summary=summary,
                plan_version=plan_version,
                force_from_decision=force_from_decision,
                runtime=runtime,
            ),
            ensure_ascii=False,
        )

    finalize_search_session_command.__name__ = "finalize_search_session"

    return [
        get_workflow_context,
        get_workflow_options,
        *build_planning_tools(),
        build_search_specialist_tool(),
        start_plan_drafting,
        request_user_question,
        request_plan_confirmation,
        request_human_decision,
        advance_workflow_command,
        finalize_search_session_command,
    ]
