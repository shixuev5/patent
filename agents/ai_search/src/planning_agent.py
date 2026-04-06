"""
AI 检索主 agent 定义。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend
from langgraph.types import interrupt

from backend.time_utils import utc_now_z

from agents.ai_search.src.checkpointer import AiSearchCheckpointSaver
from agents.ai_search.src.execution_state import normalize_execution_plan
from agents.ai_search.src.runtime import AiSearchGuardMiddleware, extract_json_object, large_model
from agents.ai_search.src.state import (
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_DRAFTING_PLAN,
    build_plan_summary,
    get_ai_search_meta,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from agents.ai_search.src.subagents.query_executor import build_query_executor_subagent
from agents.ai_search.src.subagents.search_elements import (
    build_search_elements_subagent,
    normalize_search_elements_payload,
)


MAIN_AGENT_SYSTEM_PROMPT = """
你是 AI 检索主 agent，负责检索要素整理、检索计划生成，以及确认后的动态执行方向决策。

必须遵守：
1. 每次收到用户需求后，第一步必须调用 `task`，使用 `search-elements` 子 agent。
2. 在任何真实检索开始前，必须先产出结构化检索要素，再产出结构化检索计划。
3. 如果信息不完整，先调用 `update_search_elements` 保存当前要素，再调用 `ask_user_question` 向用户追问。
4. 一旦信息完整，生成结构化检索计划，并调用 `save_search_plan` 保存。随后调用 `request_plan_confirmation`。
5. 计划确认前，不允许进行任何专利检索、粗筛、精读或特征对比。
6. 计划确认后，如果需要动态执行查询，只能通过 `task` 调用 `query-executor` 子 agent。你只允许看到它的摘要结果，不允许要求它返回候选文献明细。
7. 回答要简洁，不要输出 markdown 代码块，不要输出伪造工具结果。

`update_search_elements` 的 payload_json 必须是 JSON 对象，字段固定为：
- status
- objective
- applicants
- filing_date
- priority_date
- search_elements
- missing_items
- clarification_summary

`save_search_plan` 的 payload_json 必须是 JSON 对象，字段固定为：
- objective
- search_elements_snapshot
- query_batches
- selection_criteria
- negative_constraints
- execution_notes
- requires_confirmation
- execution_policy
- lanes
- round_stop_rules
- screening_entry_rules
- replan_rules

额外规则：
1. `applicants` 可以为空数组，但若为空，应在要素摘要中明确提示无法执行申请人追溯检索。
2. `filing_date` 与 `priority_date` 优先使用 `YYYY-MM-DD`。
3. 若 `filing_date` 与 `priority_date` 都缺失，必须把“申请日或优先权日”写入 `missing_items`。
4. 只有当检索目标缺失，或至少一个技术要素缺失时，才应停在追问阶段。
5. `lanes` 用于表达主 agent 对执行方向和优先级的规划，必须体现 lane_type、priority、enabled_when、batch_specs。
6. 确认后若进入查询执行阶段，可多轮调用 `query-executor`，但只能基于摘要结果调整方向，不能把候选专利全文或大段列表带回主上下文。
""".strip()


def build_planning_agent(storage: Any, task_id: str):
    checkpointer = AiSearchCheckpointSaver(storage)

    def update_task_phase(phase: str, **ai_search_updates: Any) -> None:
        task = storage.get_task(task_id)
        metadata = merge_ai_search_meta(task, **ai_search_updates)
        storage.update_task(
            task_id,
            metadata=metadata,
            status=phase_to_task_status(phase),
            progress=phase_progress(phase),
            current_step=phase_step(phase),
        )

    def find_message_by_question_id(question_id: str) -> Optional[Dict[str, Any]]:
        for item in reversed(storage.list_ai_search_messages(task_id)):
            if str(item.get("question_id") or "") == str(question_id):
                return item
        return None

    def update_search_elements(payload_json: str) -> str:
        """保存结构化检索要素，并在要素完整后推进到起草计划阶段。"""
        payload = normalize_search_elements_payload(extract_json_object(payload_json))
        storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": task_id,
                "role": "assistant",
                "kind": "search_elements_update",
                "content": str(payload.get("clarification_summary") or payload.get("objective") or "").strip() or None,
                "stream_status": "completed",
                "metadata": payload,
            }
        )
        if str(payload.get("status") or "").strip().lower() == "complete":
            update_task_phase(PHASE_DRAFTING_PLAN)
        return "search elements updated"

    def save_search_plan(payload_json: str) -> str:
        """持久化当前检索计划草案，并生成新的 plan version。"""
        payload = extract_json_object(payload_json)
        search_elements_snapshot = normalize_search_elements_payload(payload.get("search_elements_snapshot") or {})
        normalized_plan = normalize_execution_plan(
            {
                **payload,
                "search_elements_snapshot": search_elements_snapshot,
            },
            search_elements_snapshot,
        )
        latest_plan = storage.get_ai_search_plan(task_id)
        if latest_plan:
            storage.update_ai_search_plan(
                task_id,
                int(latest_plan["plan_version"]),
                status="superseded",
                superseded_at=utc_now_z(),
            )
        plan_version = storage.get_next_ai_search_plan_version(task_id)
        storage.create_ai_search_plan(
            {
                "task_id": task_id,
                "plan_version": plan_version,
                "status": "draft",
                "objective": payload.get("objective") or search_elements_snapshot.get("objective"),
                "search_elements_json": search_elements_snapshot,
                "plan_json": {
                    "plan_version": plan_version,
                    "status": "draft",
                    **normalized_plan,
                },
            }
        )
        update_task_phase(
            PHASE_DRAFTING_PLAN,
            active_plan_version=plan_version,
            pending_confirmation_plan_version=None,
        )
        return json.dumps({"plan_version": plan_version}, ensure_ascii=False)

    def ask_user_question(prompt: str, reason: str, expected_answer_shape: str) -> str:
        """创建或恢复一条追问，挂起执行并等待用户回答。"""
        task = storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        question_id = str(meta.get("pending_question_id") or "").strip()
        payload: Dict[str, Any]
        if question_id:
            existing = find_message_by_question_id(question_id)
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
            storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": task_id,
                    "role": "assistant",
                    "kind": "question",
                    "content": prompt,
                    "stream_status": "completed",
                    "question_id": question_id,
                    "metadata": payload,
                }
            )
            update_task_phase(PHASE_AWAITING_USER_ANSWER, pending_question_id=question_id)
        answer = interrupt(payload)
        update_task_phase(PHASE_DRAFTING_PLAN, pending_question_id=None)
        return str(answer or "").strip()

    def request_plan_confirmation(
        plan_version: int,
        plan_summary: str,
        confirmation_label: str = "确认检索计划",
    ) -> str:
        """请求用户确认指定版本的检索计划，并在确认后更新计划状态。"""
        task = storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        pending_plan_version = meta.get("pending_confirmation_plan_version")
        payload = {
            "plan_version": int(plan_version),
            "plan_summary": plan_summary,
            "confirmation_label": confirmation_label,
        }
        if int(pending_plan_version or 0) != int(plan_version):
            storage.update_ai_search_plan(task_id, int(plan_version), status="awaiting_confirmation")
            storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": task_id,
                    "plan_version": int(plan_version),
                    "role": "assistant",
                    "kind": "plan_confirmation",
                    "content": plan_summary,
                    "stream_status": "completed",
                    "metadata": payload,
                }
            )
            update_task_phase(
                PHASE_AWAITING_PLAN_CONFIRMATION,
                pending_confirmation_plan_version=int(plan_version),
            )
        confirmation = interrupt(payload)
        confirmed = False
        if isinstance(confirmation, dict):
            confirmed = bool(confirmation.get("confirmed"))
        elif isinstance(confirmation, str):
            confirmed = confirmation.strip().lower() in {"true", "yes", "confirmed", "ok"}
        if confirmed:
            storage.update_ai_search_plan(
                task_id,
                int(plan_version),
                status="confirmed",
                confirmed_at=utc_now_z(),
            )
        update_task_phase(PHASE_DRAFTING_PLAN, pending_confirmation_plan_version=None)
        return "confirmed" if confirmed else "not_confirmed"

    return create_deep_agent(
        model=large_model(),
        tools=[
            update_search_elements,
            save_search_plan,
            ask_user_question,
            request_plan_confirmation,
        ],
        system_prompt=MAIN_AGENT_SYSTEM_PROMPT,
        middleware=[AiSearchGuardMiddleware()],
        subagents=[
            build_search_elements_subagent(),
            build_query_executor_subagent(storage, task_id),
        ],
        checkpointer=checkpointer,
        backend=StateBackend,
        name=f"ai-search-planning-{task_id}",
    )
