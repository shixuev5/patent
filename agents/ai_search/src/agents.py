"""
Deep Agents factory for AI search.
"""

from __future__ import annotations

import json
import uuid
from functools import lru_cache
from typing import Any, Dict, List, Optional

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from config import settings
from backend.storage import TaskType
from backend.time_utils import utc_now_z

from .checkpointer import AiSearchCheckpointSaver
from .state import (
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


ALLOWED_SUBAGENTS = {
    "search-elements",
    "coarse-screener",
    "close-reader",
    "feature-comparer",
}
BLOCKED_TOOLS = {
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",
}


class CoarseScreenOutput(BaseModel):
    keep: List[str] = Field(default_factory=list)
    discard: List[str] = Field(default_factory=list)
    reasoning_summary: str = ""


class KeyPassageOutput(BaseModel):
    document_id: str
    passage: str
    reason: str = ""
    location: Optional[str] = None


class CloseReaderOutput(BaseModel):
    selected: List[str] = Field(default_factory=list)
    rejected: List[str] = Field(default_factory=list)
    key_passages: List[KeyPassageOutput] = Field(default_factory=list)
    selection_summary: str = ""


class FeatureCompareOutput(BaseModel):
    table_rows: List[Dict[str, Any]] = Field(default_factory=list)
    summary_markdown: str = ""
    overall_findings: str = ""


class AiSearchGuardMiddleware(AgentMiddleware):
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ) -> ToolMessage | Command[Any]:
        tool_name = str(request.tool_call.get("name") or "").strip()
        if tool_name in BLOCKED_TOOLS:
            return ToolMessage(
                content=f"Tool `{tool_name}` is disabled in AI search.",
                name=tool_name or "blocked_tool",
                tool_call_id=request.tool_call["id"],
            )
        if tool_name == "task":
            subagent_type = str((request.tool_call.get("args") or {}).get("subagent_type") or "").strip()
            if subagent_type not in ALLOWED_SUBAGENTS:
                return ToolMessage(
                    content=f"Subagent `{subagent_type or 'unknown'}` is not allowed in AI search.",
                    name="task",
                    tool_call_id=request.tool_call["id"],
                )
        return handler(request)


def _build_chat_model(model_name: Optional[str]) -> ChatOpenAI:
    resolved_model = str(model_name or "").strip()
    if not resolved_model:
        raise ValueError("LLM model is not configured for AI search")
    if not settings.LLM_API_KEY:
        raise ValueError("LLM_API_KEY is required for AI search")
    return ChatOpenAI(
        model=resolved_model,
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        temperature=0,
        timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
    )


def default_model() -> ChatOpenAI:
    return _build_chat_model(settings.LLM_MODEL_DEFAULT)


def large_model() -> ChatOpenAI:
    return _build_chat_model(settings.LLM_MODEL_LARGE or settings.LLM_MODEL_DEFAULT)


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = raw.strip("`")
        parts = raw.split("\n", 1)
        raw = parts[1] if len(parts) > 1 else raw
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def extract_latest_ai_message(result: Dict[str, Any]) -> str:
    messages = result.get("messages") if isinstance(result, dict) else None
    if not isinstance(messages, list):
        return ""
    for item in reversed(messages):
        if isinstance(item, AIMessage):
            if item.tool_calls:
                continue
            content = item.content
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                chunks = [str(part.get("text") or "") for part in content if isinstance(part, dict)]
                return "".join(chunks).strip()
    return ""


def extract_structured_response(result: Dict[str, Any]) -> Dict[str, Any]:
    structured = result.get("structured_response") if isinstance(result, dict) else None
    if isinstance(structured, BaseModel):
        return structured.model_dump()
    if isinstance(structured, dict):
        return structured
    content = extract_latest_ai_message(result)
    return _extract_json_object(content)


MAIN_AGENT_SYSTEM_PROMPT = """
你是 AI 检索主 agent，负责检索要素整理、检索计划生成、计划确认前的版本管理。

必须遵守：
1. 每次收到用户需求后，第一步必须调用 `task`，使用 `search-elements` 子 agent。
2. 在任何真实检索开始前，必须先产出结构化检索要素，再产出结构化检索计划。
3. 如果信息不完整，先调用 `update_search_elements` 保存当前要素，再调用 `ask_user_question` 向用户追问。
4. 一旦信息完整，生成结构化检索计划，并调用 `save_search_plan` 保存。随后调用 `request_plan_confirmation`。
5. 计划确认前，不允许进行任何专利检索、粗筛、精读或特征对比。
6. 回答要简洁，不要输出 markdown 代码块，不要输出伪造工具结果。

`update_search_elements` 的 payload_json 必须是 JSON 对象，字段固定为：
- status
- objective
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
""".strip()


SEARCH_ELEMENTS_SYSTEM_PROMPT = """
你是 `search-elements` 子 agent。

唯一职责：从用户输入和当前上下文中构建检索要素表。

要求：
1. 只做信息抽取与澄清判断，不做专利检索。
2. 如果信息不足，返回 `status=needs_answer`，并给出明确的 `missing_items`。
3. 如果信息完整，返回 `status=complete`。
4. 最终输出必须是一个 JSON 对象，不要加任何解释文字或 markdown。

输出字段固定：
- status
- objective
- search_elements
- missing_items
- clarification_summary
""".strip()


COARSE_SCREEN_SYSTEM_PROMPT = """
你是 `coarse-screener` 子 agent。

唯一职责：根据标题、摘要、分类号和来源批次，对候选结果做相关性粗筛。
不能读取全文长段落，不能决定最终对比文件。

输出必须为结构化对象：
- keep: 保留的 document_id 列表
- discard: 排除的 document_id 列表
- reasoning_summary: 简短原因摘要
""".strip()


CLOSE_READER_SYSTEM_PROMPT = """
你是 `close-reader` 子 agent。

唯一职责：根据检索要素和重点段落，判断 shortlisted 文献是否应纳入对比文件。
必须基于证据作出判断。

输出必须为结构化对象：
- selected
- rejected
- key_passages
- selection_summary
""".strip()


FEATURE_COMPARER_SYSTEM_PROMPT = """
你是 `feature-comparer` 子 agent。

唯一职责：基于当前 selected 文献和证据段落，输出特征对比表。
不能新增或删除对比文件。

输出必须为结构化对象：
- table_rows
- summary_markdown
- overall_findings
""".strip()


def build_planning_agent(storage: Any, task_id: str):
    checkpointer = AiSearchCheckpointSaver(storage)

    def _update_task_phase(phase: str, **ai_search_updates: Any) -> None:
        task = storage.get_task(task_id)
        metadata = merge_ai_search_meta(task, **ai_search_updates)
        storage.update_task(
            task_id,
            metadata=metadata,
            status=phase_to_task_status(phase),
            progress=phase_progress(phase),
            current_step=phase_step(phase),
        )

    def _find_message_by_question_id(question_id: str) -> Optional[Dict[str, Any]]:
        for item in reversed(storage.list_ai_search_messages(task_id)):
            if str(item.get("question_id") or "") == str(question_id):
                return item
        return None

    def update_search_elements(payload_json: str) -> str:
        payload = _extract_json_object(payload_json)
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
        status = str(payload.get("status") or "").strip().lower()
        if status == "complete":
            _update_task_phase(PHASE_DRAFTING_PLAN)
        return "search elements updated"

    def save_search_plan(payload_json: str) -> str:
        payload = _extract_json_object(payload_json)
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
                "objective": payload.get("objective"),
                "search_elements_json": payload.get("search_elements_snapshot") or {},
                "plan_json": {
                    "plan_version": plan_version,
                    "status": "draft",
                    **payload,
                },
            }
        )
        _update_task_phase(
            PHASE_DRAFTING_PLAN,
            active_plan_version=plan_version,
            pending_confirmation_plan_version=None,
        )
        return json.dumps({"plan_version": plan_version}, ensure_ascii=False)

    def ask_user_question(prompt: str, reason: str, expected_answer_shape: str) -> str:
        task = storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        question_id = str(meta.get("pending_question_id") or "").strip()
        payload: Dict[str, Any]
        if question_id:
            existing = _find_message_by_question_id(question_id)
            payload = existing.get("metadata") if existing and isinstance(existing.get("metadata"), dict) else {}
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
            _update_task_phase(PHASE_AWAITING_USER_ANSWER, pending_question_id=question_id)
        answer = interrupt(payload)
        _update_task_phase(PHASE_DRAFTING_PLAN, pending_question_id=None)
        return str(answer or "").strip()

    def request_plan_confirmation(
        plan_version: int,
        plan_summary: str,
        confirmation_label: str = "确认检索计划",
    ) -> str:
        task = storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        pending_plan_version = meta.get("pending_confirmation_plan_version")
        if int(pending_plan_version or 0) == int(plan_version):
            payload = {
                "plan_version": int(plan_version),
                "plan_summary": plan_summary,
                "confirmation_label": confirmation_label,
            }
        else:
            payload = {
                "plan_version": int(plan_version),
                "plan_summary": plan_summary,
                "confirmation_label": confirmation_label,
            }
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
            _update_task_phase(
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
        _update_task_phase(PHASE_DRAFTING_PLAN, pending_confirmation_plan_version=None)
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
            {
                "name": "search-elements",
                "description": "Build the structured search elements table from user requirements and report missing information as pure JSON.",
                "system_prompt": SEARCH_ELEMENTS_SYSTEM_PROMPT,
                "model": large_model(),
                "tools": [],
                "middleware": [AiSearchGuardMiddleware()],
            }
        ],
        checkpointer=checkpointer,
        backend=StateBackend,
        name=f"ai-search-planning-{task_id}",
    )


@lru_cache(maxsize=1)
def build_coarse_screener_agent():
    return create_deep_agent(
        model=default_model(),
        tools=[],
        system_prompt=COARSE_SCREEN_SYSTEM_PROMPT,
        middleware=[AiSearchGuardMiddleware()],
        response_format=CoarseScreenOutput,
        backend=StateBackend,
        name="ai-search-coarse-screener",
    )


@lru_cache(maxsize=1)
def build_close_reader_agent():
    return create_deep_agent(
        model=large_model(),
        tools=[],
        system_prompt=CLOSE_READER_SYSTEM_PROMPT,
        middleware=[AiSearchGuardMiddleware()],
        response_format=CloseReaderOutput,
        backend=StateBackend,
        name="ai-search-close-reader",
    )


@lru_cache(maxsize=1)
def build_feature_comparer_agent():
    return create_deep_agent(
        model=large_model(),
        tools=[],
        system_prompt=FEATURE_COMPARER_SYSTEM_PROMPT,
        middleware=[AiSearchGuardMiddleware()],
        response_format=FeatureCompareOutput,
        backend=StateBackend,
        name="ai-search-feature-comparer",
    )
