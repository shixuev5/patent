from __future__ import annotations

import importlib
import asyncio

from patent_agents.ai_search.src import runtime as agent_runtime_module
from patent_agents.ai_search.src.runtime import _patent_items_from_response, _stream_text_delta, normalize_stop_policy
from patent_agents.ai_search.src.analysis_seed import seed_prompt_from_analysis, seed_search_elements_from_analysis
from patent_agents.ai_search.src.reply_seed import seed_prompt_from_reply, seed_search_elements_from_reply
from patent_agents.ai_search.src.search_elements import normalize_search_elements_payload


def test_current_ai_search_modules_import_without_legacy_subagents() -> None:
    module_names = [
        "patent_agents.ai_search.src.runtime",
        "patent_agents.ai_search.src.analysis_seed",
        "patent_agents.ai_search.src.reply_seed",
        "patent_agents.ai_search.src.reporting",
        "patent_agents.ai_search.src.ids",
        "patent_agents.ai_search.src.time_utils",
        "backend.ai_search.agent_run_service",
        "backend.ai_search.artifacts_service",
        "backend.ai_search.service",
        "patent_agents.ai_search.src.search_elements",
        "patent_agents.ai_search.src.state",
    ]

    for name in module_names:
        importlib.import_module(name)


def test_normalize_stop_policy_bounds_and_filters_sources() -> None:
    policy = normalize_stop_policy(
        {
            "max_rounds": 999,
            "max_queries": 0,
            "max_candidates": "12",
            "max_selected_documents": 100,
            "max_no_new_result_rounds": "bad",
            "deadline_seconds": 1,
            "target_coverage": "  覆盖区别特征  ",
            "stop_when": "  找到两篇即可  ",
            "databases": ["zhihuiya", "unknown", "crossref"],
        }
    )

    assert policy["max_rounds"] == 30
    assert policy["max_queries"] == 30
    assert policy["max_candidates"] == 12
    assert policy["max_selected_documents"] == 50
    assert policy["max_no_new_result_rounds"] == 2
    assert policy["deadline_seconds"] == 30
    assert policy["target_coverage"] == "覆盖区别特征"
    assert policy["stop_when"] == "找到两篇即可"
    assert policy["databases"] == ["zhihuiya", "crossref"]


def test_search_element_seed_normalization_keeps_missing_context_visible() -> None:
    payload = normalize_search_elements_payload(
        {
            "objective": "检索视频异常检测方案",
            "filing_date": "20240301",
            "search_elements": [
                {
                    "feature": "异常检测",
                    "keywords_zh": ["异常检测", "异常检测"],
                    "keywords_en": ["anomaly detection"],
                    "block_id": "b",
                }
            ],
        }
    )

    assert payload["status"] == "complete"
    assert payload["filing_date"] == "2024-03-01"
    assert payload["search_elements"][0]["element_name"] == "异常检测"
    assert payload["search_elements"][0]["block_id"] == "B"
    assert payload["missing_items"] == ["申请人"]


def test_patent_items_from_response_accepts_zhihuiya_results_shape() -> None:
    assert _patent_items_from_response({"results": [{"pn": "CN1"}, "bad"]}) == [{"pn": "CN1"}]
    assert _patent_items_from_response({"items": [{"pn": "CN2"}]}) == [{"pn": "CN2"}]
    assert _patent_items_from_response({"data": {"results": [{"pn": "CN3"}]}}) == [{"pn": "CN3"}]


def test_stream_text_delta_accepts_responses_text_delta() -> None:
    class RawEvent:
        type = "raw_response_event"

        class data:
            type = "response.output_text.delta"
            delta = "增量文本"

    assert _stream_text_delta(RawEvent()) == "增量文本"


def test_search_agent_registers_controlled_subagent_tools(monkeypatch) -> None:
    monkeypatch.setattr(agent_runtime_module.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(agent_runtime_module.settings, "LLM_MODEL_LARGE", "large-model")
    monkeypatch.setattr(agent_runtime_module.settings, "LLM_MODEL_DEFAULT", "default-model")

    agent = agent_runtime_module.build_search_agent()
    tool_names = {str(getattr(tool, "name", "") or "") for tool in agent.tools}

    assert "run_retrieval_agent" in tool_names
    assert "run_review_agent" in tool_names
    assert "run_detail_agent" in tool_names
    assert "read_candidate_summaries" in tool_names
    assert "search_patents" not in tool_names
    assert "search_academic" not in tool_names
    assert "fetch_patent_detail" not in tool_names
    assert agent_runtime_module._default_model_name() == "default-model"


def test_stream_runner_ignores_internal_agent_updated_event(monkeypatch) -> None:
    class FakeStorage:
        def list_ai_search_messages(self, _task_id):
            return []

    class FakeContext:
        storage = FakeStorage()
        task_id = "task-1"
        traces = []

        def start_trace(self, **kwargs):
            self.traces.append(("start", kwargs))
            return ("trace-1", "now")

        def finish_trace(self, trace, **kwargs):
            self.traces.append(("finish", kwargs))

    class FakeAgent:
        name = "child-agent"

    class AgentUpdatedEvent:
        type = "agent_updated_stream_event"
        new_agent = FakeAgent()

    class RawDeltaEvent:
        type = "raw_response_event"

        class data:
            type = "response.output_text.delta"
            delta = "流式"

    class FakeRun:
        final_output = "流式答复"

        async def stream_events(self):
            yield AgentUpdatedEvent()
            yield RawDeltaEvent()

        def cancel(self):
            raise AssertionError("不应取消")

    monkeypatch.setattr(agent_runtime_module, "build_search_agent", lambda: FakeAgent())
    monkeypatch.setattr(agent_runtime_module.Runner, "run_streamed", lambda *_args, **_kwargs: FakeRun())

    deltas = []
    context = FakeContext()
    result = asyncio.run(
        agent_runtime_module.run_search_agent_stream(
            context,
            "测试",
            on_delta=lambda delta: deltas.append(delta),
        )
    )

    assert result == "流式答复"
    assert deltas == ["流式"]
    assert not any(item[1].get("tool_name") == "agent_updated" for item in context.traces)


def test_reply_seed_prompt_uses_open_search_language() -> None:
    reply_payload = {
        "task_id": "reply-1",
        "title": "AI 答复任务",
        "pn": "CN123456A",
        "search_followup_section": {
            "status": "complete",
            "objective": "围绕新增特征继续补检",
            "trigger_reasons": ["X 文献未覆盖特征 B"],
            "search_elements": [{"element_name": "特征 B", "keywords_zh": ["特征B"]}],
            "suggested_constraints": {"applicants": ["申请人A"], "filing_date": "2024-03-01"},
        },
    }

    elements = seed_search_elements_from_reply(reply_payload)
    prompt = seed_prompt_from_reply(reply_payload, elements)

    assert elements["objective"] == "围绕新增特征继续补检"
    assert elements["applicants"] == ["申请人A"]
    assert "自由检索" in prompt
    assert "不再生成待确认计划" in prompt
    assert "user_context_markdown" not in prompt
    assert "source_context" not in prompt
    assert "seeded_search_elements" not in prompt
    assert '"objective": "围绕新增特征继续补检"' in prompt


def test_analysis_seed_prompt_uses_only_structured_seed_payload() -> None:
    analysis_payload = {
        "metadata": {"task_id": "analysis-1", "resolved_pn": "CN123456A"},
        "report_core": {"ai_title": "测试专利"},
        "search_strategy": {
            "search_matrix": [
                {
                    "element_name": "二维图像转换",
                    "keywords_zh": ["二维图像"],
                    "keywords_en": ["2D image"],
                    "block_id": "A",
                }
            ]
        },
    }
    patent_payload = {
        "bibliographic_data": {
            "publication_number": "CN123456A",
            "invention_title": "测试专利",
            "application_date": "2024-01-01",
        }
    }

    elements = seed_search_elements_from_analysis(analysis_payload, patent_payload)
    prompt = seed_prompt_from_analysis(analysis_payload, patent_payload, elements)

    assert elements["objective"].startswith("围绕专利 CN123456A")
    assert "user_context_markdown" not in prompt
    assert "source_context" not in prompt
    assert "seeded_search_elements" not in prompt
    assert '"element_name": "二维图像转换"' in prompt
