"""Runtime context helpers for AI Search deepagents integration."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from langchain.agents.structured_output import ProviderStrategy
from langchain.tools import ToolRuntime
from langchain_core.runnables import Runnable
from langgraph.types import Command

from agents.ai_search.src.runtime import (
    structured_output_system_prompt,
    uses_dashscope_openai_compatible_api,
)

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


def ensure_deepagents_context_support() -> None:
    import deepagents.middleware.subagents as subagents_module
    from deepagents._models import resolve_model
    from langchain.agents import create_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    from langchain.agents.middleware.types import AgentMiddleware
    from langchain_core.messages import HumanMessage, ToolMessage
    from langchain_core.tools import StructuredTool

    if getattr(subagents_module, "_ai_search_context_support_patch", False):
        return

    def _persist_subagent_result(spec: dict[str, Any], result: dict[str, Any], runtime: ToolRuntime) -> None:
        persist_result = spec.get("persist_result")
        if not callable(persist_result):
            return
        structured = result.get("structured_response")
        if structured is None:
            return
        resolved_context = resolve_agent_context(runtime)
        persist_result(resolved_context, structured, runtime=runtime.context)

    def _build_task_tool(subagents: list[dict[str, Any]], task_description: str | None = None) -> Any:
        subagent_specs: dict[str, dict[str, Any]] = {
            str(spec["name"]): spec for spec in subagents if str(spec.get("name") or "").strip()
        }
        subagent_graphs: dict[str, Runnable] = {
            name: cast(Runnable, spec["runnable"]) for name, spec in subagent_specs.items()
        }
        subagent_description_str = "\n".join(f"- {spec['name']}: {spec['description']}" for spec in subagents)
        if task_description is None:
            description = subagents_module.TASK_TOOL_DESCRIPTION.format(available_agents=subagent_description_str)
        elif "{available_agents}" in task_description:
            description = task_description.format(available_agents=subagent_description_str)
        else:
            description = task_description

        def _return_command_with_state_update(result: dict[str, Any], tool_call_id: str) -> Any:
            if "messages" not in result:
                raise ValueError(
                    "CompiledSubAgent must return a state containing a 'messages' key. "
                    "Custom StateGraphs used with CompiledSubAgent should include 'messages' "
                    "in their state schema to communicate results back to the main agent."
                )
            state_update = {
                key: value
                for key, value in result.items()
                if key not in subagents_module._EXCLUDED_STATE_KEYS
            }
            structured = result.get("structured_response")
            if structured is not None:
                if hasattr(structured, "model_dump_json"):
                    content: str = structured.model_dump_json()
                elif dataclasses.is_dataclass(structured) and not isinstance(structured, type):
                    content = json.dumps(dataclasses.asdict(structured))
                else:
                    content = json.dumps(structured)
            else:
                content = result["messages"][-1].text.rstrip() if result["messages"][-1].text else ""
            return Command(
                update={
                    **state_update,
                    "messages": [ToolMessage(content, tool_call_id=tool_call_id)],
                }
            )

        def _validate_and_prepare_state(
            subagent_type: str,
            description: str,
            runtime: ToolRuntime,
        ) -> tuple[Runnable, dict[str, Any]]:
            subagent = subagent_graphs[subagent_type]
            subagent_state = {
                key: value
                for key, value in runtime.state.items()
                if key not in subagents_module._EXCLUDED_STATE_KEYS
            }
            subagent_state["messages"] = [HumanMessage(content=description)]
            return subagent, subagent_state

        def task(
            description: str,
            subagent_type: str,
            runtime: ToolRuntime,
        ) -> str | Command:
            if subagent_type not in subagent_graphs:
                allowed_types = ", ".join(f"`{name}`" for name in subagent_graphs)
                return (
                    f"We cannot invoke subagent {subagent_type} because it does not exist, "
                    f"the only allowed types are {allowed_types}"
                )
            if not runtime.tool_call_id:
                raise ValueError("Tool call ID is required for subagent invocation")
            subagent, subagent_state = _validate_and_prepare_state(subagent_type, description, runtime)
            result = subagent.invoke(subagent_state, context=runtime.context)
            _persist_subagent_result(subagent_specs[subagent_type], result, runtime)
            return _return_command_with_state_update(result, runtime.tool_call_id)

        async def atask(
            description: str,
            subagent_type: str,
            runtime: ToolRuntime,
        ) -> str | Command:
            if subagent_type not in subagent_graphs:
                allowed_types = ", ".join(f"`{name}`" for name in subagent_graphs)
                return (
                    f"We cannot invoke subagent {subagent_type} because it does not exist, "
                    f"the only allowed types are {allowed_types}"
                )
            if not runtime.tool_call_id:
                raise ValueError("Tool call ID is required for subagent invocation")
            subagent, subagent_state = _validate_and_prepare_state(subagent_type, description, runtime)
            result = await subagent.ainvoke(subagent_state, context=runtime.context)
            _persist_subagent_result(subagent_specs[subagent_type], result, runtime)
            return _return_command_with_state_update(result, runtime.tool_call_id)

        return StructuredTool.from_function(
            name="task",
            func=task,
            coroutine=atask,
            description=description,
            infer_schema=False,
            args_schema=subagents_module.TaskToolSchema,
        )

    def _get_subagents(self: Any) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for spec in self._subagents:
            if "runnable" in spec:
                compiled = cast("dict[str, Any]", spec)
                specs.append(
                    {
                        "name": compiled["name"],
                        "description": compiled["description"],
                        "runnable": compiled["runnable"],
                    }
                )
                continue
            if "model" not in spec:
                raise ValueError(f"SubAgent '{spec['name']}' must specify 'model'")
            if "tools" not in spec:
                raise ValueError(f"SubAgent '{spec['name']}' must specify 'tools'")
            model = resolve_model(spec["model"])
            middleware: list[AgentMiddleware] = list(spec.get("middleware", []))
            interrupt_on = spec.get("interrupt_on")
            if interrupt_on:
                middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))
            response_format = spec.get("response_format")
            system_prompt = str(spec.get("system_prompt") or "")
            if uses_dashscope_openai_compatible_api(model) and response_format is not None:
                response_format = ProviderStrategy(schema=response_format)
                system_prompt = structured_output_system_prompt(system_prompt)
            specs.append(
                {
                    "name": spec["name"],
                    "description": spec["description"],
                    "persist_result": spec.get("persist_result"),
                    "runnable": create_agent(
                        model,
                        system_prompt=system_prompt,
                        tools=spec["tools"],
                        middleware=middleware,
                        name=spec["name"],
                        response_format=response_format,
                        context_schema=spec.get("context_schema"),
                    ),
                }
            )
        return specs

    subagents_module._build_task_tool = _build_task_tool
    subagents_module.SubAgentMiddleware._get_subagents = _get_subagents
    subagents_module._ai_search_context_support_patch = True


ensure_deepagents_context_support()
