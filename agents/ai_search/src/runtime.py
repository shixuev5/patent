"""智能检索代理的共享运行时工具。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Sequence

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.types import Command

from agents.ai_search.src.state import (
    allowed_main_agent_subagents,
    allowed_main_agent_tools,
    allowed_role_tools,
    get_ai_search_meta,
)
from config import settings


ALL_AI_SEARCH_SUBAGENTS = {
    "query-executor",
    "coarse-screener",
    "close-reader",
    "feature-comparer",
}

READ_ONLY_FILESYSTEM_TOOLS = {"ls", "read_file", "glob", "grep"}
WRITE_FILESYSTEM_TOOLS = {"write_file", "edit_file"}
EXECUTION_TOOLS = {"execute"}

ROLE_TOOL_POLICIES: dict[str, dict[str, set[str]]] = {
    "main-agent": {
        "blocked_tools": READ_ONLY_FILESYSTEM_TOOLS | WRITE_FILESYSTEM_TOOLS | EXECUTION_TOOLS,
        "allowed_subagents": set(ALL_AI_SEARCH_SUBAGENTS),
    },
    "query-executor": {
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
    ) -> None:
        super().__init__()

    def _runtime_metadata(self, runtime: Any | None) -> Dict[str, Any]:
        config = getattr(runtime, "config", None) if runtime is not None else None
        if isinstance(config, dict):
            metadata = config.get("metadata")
            if isinstance(metadata, dict):
                return metadata
        return {}

    def _resolved_role(self, runtime: Any | None) -> str:
        metadata = self._runtime_metadata(runtime)
        return normalize_ai_search_role(metadata.get("lc_agent_name")) or "main-agent"

    def _role_policy(self, role: str) -> dict[str, set[str]]:
        return ROLE_TOOL_POLICIES.get(role, ROLE_TOOL_POLICIES["main-agent"])

    def _runtime_storage_and_task_id(self, runtime: Any | None) -> tuple[Any | None, str]:
        runtime_context = getattr(runtime, "context", None) if runtime is not None else None
        runtime_storage = getattr(runtime_context, "storage", None)
        runtime_task_id = str(getattr(runtime_context, "task_id", "") or "").strip()
        return runtime_storage, runtime_task_id

    def _current_task_state(self, runtime: Any | None = None) -> str:
        storage, task_id = self._runtime_storage_and_task_id(runtime)
        if storage is None or not task_id:
            return ""
        task = storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        return str(meta.get("current_phase") or "").strip()

    def _guard_subagent_call(self, request: ToolCallRequest, subagent_type: str, role: str) -> ToolMessage | None:
        normalized_subagent = str(subagent_type or "").strip()
        phase = self._current_task_state(request.runtime)
        allowed_subagents = self._role_policy(role)["allowed_subagents"]
        if normalized_subagent not in allowed_subagents:
            return ToolMessage(
                content=f"子 agent `{normalized_subagent or 'unknown'}` 不允许由 `{role}` 调用。",
                name=str(request.tool_call.get("name") or "task") or "task",
                tool_call_id=request.tool_call["id"],
            )
        if phase and role == "main-agent":
            allowed_subagents = allowed_main_agent_subagents(phase)
            if normalized_subagent not in allowed_subagents:
                return ToolMessage(
                    content=f"子 agent `{normalized_subagent or 'unknown'}` 不能在阶段 `{phase}` 由 `{role}` 调用。",
                    name=str(request.tool_call.get("name") or "task") or "task",
                    tool_call_id=request.tool_call["id"],
                )
        return None

    def _guard_tool_call(self, request: ToolCallRequest) -> ToolMessage | None:
        role = self._resolved_role(request.runtime)
        policy = self._role_policy(role)
        tool_name = str(request.tool_call.get("name") or "").strip()
        phase = self._current_task_state(request.runtime)
        if tool_name in {"task", "run_search_specialist"}:
            args = request.tool_call.get("args") or {}
            subagent_type = str(args.get("specialist_type") or args.get("subagent_type") or "").strip()
            return self._guard_subagent_call(request, subagent_type, role)
        if tool_name in ALL_AI_SEARCH_SUBAGENTS:
            return self._guard_subagent_call(request, tool_name, role)
        if tool_name in policy["blocked_tools"]:
            return ToolMessage(
                content=f"工具 `{tool_name}` 对 `{role}` 不可用。",
                name=tool_name or "blocked_tool",
                tool_call_id=request.tool_call["id"],
            )
        if phase:
            if role == "main-agent":
                allowed_tools = allowed_main_agent_tools(phase)
                if tool_name not in allowed_tools:
                    return ToolMessage(
                        content=f"工具 `{tool_name}` 不能在阶段 `{phase}` 由 `{role}` 调用。",
                        name=tool_name or "phase_blocked_tool",
                        tool_call_id=request.tool_call["id"],
                    )
            else:
                allowed_tools = allowed_role_tools(role, phase)
                if allowed_tools is not None and tool_name not in allowed_tools:
                    return ToolMessage(
                        content=f"工具 `{tool_name}` 不能在阶段 `{phase}` 由 `{role}` 调用。",
                        name=tool_name or "phase_blocked_tool",
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


def build_guard_middleware() -> AiSearchGuardMiddleware:
    return AiSearchGuardMiddleware()


def normalize_ai_search_role(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if raw in {"main-agent", "main_agent"} or raw.startswith("ai-search-main-agent-"):
        return "main-agent"
    if raw in ALL_AI_SEARCH_SUBAGENTS:
        return raw
    for candidate in ALL_AI_SEARCH_SUBAGENTS:
        if raw.startswith(f"ai-search-{candidate}-"):
            return candidate
    return ""


def write_stream_event(writer: Any, payload: Dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    if hasattr(writer, "write") and callable(writer.write):
        writer.write(payload)
        return
    if callable(writer):
        writer(payload)


class AiSearchChatOpenAI(ChatOpenAI):
    """Provider-compatibility wrapper for AI Search agents."""

    def bind_tools(
        self,
        tools,
        *,
        tool_choice=None,
        strict=None,
        parallel_tool_calls=None,
        response_format=None,
        **kwargs: Any,
    ):
        return super().bind_tools(
            tools,
            tool_choice=tool_choice,
            strict=strict,
            parallel_tool_calls=parallel_tool_calls,
            response_format=response_format,
            **kwargs,
        )


def build_chat_model(model_name: Optional[str]) -> ChatOpenAI:
    resolved_model = str(model_name or "").strip()
    if not resolved_model:
        raise ValueError("AI 检索未配置 LLM 模型。")
    if not settings.LLM_API_KEY:
        raise ValueError("AI 检索缺少必需的 LLM_API_KEY。")
    return AiSearchChatOpenAI(
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
