"""
AI 检索 Agent 共享运行时工具。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.types import Command
from pydantic import BaseModel

from config import settings


ALLOWED_SUBAGENTS = {
    "search-elements",
    "query-executor",
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


class AiSearchGuardMiddleware(AgentMiddleware):
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ) -> ToolMessage | Command[Any]:
        tool_name = str(request.tool_call.get("name") or "").strip()
        if tool_name in BLOCKED_TOOLS:
            return ToolMessage(
                content=f"工具 `{tool_name}` 在 AI 检索中不可用。",
                name=tool_name or "blocked_tool",
                tool_call_id=request.tool_call["id"],
            )
        if tool_name == "task":
            subagent_type = str((request.tool_call.get("args") or {}).get("subagent_type") or "").strip()
            if subagent_type not in ALLOWED_SUBAGENTS:
                return ToolMessage(
                    content=f"子 agent `{subagent_type or 'unknown'}` 不允许在 AI 检索中使用。",
                    name="task",
                    tool_call_id=request.tool_call["id"],
                )
        return handler(request)


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
