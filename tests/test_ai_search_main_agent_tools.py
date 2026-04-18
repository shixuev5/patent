from __future__ import annotations

import sys
import types

stub_retrieval_pkg = types.ModuleType("agents.common.retrieval")
stub_retrieval_pkg.__path__ = []
stub_academic_query_utils = types.ModuleType("agents.common.retrieval.academic_query_utils")
stub_academic_query_utils.to_crossref_bibliographic_query = lambda text, *args, **kwargs: str(text or "").strip()
stub_academic_query_utils.to_semantic_academic_query = lambda text, *args, **kwargs: str(text or "").strip()
stub_academic_search = types.ModuleType("agents.common.retrieval.academic_search")

class _StubAcademicSearchClient:
    def search_openalex(self, *args, **kwargs):
        return []

    def search_semanticscholar(self, *args, **kwargs):
        return []

    def search_crossref(self, *args, **kwargs):
        return []

stub_academic_search.AcademicSearchClient = _StubAcademicSearchClient
stub_retrieval_pkg.academic_query_utils = stub_academic_query_utils
stub_retrieval_pkg.academic_search = stub_academic_search
stub_search_clients_pkg = types.ModuleType("agents.common.search_clients")
stub_search_clients_pkg.__path__ = []
stub_search_clients_factory = types.ModuleType("agents.common.search_clients.factory")

class _StubSearchClientFactory:
    @staticmethod
    def get_client(name):
        raise AssertionError(f"unexpected search client usage in tools contract test: {name}")

stub_search_clients_factory.SearchClientFactory = _StubSearchClientFactory
stub_search_clients_pkg.factory = stub_search_clients_factory
sys.modules.setdefault("agents.common.retrieval", stub_retrieval_pkg)
sys.modules.setdefault("agents.common.retrieval.academic_query_utils", stub_academic_query_utils)
sys.modules.setdefault("agents.common.retrieval.academic_search", stub_academic_search)
sys.modules.setdefault("agents.common.search_clients", stub_search_clients_pkg)
sys.modules.setdefault("agents.common.search_clients.factory", stub_search_clients_factory)

from agents.ai_search.src.main_agent.agent import build_main_agent
import agents.ai_search.src.main_agent.agent as main_agent_agent_module
from agents.ai_search.src.main_agent.prompt import MAIN_AGENT_SYSTEM_PROMPT
from agents.ai_search.src.runtime_context import AiSearchRuntimeContext
from agents.ai_search.src.subagents.close_reader.agent import build_close_reader_subagent
from agents.ai_search.src.subagents.coarse_screener.agent import build_coarse_screener_subagent
from agents.ai_search.src.subagents.feature_comparer.agent import build_feature_comparer_subagent
from agents.ai_search.src.subagents.planner.agent import build_planner_subagent
from agents.ai_search.src.subagents.plan_prober.agent import build_plan_prober_subagent
from agents.ai_search.src.subagents.close_reader.prompt import CLOSE_READER_SYSTEM_PROMPT, build_close_reader_prompt
from agents.ai_search.src.subagents.coarse_screener.prompt import COARSE_SCREEN_SYSTEM_PROMPT
from agents.ai_search.src.subagents.feature_comparer.prompt import FEATURE_COMPARER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.planner.prompt import PLANNER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.plan_prober.prompt import PLAN_PROBER_SYSTEM_PROMPT
from agents.ai_search.src.subagents.query_executor.prompt import QUERY_EXECUTOR_SYSTEM_PROMPT
from agents.ai_search.src.subagents.query_executor.agent import build_query_executor_subagent
from agents.ai_search.src.subagents.search_elements.agent import build_search_elements_subagent
from agents.ai_search.src.subagents.search_elements.prompt import SEARCH_ELEMENTS_SYSTEM_PROMPT


def test_build_main_agent_exposes_orchestration_tools_only(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(main_agent_agent_module, "create_deep_agent", _fake_create_deep_agent)
    monkeypatch.setattr(main_agent_agent_module, "large_model", lambda: object())

    build_main_agent(object(), "task-ai-search")

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
        "request_human_decision",
        "advance_workflow",
        "complete_session",
    }
    assert captured.get("context_schema") is AiSearchRuntimeContext


def test_specialists_own_domain_tools():
    search_elements_spec = build_search_elements_subagent()
    planner_spec = build_planner_subagent()
    prober_spec = build_plan_prober_subagent()
    query_tools = {
        str(getattr(tool, "__name__", ""))
        for tool in build_query_executor_subagent()["runnable"].tools
    }
    coarse_tools = {
        str(getattr(tool, "__name__", ""))
        for tool in build_coarse_screener_subagent()["runnable"].tools
    }
    close_tools = {
        str(getattr(tool, "__name__", ""))
        for tool in build_close_reader_subagent()["runnable"].tools
    }
    feature_tools = {
        str(getattr(tool, "__name__", ""))
        for tool in build_feature_comparer_subagent()["runnable"].tools
    }

    assert "runnable" in search_elements_spec
    assert "runnable" in planner_spec
    assert "runnable" in prober_spec
    assert "runnable" in build_query_executor_subagent()
    assert "runnable" in build_coarse_screener_subagent()
    assert "runnable" in build_close_reader_subagent()
    assert "runnable" in build_feature_comparer_subagent()
    assert query_tools == {
        "run_execution_step",
        "search_trace",
        "search_semantic",
        "search_boolean",
        "count_boolean",
        "fetch_patent_details",
        "prepare_lane_queries",
        "search_academic_openalex",
        "search_academic_semanticscholar",
        "search_academic_crossref",
    }
    assert {
        "probe_search_semantic",
        "probe_search_boolean",
        "probe_count_boolean",
    } == {
        str(getattr(tool, "__name__", ""))
        for tool in prober_spec["runnable"].tools
    }
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
    assert "write_stage_log" not in MAIN_AGENT_SYSTEM_PROMPT
    assert "advance_workflow" in MAIN_AGENT_SYSTEM_PROMPT
    assert "`planner`" in MAIN_AGENT_SYSTEM_PROMPT
    assert "缺少申请人、申请日、优先权日时" in MAIN_AGENT_SYSTEM_PROMPT
    assert "异常处理与防死循环" in MAIN_AGENT_SYSTEM_PROMPT
    assert "私下决策检查清单" in MAIN_AGENT_SYSTEM_PROMPT
    assert "不要向用户输出思维链" in MAIN_AGENT_SYSTEM_PROMPT
    assert "同一执行上下文下最多重试 2 次" in MAIN_AGENT_SYSTEM_PROMPT
    assert "越权零容忍" in MAIN_AGENT_SYSTEM_PROMPT


def test_specialist_prompts_describe_allowed_tools_and_required_fields():
    assert "`save_search_elements`" not in SEARCH_ELEMENTS_SYSTEM_PROMPT
    assert "系统自动持久化" in SEARCH_ELEMENTS_SYSTEM_PROMPT
    assert "missing_items" in SEARCH_ELEMENTS_SYSTEM_PROMPT
    assert "clarification_summary" not in SEARCH_ELEMENTS_SYSTEM_PROMPT
    assert '"申请人"' in SEARCH_ELEMENTS_SYSTEM_PROMPT

    assert "`probe_search_semantic`" in PLAN_PROBER_SYSTEM_PROMPT
    assert "`save_probe_findings`" not in PLAN_PROBER_SYSTEM_PROMPT
    assert "overall_observation" not in PLAN_PROBER_SYSTEM_PROMPT
    assert "retrieval_step_refs" in PLAN_PROBER_SYSTEM_PROMPT
    assert "signals" in PLAN_PROBER_SYSTEM_PROMPT

    assert "`save_plan_execution_overview`" not in PLANNER_SYSTEM_PROMPT
    assert "`append_plan_sub_plan`" not in PLANNER_SYSTEM_PROMPT
    assert "`save_plan_review_markdown`" not in PLANNER_SYSTEM_PROMPT
    assert "`finalize_plan_draft`" not in PLANNER_SYSTEM_PROMPT
    assert "完整的 `review_markdown` Markdown 文档本身" in PLANNER_SYSTEM_PROMPT
    assert "query_blueprint_refs" in PLANNER_SYSTEM_PROMPT
    assert "activation_mode" in PLANNER_SYSTEM_PROMPT
    assert "activation_conditions" in PLANNER_SYSTEM_PROMPT

    assert "`prepare_lane_queries`" in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert "`fetch_patent_details`" in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert "plan_change_assessment" in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert "next_recommendation" not in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert "adjustments`: 数组" not in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert "result_summary" not in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert "outcome_signals" in QUERY_EXECUTOR_SYSTEM_PROMPT
    assert '"too_broad" | "balanced" | "too_narrow"' in QUERY_EXECUTOR_SYSTEM_PROMPT

    assert "`run_coarse_screen_batch`" in COARSE_SCREEN_SYSTEM_PROMPT
    assert "不能遗漏" in COARSE_SCREEN_SYSTEM_PROMPT
    assert "reasoning_summary" not in COARSE_SCREEN_SYSTEM_PROMPT

    assert "`run_close_read_batch`" in CLOSE_READER_SYSTEM_PROMPT
    assert "claim_alignments" in CLOSE_READER_SYSTEM_PROMPT
    assert "selected" in CLOSE_READER_SYSTEM_PROMPT
    assert "rejected" in CLOSE_READER_SYSTEM_PROMPT
    assert "follow_up_hints`: 数组" not in CLOSE_READER_SYSTEM_PROMPT
    assert "coverage_summary" not in CLOSE_READER_SYSTEM_PROMPT

    assert "`run_feature_compare`" in FEATURE_COMPARER_SYSTEM_PROMPT
    assert "document_roles" in FEATURE_COMPARER_SYSTEM_PROMPT
    assert "creativity_readiness" in FEATURE_COMPARER_SYSTEM_PROMPT
    assert '"needs_more_evidence"' in FEATURE_COMPARER_SYSTEM_PROMPT
    assert "follow_up_search_hints`: 数组" in FEATURE_COMPARER_SYSTEM_PROMPT
    assert "summary_markdown" not in FEATURE_COMPARER_SYSTEM_PROMPT
    assert "overall_findings" not in FEATURE_COMPARER_SYSTEM_PROMPT


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
