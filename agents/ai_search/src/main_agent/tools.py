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
    complete_session,
)
from agents.ai_search.src.orchestration.phase_machine import enter_drafting_plan
from agents.ai_search.src.orchestration.planning_runtime import (
    build_planning_context,
    publish_planner_draft,
)
from agents.ai_search.src.orchestration.session_views import build_session_context
from agents.ai_search.src.state import (
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_HUMAN_DECISION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_DRAFTING_PLAN,
    phase_to_task_status,
)
from backend.time_utils import utc_now_z


def build_main_agent_tools() -> List[Any]:
    def get_session_context(runtime: ToolRuntime = None) -> str:
        """读取当前会话级上下文。"""
        resolved_context = resolve_agent_context(runtime)
        return json.dumps(build_session_context(resolved_context), ensure_ascii=False)

    def get_planning_context(plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """读取规划阶段所需上下文。"""
        resolved_context = resolve_agent_context(runtime)
        return json.dumps(build_planning_context(resolved_context, plan_version), ensure_ascii=False)

    def get_execution_context(plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """读取执行阶段所需上下文。"""
        resolved_context = resolve_agent_context(runtime)
        return json.dumps(build_execution_context(resolved_context, plan_version), ensure_ascii=False)

    def start_plan_drafting(runtime: ToolRuntime = None) -> str:
        """显式进入 draft plan 阶段。"""
        resolved_context = resolve_agent_context(runtime)
        return json.dumps(enter_drafting_plan(resolved_context, runtime=runtime), ensure_ascii=False)

    def publish_planner_draft_command(runtime: ToolRuntime = None) -> str:
        """将当前 planner 草案发布为正式计划。"""
        resolved_context = resolve_agent_context(runtime)
        return json.dumps(publish_planner_draft(resolved_context, runtime=runtime), ensure_ascii=False)

    publish_planner_draft_command.__name__ = "publish_planner_draft"

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
        plan_version: int,
        confirmation_label: str = "实施此计划",
        runtime: ToolRuntime = None,
    ) -> str:
        """请求用户确认计划。"""
        resolved_context = resolve_agent_context(runtime)
        plan = resolved_context.storage.get_ai_search_plan(resolved_context.task_id, int(plan_version))
        if not isinstance(plan, dict):
            raise ValueError("指定的 plan_version 不存在。")
        plan_summary = str(plan.get("review_markdown") or "").strip()
        if not plan_summary:
            raise ValueError("当前计划缺少 review_markdown，无法请求确认。")
        pending_action = resolved_context.storage.get_ai_search_pending_action(resolved_context.task_id, "plan_confirmation", status="pending")
        payload = {
            "plan_version": int(plan_version),
            "plan_summary": plan_summary,
            "confirmation_label": confirmation_label,
        }
        pending_payload = pending_action.get("payload") if isinstance(pending_action, dict) else None
        if not isinstance(pending_payload, dict) or int(pending_payload.get("plan_version") or 0) != int(plan_version):
            resolved_context.storage.update_ai_search_plan(resolved_context.task_id, int(plan_version), status="awaiting_confirmation")
            resolved_context.storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": resolved_context.task_id,
                    "plan_version": int(plan_version),
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
            run_id=resolved_context.active_run_id(),
            plan_version=int(plan_version),
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
            resolved_context.storage.update_ai_search_plan(resolved_context.task_id, int(plan_version), status="confirmed", confirmed_at=utc_now_z())
            resolved_context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime, active_plan_version=int(plan_version))
        else:
            resolved_context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime)
        return "confirmed" if confirmed else "not_confirmed"

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

    def complete_session_command(
        summary: str = "",
        plan_version: int = 0,
        force_from_decision: bool = False,
        runtime: ToolRuntime = None,
    ) -> str:
        """结束当前轮并更新终态。"""
        resolved_context = resolve_agent_context(runtime)
        return json.dumps(
            complete_session(
                resolved_context,
                summary=summary,
                plan_version=plan_version,
                force_from_decision=force_from_decision,
                runtime=runtime,
            ),
            ensure_ascii=False,
        )

    complete_session_command.__name__ = "complete_session"

    return [
        get_session_context,
        get_planning_context,
        get_execution_context,
        start_plan_drafting,
        publish_planner_draft_command,
        request_user_question,
        request_plan_confirmation,
        request_human_decision,
        advance_workflow_command,
        complete_session_command,
    ]
