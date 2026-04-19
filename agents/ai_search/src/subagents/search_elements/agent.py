"""检索要素子代理定义。"""

from __future__ import annotations

from agents.ai_search.src.subagents.common import build_structured_subagent_spec
from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.runtime import build_guard_middleware, large_model
from agents.ai_search.src.subagents.search_elements.prompt import SEARCH_ELEMENTS_SYSTEM_PROMPT
from agents.ai_search.src.subagents.search_elements.tools import build_search_elements_tools
from agents.ai_search.src.subagents.search_elements.schemas import SearchElementsOutput


def _persist_search_elements(context: object, output: SearchElementsOutput) -> None:
    context.save_search_elements_payload(output.model_dump(mode="python"))


def build_search_elements_subagent() -> dict:
    return build_structured_subagent_spec(
        name="search-elements",
        description="根据用户需求整理结构化检索要素，并提取申请人和日期边界。",
        model=large_model(),
        system_prompt=SEARCH_ELEMENTS_SYSTEM_PROMPT,
        response_format=SearchElementsOutput,
        persist_result=_persist_search_elements,
        tools=build_search_elements_tools(),
        middleware=[build_guard_middleware()],
        context_schema=AiSearchRuntimeContext,
    )
