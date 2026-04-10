from __future__ import annotations

from agents.ai_search.src import main_agent as main_agent_module
from agents.ai_search.src.main_agent import agent as main_agent_agent_module
from agents.ai_search.src.main_agent.prompt import MAIN_AGENT_SYSTEM_PROMPT
from agents.ai_search.src.subagents import close_reader as close_reader_module
from agents.ai_search.src.subagents import coarse_screener as coarse_screener_module
from agents.ai_search.src.subagents import feature_comparer as feature_comparer_module
from agents.ai_search.src.subagents import planner as planner_module
from agents.ai_search.src.subagents.close_reader.prompt import CLOSE_READER_SYSTEM_PROMPT, build_close_reader_prompt
from agents.ai_search.src.subagents.coarse_screener.prompt import COARSE_SCREEN_SYSTEM_PROMPT
from agents.ai_search.src.subagents.feature_comparer.prompt import FEATURE_COMPARER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.planner.prompt import PLANNER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.plan_prober.prompt import PLAN_PROBER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.query_executor.prompt import QUERY_EXECUTOR_SYSTEM_PROMPT
from agents.ai_search.src.subagents import query_executor as query_executor_module
from agents.ai_search.src.subagents import search_elements as search_elements_module
from agents.ai_search.src.subagents.search_elements.prompt import SEARCH_ELEMENTS_SYSTEM_PROMPT


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
        "get_session_context",
        "get_planning_context",
        "get_execution_context",
        "start_plan_drafting",
        "publish_planner_draft",
        "request_user_question",
        "request_plan_confirmation",
        "advance_workflow",
        "complete_session",
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
    planner_tools = {
        str(getattr(tool, "__name__", ""))
        for tool in planner_module.build_planner_subagent(storage, task_id)["tools"]
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
    assert planner_tools == {"commit_plan_draft"}
    assert "run_execution_step" in query_tools
    assert coarse_tools == {"run_coarse_screen_batch"}
    assert close_tools == {"run_close_read_batch"}
    assert feature_tools == {"run_feature_compare"}


def test_main_agent_prompt_uses_runtime_phase_names():
    assert "collecting_requirements" in MAIN_AGENT_SYSTEM_PROMPT
    assert "drafting_plan" in MAIN_AGENT_SYSTEM_PROMPT
    assert "awaiting_plan_confirmation" in MAIN_AGENT_SYSTEM_PROMPT
    assert "`collect_requirements`" not in MAIN_AGENT_SYSTEM_PROMPT
    assert "`draft_plan`" not in MAIN_AGENT_SYSTEM_PROMPT
    assert "`await_plan_confirmation`" not in MAIN_AGENT_SYSTEM_PROMPT
    assert "get_planning_context" in MAIN_AGENT_SYSTEM_PROMPT
    assert "get_execution_context" in MAIN_AGENT_SYSTEM_PROMPT
    assert "advance_workflow" in MAIN_AGENT_SYSTEM_PROMPT
    assert "`planner`" in MAIN_AGENT_SYSTEM_PROMPT
    assert "缺少申请人、申请日、优先权日时" in MAIN_AGENT_SYSTEM_PROMPT
    assert "异常处理与防死循环" in MAIN_AGENT_SYSTEM_PROMPT
    assert "私下决策检查清单" in MAIN_AGENT_SYSTEM_PROMPT
    assert "不要向用户输出思维链" in MAIN_AGENT_SYSTEM_PROMPT
    assert "同一执行上下文下最多重试 2 次" in MAIN_AGENT_SYSTEM_PROMPT
    assert "越权零容忍" in MAIN_AGENT_SYSTEM_PROMPT


def test_specialist_prompts_describe_allowed_tools_and_required_fields():
    assert "`save_search_elements`" in SEARCH_ELEMENTS_SYSTEM_PROMPT
    assert "clarification_summary" in SEARCH_ELEMENTS_SYSTEM_PROMPT
    assert '"申请人"' in SEARCH_ELEMENTS_SYSTEM_PROMPT

    assert "`probe_search_semantic`" in PLAN_PROBER_SYSTEM_PROMPT
    assert "overall_observation" in PLAN_PROBER_SYSTEM_PROMPT
    assert "retrieval_step_refs" in PLAN_PROBER_SYSTEM_PROMPT
    assert "signals" in PLAN_PROBER_SYSTEM_PROMPT

    assert "`commit_plan_draft`" in PLANNER_SYSTEM_PROMPT
    assert "query_blueprint_refs" in PLANNER_SYSTEM_PROMPT
    assert "activation_mode" in PLANNER_SYSTEM_PROMPT
    assert "activation_conditions" in PLANNER_SYSTEM_PROMPT

    assert "`prepare_lane_queries`" in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert "`fetch_patent_details`" in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert "plan_change_assessment" in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert "next_recommendation" in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert "adjustments`: 数组" in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert "outcome_signals" in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert '"too_broad" | "balanced" | "too_narrow"' in QUERY_EXECUTOR_SYSTEM_PROMPT

    assert "`run_coarse_screen_batch`" in COARSE_SCREEN_SYSTEM_PROMPT
    assert "不能遗漏" in COARSE_SCREEN_SYSTEM_PROMPT

    assert "`run_close_read_batch`" in CLOSE_READER_SYSTEM_PROMPT
    assert "claim_alignments" in CLOSE_READER_SYSTEM_PROMPT
    assert "selected" in CLOSE_READER_SYSTEM_PROMPT
    assert "rejected" in CLOSE_READER_SYSTEM_PROMPT
    assert "follow_up_hints`: 数组" in CLOSE_READER_SYSTEM_PROMPT

    assert "`run_feature_compare`" in FEATURE_COMPARER_SYSTEM_PROMPT
    assert "document_roles" in FEATURE_COMPARER_SYSTEM_PROMPT
    assert "creativity_readiness" in FEATURE_COMPARER_SYSTEM_PROMPT
    assert '"needs_more_evidence"' in FEATURE_COMPARER_SYSTEM_PROMPT
    assert "follow_up_search_hints`: 数组" in FEATURE_COMPARER_SYSTEM_PROMPT


def test_close_reader_dynamic_prompt_mentions_claim_alignments():
    prompt = build_close_reader_prompt(
        {"objective": "测试目标", "search_elements": []},
        [
            {
                "document_id": "doc-1",
                "pn": "CN123456A",
                "title": "一种装置",
                "abstract": "摘要",
                "claims": "权利要求",
                "description": "说明书",
            }
        ],
        {"CN123456A": "/tmp/doc-1.txt"},
    )

    assert "claim_alignments" in prompt
