from __future__ import annotations

import importlib


def test_ai_search_concrete_modules_import_without_cycles():
    module_names = [
        "agents.ai_search.src.main_agent.agent",
        "agents.ai_search.src.main_agent.planning_tools",
        "agents.ai_search.src.search_elements",
        "agents.ai_search.src.subagents.query_executor.agent",
        "agents.ai_search.src.subagents.query_executor.search_backend_tools",
        "agents.ai_search.src.subagents.coarse_screener.agent",
        "agents.ai_search.src.subagents.close_reader.agent",
        "agents.ai_search.src.subagents.close_reader.workspace",
        "agents.ai_search.src.subagents.feature_comparer.agent",
    ]

    for name in module_names:
        importlib.import_module(name)
