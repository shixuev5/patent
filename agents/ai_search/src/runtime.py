"""智能检索代理的共享运行时工具。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Sequence

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.types import Command
from pydantic import BaseModel

from agents.ai_search.src.state import (
    allowed_main_agent_subagents,
    allowed_main_agent_tools,
    allowed_role_tools,
    get_ai_search_meta,
)
from config import settings


ALL_AI_SEARCH_SUBAGENTS = {
    "search-elements",
    "planner",
    "query-executor",
    "plan-prober",
    "coarse-screener",
    "close-reader",
    "feature-comparer",
}

SUBAGENT_DISPLAY_LABELS = {
    "search-elements": "检索要素整理",
    "planner": "检索规划",
    "query-executor": "检索执行",
    "plan-prober": "计划预检",
    "coarse-screener": "候选粗筛",
    "close-reader": "重点精读",
    "feature-comparer": "特征对比",
}

ROLE_DISPLAY_LABELS = {
    "main-agent": "主控代理",
    **SUBAGENT_DISPLAY_LABELS,
}

TOOL_DISPLAY_LABELS = {
    "get_session_context": "读取会话上下文",
    "get_planning_context": "读取规划上下文",
    "get_execution_context": "读取执行上下文",
    "start_plan_drafting": "进入规划阶段",
    "publish_planner_draft": "发布计划草案",
    "request_user_question": "请求用户补充信息",
    "request_plan_confirmation": "请求确认计划",
    "advance_workflow": "推进工作流",
    "complete_session": "完成当前检索",
    "save_search_elements": "保存检索要素",
    "save_plan_review_markdown": "保存计划正文",
    "save_plan_execution_overview": "保存计划总览",
    "append_plan_sub_plan": "追加子计划",
    "finalize_plan_draft": "完成计划草案",
    "probe_search_semantic": "执行语义预检",
    "probe_search_boolean": "执行布尔预检",
    "probe_count_boolean": "统计布尔命中数",
    "run_execution_step": "执行检索步骤",
    "run_coarse_screen_batch": "执行候选粗筛",
    "run_close_read_batch": "执行重点精读",
    "run_feature_compare": "执行特征对比",
    "write_stage_log": "更新阶段日志",
}

READ_ONLY_FILESYSTEM_TOOLS = {"ls", "read_file", "glob", "grep"}
WRITE_FILESYSTEM_TOOLS = {"write_file", "edit_file"}
EXECUTION_TOOLS = {"execute"}

ROLE_TOOL_POLICIES: dict[str, dict[str, set[str]]] = {
    "main-agent": {
        "blocked_tools": READ_ONLY_FILESYSTEM_TOOLS | WRITE_FILESYSTEM_TOOLS | EXECUTION_TOOLS,
        "allowed_subagents": set(ALL_AI_SEARCH_SUBAGENTS),
    },
    "search-elements": {
        "blocked_tools": READ_ONLY_FILESYSTEM_TOOLS | WRITE_FILESYSTEM_TOOLS | EXECUTION_TOOLS | {"task"},
        "allowed_subagents": set(),
    },
    "query-executor": {
        "blocked_tools": READ_ONLY_FILESYSTEM_TOOLS | WRITE_FILESYSTEM_TOOLS | EXECUTION_TOOLS | {"task"},
        "allowed_subagents": set(),
    },
    "planner": {
        "blocked_tools": READ_ONLY_FILESYSTEM_TOOLS | WRITE_FILESYSTEM_TOOLS | EXECUTION_TOOLS | {"task"},
        "allowed_subagents": set(),
    },
    "plan-prober": {
        "blocked_tools": READ_ONLY_FILESYSTEM_TOOLS | WRITE_FILESYSTEM_TOOLS | EXECUTION_TOOLS | {"task"},
        "allowed_subagents": set(),
    },
    "coarse-screener": {
        "blocked_tools": READ_ONLY_FILESYSTEM_TOOLS | WRITE_FILESYSTEM_TOOLS | EXECUTION_TOOLS | {"task"},
        "allowed_subagents": set(),
    },
    "close-reader": {
        "blocked_tools": WRITE_FILESYSTEM_TOOLS | EXECUTION_TOOLS | {"task"},
        "allowed_subagents": set(),
    },
    "feature-comparer": {
        "blocked_tools": READ_ONLY_FILESYSTEM_TOOLS | WRITE_FILESYSTEM_TOOLS | EXECUTION_TOOLS | {"task"},
        "allowed_subagents": set(),
    },
}


class AiSearchGuardMiddleware(AgentMiddleware):
    def __init__(
        self,
        role: str,
        *,
        storage: Any = None,
        task_id: str = "",
        blocked_tools: Optional[Sequence[str]] = None,
        allowed_subagents: Optional[Sequence[str]] = None,
    ) -> None:
        self.role = str(role or "main-agent").strip() or "main-agent"
        self.storage = storage
        self.task_id = str(task_id or "").strip()
        defaults = ROLE_TOOL_POLICIES.get(self.role, ROLE_TOOL_POLICIES["main-agent"])
        self.blocked_tools = set(blocked_tools or defaults["blocked_tools"])
        self.allowed_subagents = set(allowed_subagents or defaults["allowed_subagents"])

    def _current_task_state(self) -> str:
        if self.storage is None or not self.task_id:
            return ""
        task = self.storage.get_task(self.task_id)
        meta = get_ai_search_meta(task)
        return str(meta.get("current_phase") or "").strip()

    def _guard_tool_call(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_name = str(request.tool_call.get("name") or "").strip()
        phase = self._current_task_state()
        if tool_name in self.blocked_tools:
            return ToolMessage(
                content=f"工具 `{tool_name}` 对 `{self.role}` 不可用。",
                name=tool_name or "blocked_tool",
                tool_call_id=request.tool_call["id"],
            )
        if phase:
            if self.role == "main-agent":
                allowed_tools = allowed_main_agent_tools(phase)
                if tool_name != "task" and tool_name not in allowed_tools:
                    return ToolMessage(
                        content=f"工具 `{tool_name}` 不能在阶段 `{phase}` 由 `{self.role}` 调用。",
                        name=tool_name or "phase_blocked_tool",
                        tool_call_id=request.tool_call["id"],
                    )
            else:
                allowed_tools = allowed_role_tools(self.role, phase)
                if allowed_tools is not None and tool_name not in allowed_tools:
                    return ToolMessage(
                        content=f"工具 `{tool_name}` 不能在阶段 `{phase}` 由 `{self.role}` 调用。",
                        name=tool_name or "phase_blocked_tool",
                        tool_call_id=request.tool_call["id"],
                    )
        if tool_name == "task":
            subagent_type = str((request.tool_call.get("args") or {}).get("subagent_type") or "").strip()
            if subagent_type not in self.allowed_subagents:
                return ToolMessage(
                    content=f"子 agent `{subagent_type or 'unknown'}` 不允许由 `{self.role}` 调用。",
                    name="task",
                    tool_call_id=request.tool_call["id"],
                )
            if phase and self.role == "main-agent":
                allowed_subagents = allowed_main_agent_subagents(phase)
                if subagent_type not in allowed_subagents:
                    return ToolMessage(
                        content=f"子 agent `{subagent_type or 'unknown'}` 不能在阶段 `{phase}` 由 `{self.role}` 调用。",
                        name="task",
                        tool_call_id=request.tool_call["id"],
                    )
        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ) -> ToolMessage | Command[Any]:
        blocked = self._guard_tool_call(request)
        if blocked is not None:
            return blocked
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ) -> ToolMessage | Command[Any]:
        blocked = self._guard_tool_call(request)
        if blocked is not None:
            return blocked
        return await handler(request)


def build_guard_middleware(role: str, storage: Any = None, task_id: str = "") -> AiSearchGuardMiddleware:
    return AiSearchGuardMiddleware(role, storage=storage, task_id=task_id)


def format_subagent_label(name: str) -> str:
    return SUBAGENT_DISPLAY_LABELS.get(str(name or "").strip(), str(name or "").strip() or "子 agent")


def format_role_label(role: str) -> str:
    return ROLE_DISPLAY_LABELS.get(str(role or "").strip(), str(role or "").strip() or "代理")


def format_tool_label(name: str) -> str:
    return TOOL_DISPLAY_LABELS.get(str(name or "").strip(), str(name or "").strip() or "工具")


_PROCESS_EVENT_SUFFIX_RE = re.compile(r":(?:running|completed|failed|started)$")


def normalize_process_dedupe_key(event_id: str, fallback: str) -> str:
    normalized_event_id = str(event_id or "").strip()
    if normalized_event_id:
        dedupe_key = _PROCESS_EVENT_SUFFIX_RE.sub("", normalized_event_id)
        if dedupe_key:
            return dedupe_key
    return str(fallback or "").strip()


def build_process_display_metadata(
    *,
    process_type: str,
    event_id: str = "",
    subagent_name: str = "",
    tool_name: str = "",
    label: str = "",
    summary: str = "",
) -> Dict[str, Any]:
    normalized_process_type = str(process_type or "").strip()
    normalized_group_key = str(subagent_name or "").strip() or None
    normalized_label = str(label or "").strip()
    normalized_summary = str(summary or "").strip()
    normalized_tool_name = str(tool_name or "").strip()
    if normalized_process_type == "subagent":
        fallback = f"subagent:{normalized_group_key or normalized_label or 'unknown'}"
        return {
            "displayKind": "group_status",
            "displayGroupKey": normalized_group_key,
            "dedupeKey": normalize_process_dedupe_key(event_id, fallback),
        }
    fallback = f"tool:{normalized_group_key or 'root'}:{normalized_tool_name or normalized_summary or normalized_label or 'unknown'}"
    return {
        "displayKind": "detail",
        "displayGroupKey": normalized_group_key,
        "dedupeKey": normalize_process_dedupe_key(event_id, fallback),
    }


def _tool_summary(tool_name: str, args: Dict[str, Any]) -> str:
    name = str(tool_name or "").strip()
    if name == "advance_workflow":
        action = str(args.get("action") or "").strip()
        return f"推进工作流{f'：{action}' if action else ''}"
    if name == "run_execution_step":
        operation = str(args.get("operation") or "load").strip().lower()
        return "提交执行步骤摘要" if operation == "commit" else "加载当前执行步骤"
    if name == "run_coarse_screen_batch":
        operation = str(args.get("operation") or "load").strip().lower()
        return "提交粗筛结果" if operation == "commit" else "加载粗筛批次"
    if name == "run_close_read_batch":
        operation = str(args.get("operation") or "load").strip().lower()
        return "提交精读结果" if operation == "commit" else "加载精读批次"
    if name == "run_feature_compare":
        operation = str(args.get("operation") or "load").strip().lower()
        return "提交特征对比结果" if operation == "commit" else "加载特征对比上下文"
    if name == "save_plan_review_markdown":
        return "保存计划正文"
    if name == "save_plan_execution_overview":
        return "保存计划总览"
    if name == "append_plan_sub_plan":
        return "追加子计划"
    if name == "finalize_plan_draft":
        return "完成计划草案"
    return format_tool_label(name)


def build_tool_event_payload(
    *,
    role: str,
    tool_name: str,
    tool_call_id: str,
    args: Optional[Dict[str, Any]] = None,
    status: str,
    error_message: str = "",
) -> Dict[str, Any]:
    normalized_role = str(role or "").strip() or "main-agent"
    normalized_tool_name = str(tool_name or "").strip()
    normalized_args = args if isinstance(args, dict) else {}
    summary = _tool_summary(normalized_tool_name, normalized_args)
    subagent_name = normalized_role if normalized_role != "main-agent" else None
    subagent_label = format_role_label(normalized_role) if subagent_name else None
    status_text = summary
    if status == "completed":
        status_text = f"{summary}已完成"
    elif status == "failed":
        status_text = f"{summary}失败"
    display_metadata = build_process_display_metadata(
        process_type="tool",
        event_id=f"{tool_call_id or normalized_tool_name}:{status}",
        subagent_name=subagent_name or "",
        tool_name=normalized_tool_name,
        label=format_tool_label(normalized_tool_name),
        summary=summary,
    )
    return {
        "eventId": f"{tool_call_id or normalized_tool_name}:{status}",
        "processType": "tool",
        "status": status,
        "toolName": normalized_tool_name,
        "toolLabel": format_tool_label(normalized_tool_name),
        "summary": summary,
        "statusText": status_text,
        "subagentName": subagent_name,
        "subagentLabel": subagent_label,
        "errorMessage": str(error_message or "").strip() or None,
        **display_metadata,
    }


def write_stream_event(writer: Any, payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    if hasattr(writer, "write") and callable(writer.write):
        writer.write(payload)
        return
    if callable(writer):
        writer(payload)


class AiSearchStreamingMiddleware(AgentMiddleware):
    def __init__(self, role: str, *, context: Any | None = None) -> None:
        self.role = str(role or "").strip()
        self.context = context

    def before_agent(self, state: Any, runtime: Any) -> None:
        if not self.role or self.role == "main-agent":
            return None
        label = format_subagent_label(self.role)
        write_stream_event(
            getattr(runtime, "stream_writer", None),
            {
                "type": "subagent.started",
                "payload": {
                    "eventId": f"{self.role}:started",
                    "processType": "subagent",
                    "status": "running",
                    "name": self.role,
                    "label": label,
                    "summary": label,
                    "statusText": f"{label}开始执行",
                    "subagentName": self.role,
                    "subagentLabel": label,
                    **build_process_display_metadata(
                        process_type="subagent",
                        event_id=f"{self.role}:started",
                        subagent_name=self.role,
                        label=label,
                        summary=label,
                    ),
                },
            },
        )
        if self.context is not None:
            self.context.emit_startup_stage_log(stage_kind=self.role, runtime=runtime)
            self.context.emit_runtime_stage_checkpoint(
                stage_kind=self.role,
                checkpoint="entered_execution",
                runtime=runtime,
            )
        return None

    def after_agent(self, state: Any, runtime: Any) -> None:
        if not self.role or self.role == "main-agent":
            return None
        label = format_subagent_label(self.role)
        write_stream_event(
            getattr(runtime, "stream_writer", None),
            {
                "type": "subagent.completed",
                "payload": {
                    "eventId": f"{self.role}:completed",
                    "processType": "subagent",
                    "status": "completed",
                    "name": self.role,
                    "label": label,
                    "summary": label,
                    "statusText": f"{label}已完成",
                    "subagentName": self.role,
                    "subagentLabel": label,
                    **build_process_display_metadata(
                        process_type="subagent",
                        event_id=f"{self.role}:completed",
                        subagent_name=self.role,
                        label=label,
                        summary=label,
                    ),
                },
            },
        )
        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ) -> ToolMessage | Command[Any]:
        tool_name = str(request.tool_call.get("name") or "").strip()
        if tool_name == "task":
            return handler(request)
        tool_call_id = str(request.tool_call.get("id") or tool_name or "tool").strip()
        args = request.tool_call.get("args") if isinstance(request.tool_call.get("args"), dict) else {}
        write_stream_event(
            getattr(request.runtime, "stream_writer", None),
            {
                "type": "tool.started",
                "payload": build_tool_event_payload(
                    role=self.role,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    args=args,
                    status="running",
                ),
            },
        )
        try:
            result = handler(request)
        except Exception as exc:
            write_stream_event(
                getattr(request.runtime, "stream_writer", None),
                {
                    "type": "tool.failed",
                    "payload": build_tool_event_payload(
                        role=self.role,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        args=args,
                        status="failed",
                        error_message=str(exc),
                    ),
                },
            )
            raise
        write_stream_event(
            getattr(request.runtime, "stream_writer", None),
            {
                "type": "tool.completed",
                "payload": build_tool_event_payload(
                    role=self.role,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    args=args,
                    status="completed",
                ),
            },
        )
        if self.context is not None:
            self.context.emit_runtime_stage_tool_progress(
                stage_kind=self.role,
                tool_name=tool_name,
                tool_result=result,
                runtime=request.runtime,
            )
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ) -> ToolMessage | Command[Any]:
        tool_name = str(request.tool_call.get("name") or "").strip()
        if tool_name == "task":
            return await handler(request)
        tool_call_id = str(request.tool_call.get("id") or tool_name or "tool").strip()
        args = request.tool_call.get("args") if isinstance(request.tool_call.get("args"), dict) else {}
        write_stream_event(
            getattr(request.runtime, "stream_writer", None),
            {
                "type": "tool.started",
                "payload": build_tool_event_payload(
                    role=self.role,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    args=args,
                    status="running",
                ),
            },
        )
        try:
            result = await handler(request)
        except Exception as exc:
            write_stream_event(
                getattr(request.runtime, "stream_writer", None),
                {
                    "type": "tool.failed",
                    "payload": build_tool_event_payload(
                        role=self.role,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        args=args,
                        status="failed",
                        error_message=str(exc),
                    ),
                },
            )
            raise
        write_stream_event(
            getattr(request.runtime, "stream_writer", None),
            {
                "type": "tool.completed",
                "payload": build_tool_event_payload(
                    role=self.role,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    args=args,
                    status="completed",
                ),
            },
        )
        if self.context is not None:
            self.context.emit_runtime_stage_tool_progress(
                stage_kind=self.role,
                tool_name=tool_name,
                tool_result=result,
                runtime=request.runtime,
            )
        return result


def build_streaming_middleware(role: str, *, context: Any | None = None) -> AiSearchStreamingMiddleware:
    return AiSearchStreamingMiddleware(role, context=context)


def build_chat_model(model_name: Optional[str]) -> ChatOpenAI:
    resolved_model = str(model_name or "").strip()
    if not resolved_model:
        raise ValueError("AI 检索未配置 LLM 模型。")
    if not settings.LLM_API_KEY:
        raise ValueError("AI 检索缺少必需的 LLM_API_KEY。")
    return ChatOpenAI(
        model=resolved_model,
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        temperature=0,
        timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
    )


def default_model() -> ChatOpenAI:
    return build_chat_model(settings.LLM_MODEL_DEFAULT)


def large_model() -> ChatOpenAI:
    return build_chat_model(settings.LLM_MODEL_LARGE or settings.LLM_MODEL_DEFAULT)


def extract_json_object(text: str) -> Dict[str, Any]:
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
    return extract_json_object(content)
