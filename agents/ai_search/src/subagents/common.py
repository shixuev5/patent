"""Shared helpers for structured-output AI Search subagent wrappers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Generic, Optional, TypeVar

from langchain.agents import create_agent

from agents.ai_search.src.runtime_context import resolve_agent_context

T = TypeVar("T")


@dataclass
class StructuredPersistingSubagent(Generic[T]):
    """Wrap a structured-output agent and persist results deterministically."""

    name: str
    model: Any
    system_prompt: str
    response_format: Any
    persist_result: Callable[..., None]
    tools: Optional[list[Any]] = None
    middleware: Optional[list[Any]] = None
    context_schema: Any = None

    def __post_init__(self) -> None:
        self._agent = create_agent(
            self.model,
            system_prompt=self.system_prompt,
            tools=list(self.tools or []),
            middleware=list(self.middleware or []),
            name=self.name,
            response_format=self.response_format,
            context_schema=self.context_schema,
        )

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> dict[str, Any]:
        result = self._agent.invoke(input, config=config, **kwargs)
        runtime_context = kwargs.get("context")
        resolved = resolve_agent_context(
            storage=getattr(runtime_context, "storage", None),
            task_id=str(getattr(runtime_context, "task_id", "") or ""),
        )
        structured = result.get("structured_response")
        if structured is not None:
            self.persist_result(resolved, structured, runtime=runtime_context)
        return result

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> dict[str, Any]:
        result = await self._agent.ainvoke(input, config=config, **kwargs)
        runtime_context = kwargs.get("context")
        resolved = resolve_agent_context(
            storage=getattr(runtime_context, "storage", None),
            task_id=str(getattr(runtime_context, "task_id", "") or ""),
        )
        structured = result.get("structured_response")
        if structured is not None:
            self.persist_result(resolved, structured, runtime=runtime_context)
        return result
