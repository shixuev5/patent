"""Main-agent orchestration tools."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from langchain.tools import ToolRuntime
from langgraph.types import interrupt

from agents.ai_search.src.execution_state import enrich_execution_round_summary, normalize_execution_plan
from agents.ai_search.src.execution_state import decide_search_transition as decide_search_transition_from_summary
from agents.ai_search.src.runtime import extract_json_object
from agents.ai_search.src.state import (
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_CLAIM_DECOMPOSITION,
    PHASE_CLOSE_READ,
    PHASE_COMPLETED,
    PHASE_COARSE_SCREEN,
    PHASE_DRAFTING_PLAN,
    PHASE_EXECUTE_SEARCH,
    PHASE_GENERATE_FEATURE_TABLE,
    PHASE_SEARCH_STRATEGY,
    build_plan_summary,
    get_ai_search_meta,
)
from agents.ai_search.src.subagents.search_elements.normalize import normalize_search_elements_payload
from backend.time_utils import utc_now_z


def build_main_agent_tools(context: Any) -> List[Any]:
    def read_todos() -> str:
        """读取当前任务清单。"""
        task = context.storage.get_task(context.task_id)
        meta = get_ai_search_meta(task)
        todos = context._current_todos(task)
        return json.dumps(
            {
                "todos": todos,
                "current_task": meta.get("current_task"),
                "search_mode": context.current_search_mode(),
            },
            ensure_ascii=False,
        )

    def write_todos(payload_json: str, runtime: ToolRuntime = None) -> str:
        """写入当前任务清单。"""
        task = context.storage.get_task(context.task_id)
        payload = extract_json_object(payload_json)
        raw_todos = payload.get("todos") if isinstance(payload.get("todos"), list) else []
        existing_by_key = context._todo_map(task)
        todos: List[Dict[str, Any]] = []
        for item in raw_todos:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            title = str(item.get("title") or "").strip()
            if not key or not title:
                continue
            todos.append(context._normalized_todo(item, existing=existing_by_key.get(key)))
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

    def get_claim_context() -> str:
        """读取最新的 claim decomposition 和 search strategy。"""
        return json.dumps(
            {
                "claim_decomposition": context.latest_message_metadata("claim_decomposition"),
                "claim_search_strategy": context.latest_message_metadata("claim_search_strategy"),
            },
            ensure_ascii=False,
        )

    def get_gap_context() -> str:
        """读取最新的 limitation coverage、gap 和 creativity readiness 上下文。"""
        return json.dumps(context.latest_gap_context(), ensure_ascii=False)

    def evaluate_gap_progress(plan_version: int = 0) -> str:
        """根据最新 gap/readiness 状态给出下一步建议。"""
        return json.dumps(context.evaluate_gap_progress_payload(plan_version), ensure_ascii=False)

    def decide_search_transition(plan_version: int = 0) -> str:
        """根据搜索轮次摘要与计划规则决定下一步是继续检索、转粗筛还是回到重规划。"""
        version = int(plan_version or context.active_plan_version() or 0)
        plan_json = context.execution_plan_json(version)
        summaries = context.list_execution_summaries(version)
        payload = {
            "plan_version": version,
            **decide_search_transition_from_summary(plan_json, summaries),
            "latest_summary": enrich_execution_round_summary(summaries[-1]) if summaries else {},
        }
        return json.dumps(payload, ensure_ascii=False)

    def start_claim_decomposition(runtime: ToolRuntime = None) -> str:
        """显式进入 claim decomposition 阶段。"""
        context.update_task_phase(PHASE_CLAIM_DECOMPOSITION, runtime=runtime, current_task="claim_decomposition")
        context.update_todos("claim_decomposition", "in_progress", current_task="claim_decomposition")
        return "phase switched to claim_decomposition"

    def start_search_strategy(runtime: ToolRuntime = None) -> str:
        """显式进入 search strategy 阶段。"""
        context.update_task_phase(PHASE_SEARCH_STRATEGY, runtime=runtime, current_task="search_strategy")
        context.update_todos("search_strategy", "in_progress", current_task="search_strategy")
        return "phase switched to search_strategy"

    def start_plan_drafting(runtime: ToolRuntime = None) -> str:
        """显式进入 draft plan 阶段。"""
        context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime, current_task="draft_plan")
        context.update_todos("draft_plan", "in_progress", current_task="draft_plan")
        return "phase switched to drafting_plan"

    def save_search_plan(payload_json: str, runtime: ToolRuntime = None) -> str:
        """持久化检索计划草案。"""
        payload = extract_json_object(payload_json)
        search_elements_snapshot = normalize_search_elements_payload(payload.get("search_elements_snapshot") or {})
        normalized_plan = normalize_execution_plan(
            {**payload, "search_elements_snapshot": search_elements_snapshot},
            search_elements_snapshot,
        )
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
                "objective": payload.get("objective") or search_elements_snapshot.get("objective"),
                "search_elements_json": search_elements_snapshot,
                "plan_json": {"plan_version": plan_version, "status": "draft", **normalized_plan},
            }
        )
        context.update_task_phase(
            PHASE_DRAFTING_PLAN,
            runtime=runtime,
            active_plan_version=plan_version,
            pending_confirmation_plan_version=None,
        )
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
        confirmation_label: str = "确认检索计划",
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
            summary = build_plan_summary(context.storage.get_ai_search_plan(context.task_id, int(plan_version)))
            context.update_task_phase(
                PHASE_DRAFTING_PLAN,
                runtime=runtime,
                pending_confirmation_plan_version=None,
                current_task="execute_search",
                todos=[
                    context._normalized_todo({"key": "execute_search", "title": "执行检索召回", "status": "pending", "resume_from": "run_search_round.load"}),
                    context._normalized_todo({"key": "coarse_screen", "title": "粗筛候选文献", "status": "pending", "resume_from": "run_coarse_screen_batch.load"}),
                    context._normalized_todo({"key": "close_read", "title": "精读并提取证据", "status": "pending", "resume_from": "run_close_read_batch.load"}),
                    context._normalized_todo(
                        {
                            "key": "generate_feature_table",
                            "title": "生成特征对比表",
                            "status": "pending",
                            "details": json.dumps(summary, ensure_ascii=False),
                            "resume_from": "run_feature_compare.load",
                        }
                    ),
                ],
            )
        else:
            context.update_task_phase(PHASE_DRAFTING_PLAN, runtime=runtime, pending_confirmation_plan_version=None)
        return "confirmed" if confirmed else "not_confirmed"

    def begin_execution(plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """标记执行阶段开始。"""
        version = int(plan_version or context.active_plan_version() or 0)
        if version <= 0:
            return "missing_plan"
        context.update_task_phase(PHASE_EXECUTE_SEARCH, runtime=runtime, active_plan_version=version, current_task="execute_search")
        context.update_todos(
            "execute_search",
            "in_progress",
            current_task="execute_search",
            resume_from="run_search_round.load",
            state_updates={"plan_version": version},
        )
        return json.dumps({"plan_version": version}, ensure_ascii=False)

    def start_coarse_screen(plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """显式进入 coarse screen 阶段。"""
        version = int(plan_version or context.active_plan_version() or 0)
        candidate_count = len(context.storage.list_ai_search_documents(context.task_id, version))
        if candidate_count <= 0:
            return "no_candidates_for_coarse_screen"
        context.update_task_phase(PHASE_COARSE_SCREEN, runtime=runtime, active_plan_version=version, current_task="coarse_screen")
        context.update_todos(
            "coarse_screen",
            "in_progress",
            current_task="coarse_screen",
            resume_from="run_coarse_screen_batch.load",
            state_updates={"plan_version": version, "candidate_count": candidate_count},
        )
        return json.dumps({"plan_version": version, "candidate_count": candidate_count}, ensure_ascii=False)

    def start_close_read(plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """显式进入 close read 阶段。"""
        version = int(plan_version or context.active_plan_version() or 0)
        shortlisted_count = len(context.storage.list_ai_search_documents(context.task_id, version, stages=["shortlisted"]))
        if shortlisted_count <= 0:
            return "no_shortlisted_documents"
        context.update_task_phase(PHASE_CLOSE_READ, runtime=runtime, active_plan_version=version, current_task="close_read")
        context.update_todos(
            "close_read",
            "in_progress",
            current_task="close_read",
            resume_from="run_close_read_batch.load",
            state_updates={"plan_version": version, "shortlisted_count": shortlisted_count},
        )
        return json.dumps({"plan_version": version, "shortlisted_count": shortlisted_count}, ensure_ascii=False)

    def start_feature_table_generation(plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """显式进入 feature table 阶段。"""
        version = int(plan_version or context.active_plan_version() or 0)
        selected_count = len(context.storage.list_ai_search_documents(context.task_id, version, stages=["selected"]))
        if selected_count <= 0:
            return "no_selected_documents"
        context.update_task_phase(
            PHASE_GENERATE_FEATURE_TABLE,
            runtime=runtime,
            active_plan_version=version,
            current_task="generate_feature_table",
        )
        context.update_todos(
            "generate_feature_table",
            "in_progress",
            current_task="generate_feature_table",
            resume_from="run_feature_compare.load",
            state_updates={"plan_version": version, "selected_count": selected_count},
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
                "search_mode": context.current_search_mode(),
                "todos": todos,
                "current_task": meta.get("current_task"),
                "recovery": {
                    "resume_from": current_todo.get("resume_from"),
                    "attempt_count": int(current_todo.get("attempt_count") or 0),
                    "last_error": current_todo.get("last_error") or "",
                    "current_todo_state": current_todo.get("state") if isinstance(current_todo.get("state"), dict) else {},
                },
                "plan": plan.get("plan_json") if isinstance(plan.get("plan_json"), dict) else {},
                "search_elements": plan.get("search_elements_json") if isinstance(plan.get("search_elements_json"), dict) else {},
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

    def complete_execution(summary: str = "", plan_version: int = 0, runtime: ToolRuntime = None) -> str:
        """结束执行阶段并更新汇总状态。"""
        version = int(plan_version or context.active_plan_version() or 0)
        current_phase = context.current_phase()
        if current_phase == PHASE_GENERATE_FEATURE_TABLE:
            progress = context.evaluate_gap_progress_payload(version)
            if str(progress.get("recommended_action") or "") == "replan_search_strategy":
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
            current_task=None,
        )
        return json.dumps({"selected_count": selected_count}, ensure_ascii=False)

    return [
        read_todos,
        write_todos,
        get_search_elements,
        get_claim_context,
        get_gap_context,
        evaluate_gap_progress,
        decide_search_transition,
        start_claim_decomposition,
        start_search_strategy,
        start_plan_drafting,
        save_search_plan,
        ask_user_question,
        request_plan_confirmation,
        begin_execution,
        start_coarse_screen,
        start_close_read,
        start_feature_table_generation,
        get_execution_state,
        list_documents,
        complete_execution,
    ]
