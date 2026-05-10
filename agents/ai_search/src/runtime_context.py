"""Runtime context helpers for AI Search agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.ai_search.src.context import AiSearchAgentContext


@dataclass(frozen=True)
class AiSearchRuntimeContext:
    storage: Any
    task_id: str


def build_runtime_context(storage: Any, task_id: str) -> AiSearchRuntimeContext:
    return AiSearchRuntimeContext(storage=storage, task_id=str(task_id or "").strip())


def resolve_agent_context(
    runtime: Any | None = None,
    *,
    fallback: "AiSearchAgentContext | None" = None,
    storage: Any | None = None,
    task_id: str = "",
) -> "AiSearchAgentContext":
    from agents.ai_search.src.context import AiSearchAgentContext

    runtime_context = getattr(runtime, "context", None) if runtime is not None else None
    if runtime_context is not None:
        runtime_storage = getattr(runtime_context, "storage", None)
        runtime_task_id = str(getattr(runtime_context, "task_id", "") or "").strip()
        if runtime_storage is not None and runtime_task_id:
            return AiSearchAgentContext(runtime_storage, runtime_task_id)
    if fallback is not None:
        return fallback
    if storage is not None and str(task_id or "").strip():
        return AiSearchAgentContext(storage, str(task_id or "").strip())
    raise ValueError("AI Search runtime context is required")
