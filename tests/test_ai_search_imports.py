from __future__ import annotations

import importlib


def test_public_ai_search_imports_are_stable():
    pkg = importlib.import_module("agents.ai_search")
    main = importlib.import_module("agents.ai_search.main")

    assert hasattr(pkg, "build_main_agent")
    assert hasattr(pkg, "build_planner_agent")
    assert hasattr(pkg, "build_query_executor_agent")
    assert hasattr(pkg, "build_plan_prober_agent")
    assert hasattr(main, "build_main_agent")
    assert hasattr(main, "build_planner_agent")
    assert hasattr(main, "extract_structured_response")


def test_reorganized_ai_search_packages_import_without_cycles():
    module_names = [
        "agents.ai_search.src.main_agent",
        "agents.ai_search.src.subagents.search_elements",
        "agents.ai_search.src.subagents.planner",
        "agents.ai_search.src.subagents.plan_prober",
        "agents.ai_search.src.subagents.query_executor",
        "agents.ai_search.src.subagents.coarse_screener",
        "agents.ai_search.src.subagents.close_reader",
        "agents.ai_search.src.subagents.feature_comparer",
    ]

    for name in module_names:
        importlib.import_module(name)
