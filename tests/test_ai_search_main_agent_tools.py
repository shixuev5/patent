from __future__ import annotations

from agents.ai_search.src import main_agent as main_agent_module
from agents.ai_search.src.main_agent import agent as main_agent_agent_module
from agents.ai_search.src.subagents import close_reader as close_reader_module
from agents.ai_search.src.subagents import coarse_screener as coarse_screener_module
from agents.ai_search.src.subagents import feature_comparer as feature_comparer_module
from agents.ai_search.src.subagents import query_executor as query_executor_module
from agents.ai_search.src.subagents import search_elements as search_elements_module


def test_build_main_agent_exposes_orchestration_tools_only(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(main_agent_agent_module, "create_deep_agent", _fake_create_deep_agent)
    monkeypatch.setattr(main_agent_agent_module, "large_model", lambda: object())

    main_agent_module.build_main_agent(object(), "task-ai-search")

    tools = captured.get("tools")
    assert isinstance(tools, list)
    assert tools
    assert all(callable(tool) for tool in tools)
    assert all(str(getattr(tool, "__doc__", "") or "").strip() for tool in tools)

    tool_names = {str(getattr(tool, "__name__", "")) for tool in tools}
    assert tool_names == {
        "read_todos",
        "write_todos",
        "get_search_elements",
        "get_gap_context",
        "evaluate_gap_progress",
        "start_plan_drafting",
        "save_search_plan",
        "ask_user_question",
        "request_plan_confirmation",
        "begin_execution",
        "start_execution_step",
        "complete_execution_step",
        "pause_execution_for_replan",
        "start_coarse_screen",
        "start_close_read",
        "start_feature_table_generation",
        "get_execution_state",
        "list_documents",
        "complete_execution",
    }


def test_specialists_own_domain_tools():
    storage = object()
    task_id = "task-ai-search"

    search_elements_tools = {
        str(getattr(tool, "__name__", ""))
        for tool in search_elements_module.build_search_elements_subagent(storage, task_id)["tools"]
    }
    query_tools = {
        str(getattr(tool, "__name__", ""))
        for tool in query_executor_module.build_query_executor_subagent(storage, task_id)["tools"]
    }
    coarse_tools = {
        str(getattr(tool, "__name__", ""))
        for tool in coarse_screener_module.build_coarse_screener_subagent(storage, task_id)["tools"]
    }
    close_tools = {
        str(getattr(tool, "__name__", ""))
        for tool in close_reader_module.build_close_reader_subagent(storage, task_id)["tools"]
    }
    feature_tools = {
        str(getattr(tool, "__name__", ""))
        for tool in feature_comparer_module.build_feature_comparer_subagent(storage, task_id)["tools"]
    }

    assert search_elements_tools == {"save_search_elements"}
    assert "run_execution_step" in query_tools
    assert coarse_tools == {"run_coarse_screen_batch"}
    assert close_tools == {"run_close_read_batch"}
    assert feature_tools == {"run_feature_compare"}
