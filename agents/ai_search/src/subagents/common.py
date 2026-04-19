"""Shared helpers for AI Search subagent specs."""

from __future__ import annotations

from typing import Any, Callable, Optional


def build_structured_subagent_spec(
    *,
    name: str,
    description: str,
    model: Any,
    system_prompt: str,
    response_format: Any,
    persist_result: Callable[..., None],
    tools: Optional[list[Any]] = None,
    middleware: Optional[list[Any]] = None,
    context_schema: Any = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "model": model,
        "system_prompt": system_prompt,
        "response_format": response_format,
        "persist_result": persist_result,
        "tools": list(tools or []),
        "middleware": list(middleware or []),
        "context_schema": context_schema,
    }
