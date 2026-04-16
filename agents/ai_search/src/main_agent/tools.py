"""主控代理的高层编排工具。"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from langchain.tools import ToolRuntime
from langgraph.types import interrupt

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
    PHASE_AWAITING_USER_ANSWER,
    PHASE_DRAFTING_PLAN,
)
from backend.time_utils import utc_now_z


def build_main_agent_tools(context: Any) -> List[Any]:
    def write_stage_log(
        content: str,
        status: str = "completed",
        append: bool = False,
        runtime: ToolRuntime = None,
    ) -> str:
        """写入当前主控阶段的用户可见工作日志。"""
        stage_kind = context.default_stage_kind_for_phase()
        if not stage_kind:
            raise ValueError("当前 phase 不支持主控阶段日志。")
        result = context.write_stage_log(
            stage_kind=stage_kind,
            content=str(content or ""),
            status=str(status or "completed"),
            append=bool(append),
            runtime=runtime,
        )
        return json.dumps(
            {
                "message_id": str(result.get("message_id") or "").strip(),
                "stage_instance_id": str(result.get("stage_instance_id") or "").strip(),
                "status": str(result.get("status") or "").strip(),
            },
            ensure_ascii=False,
        )

    def get_session_context() -> str:
        """读取当前会话级上下文。"""
        return json.dumps(build_session_context(context), ensure_ascii=False)

    def get_planning_context(plan_version: int = 0) -> str:
        """读取规划阶段所需上下文。"""
        return json.dumps(build_planning_context(context, plan_version), ensure_ascii=False)

    def get_execution_context(plan_version: int = 0) -> str:
        """读取执行阶段所需上下文。"""
        return json.dumps(build_execution_context(context, plan_version), ensure_ascii=False)

    def start_plan_drafting(runtime: ToolRuntime = None) -> str:
        """显式进入 draft plan 阶段。"""
        return json.dumps(enter_drafting_plan(context, runtime=runtime), ensure_ascii=False)

    def publish_planner_draft_command(runtime: ToolRuntime = None) -> str:
        """将当前 planner 草案发布为正式计划。"""
        return json.dumps(publish_planner_draft(context, runtime=runtime), ensure_ascii=False)

    publish_planner_draft_command.__name__ = "publish_planner_draft"

    def request_user_question(
        prompt: str,
        reason: str,
        expected_answer_shape: str,
        runtime: ToolRuntime = None,
    ) -> str:
        """创建追问并挂起。"""
        pending = context.storage.get_ai_search_pending_action(context.task_id, "question", status="pending")
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
        open_pending_action(
            context,
            action_type="question",
            source="agent_prompted",
            payload=payload,
            run_id=context.active_run_id(),
            plan_version=context.active_plan_version(),
            runtime=runtime,
        )
        answer = interrupt(payload)
        context.resolve_pending_action(
            "question",
            resolution={"answer": str(answer or "").strip()},
            runtime=runtime,
        )
        context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime)
        return str(answer or "").strip()

    def request_plan_confirmation(
        plan_version: int,
        plan_summary: str,
        confirmation_label: str = "实施此计划",
        runtime: ToolRuntime = None,
    ) -> str:
        """请求用户确认计划。"""
        pending_action = context.storage.get_ai_search_pending_action(context.task_id, "plan_confirmation", status="pending")
        payload = {
            "plan_version": int(plan_version),
            "plan_summary": plan_summary,
            "confirmation_label": confirmation_label,
        }
        pending_payload = pending_action.get("payload") if isinstance(pending_action, dict) else None
        if not isinstance(pending_payload, dict) or int(pending_payload.get("plan_version") or 0) != int(plan_version):
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
        open_pending_action(
            context,
            action_type="plan_confirmation",
            source="plan_gate",
            payload=payload,
            run_id=context.active_run_id(),
            plan_version=int(plan_version),
            runtime=runtime,
        )
        confirmation = interrupt(payload)
        confirmed = False
        if isinstance(confirmation, dict):
            confirmed = bool(confirmation.get("confirmed"))
        elif isinstance(confirmation, str):
            confirmed = confirmation.strip().lower() in {"true", "yes", "confirmed", "ok"}
        context.resolve_pending_action(
            "plan_confirmation",
            resolution={"decision": "confirmed" if confirmed else "rejected"},
            runtime=runtime,
        )
        if confirmed:
            context.storage.update_ai_search_plan(context.task_id, int(plan_version), status="confirmed", confirmed_at=utc_now_z())
            context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime, active_plan_version=int(plan_version))
        else:
            context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime)
        return "confirmed" if confirmed else "not_confirmed"

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
        return json.dumps(
            advance_workflow(
                context,
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
        return json.dumps(
            complete_session(
                context,
                summary=summary,
                plan_version=plan_version,
                force_from_decision=force_from_decision,
                runtime=runtime,
            ),
            ensure_ascii=False,
        )

    complete_session_command.__name__ = "complete_session"

    return [
        write_stage_log,
        get_session_context,
        get_planning_context,
        get_execution_context,
        start_plan_drafting,
        publish_planner_draft_command,
        request_user_question,
        request_plan_confirmation,
        advance_workflow_command,
        complete_session_command,
    ]
