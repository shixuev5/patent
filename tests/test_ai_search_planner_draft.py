from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.main_agent.schemas import SearchPlanExecutionSpecInput
from agents.ai_search.src.runtime_context import build_runtime_context
from agents.ai_search.src.state import default_ai_search_meta
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage import SQLiteTaskStorage


def _mount_context(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_planner_draft.db")
    manager = PipelineTaskManager(storage)
    task = manager.create_task(owner_id="guest_ai_search", task_type="ai_search", title="AI 检索会话")
    storage.update_task(task.id, metadata={"ai_search": default_ai_search_meta(f"ai-search-{task.id}")})
    return AiSearchAgentContext(storage, task.id), storage, task.id


def _runtime(context: AiSearchAgentContext) -> SimpleNamespace:
    return SimpleNamespace(context=build_runtime_context(context.storage, context.task_id))


def _execution_spec() -> SearchPlanExecutionSpecInput:
    return SearchPlanExecutionSpecInput.model_validate(
        {
            "search_scope": {"objective": "测试目标", "applicants": [], "languages": ["zh"], "databases": ["zhihuiya"]},
            "constraints": {},
            "execution_policy": {},
            "sub_plans": [
                {
                    "sub_plan_id": "sub_plan_1",
                    "title": "子计划 1",
                    "goal": "测试目标",
                    "semantic_query_text": "",
                    "search_elements": [
                        {
                            "element_name": "要素A",
                            "keywords_zh": ["要素A"],
                            "keywords_en": ["feature a"],
                            "block_id": "B1",
                        }
                    ],
                    "retrieval_steps": [
                        {
                            "step_id": "step_1",
                            "title": "步骤 1",
                            "purpose": "验证方向",
                            "feature_combination": "A+B1",
                            "language_strategy": "中文优先",
                            "ipc_cpc_mode": "按需补 IPC/CPC",
                            "ipc_cpc_codes": [],
                            "expected_recall": "20-50",
                            "fallback_action": "扩词",
                            "query_blueprint_refs": ["b1"],
                            "phase_key": "execute_search",
                        }
                    ],
                    "query_blueprints": [{"batch_id": "b1", "goal": "测试目标", "sub_plan_id": "sub_plan_1"}],
                    "classification_hints": [],
                }
            ],
        }
    )


def test_planner_draft_commit_read_and_clear(tmp_path):
    context, _storage, _task_id = _mount_context(tmp_path)
    main_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_main_agent_tools()}
    runtime = _runtime(context)
    spec = _execution_spec()

    review_result = context.save_planner_draft_payload(
        review_markdown="# 检索计划\n\n## 检索目标\n测试目标",
        execution_spec=spec.model_dump(mode="python"),
        probe_findings={"signals": [{"type": "semantic_probe", "count": 3}]},
    )
    payload = json.loads(main_tools["publish_planner_draft"](runtime=runtime))
    draft = context.current_planner_draft()
    fetched = json.loads(main_tools["get_planning_context"](runtime=runtime))

    assert review_result["draft_id"]
    assert draft == {}
    assert payload["plan_version"] == 1
    assert fetched["planner_draft"] == {}
    plan = _storage.get_ai_search_plan(_task_id, payload["plan_version"])
    assert plan["execution_spec_json"]["sub_plans"][0]["retrieval_steps"][0]["query_blueprint_refs"] == ["b1"]
    assert plan["review_markdown"].startswith("# 检索计划")

    context.clear_planner_draft()

    assert context.current_planner_draft() == {}
    assert json.loads(main_tools["get_planning_context"](runtime=runtime))["planner_draft"] == {}


def test_save_planner_draft_tool_persists_draft_explicitly(tmp_path):
    context, _storage, _task_id = _mount_context(tmp_path)
    runtime = _runtime(context)
    spec = _execution_spec()
    planner_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_planner_tools()}

    result = json.loads(
        planner_tools["save_planner_draft"](
            review_markdown="# 检索计划\n\n## 检索目标\n测试目标",
            execution_spec=spec.model_dump(mode="python"),
            probe_findings={"signals": [{"tool": "probe_search_semantic", "observation": "前5篇较相关"}]},
            runtime=runtime,
        )
    )
    draft = context.current_planner_draft()

    assert result["draft_status"] == "drafting"
    assert result["sub_plan_count"] == 1
    assert draft["review_markdown"].startswith("# 检索计划")
    assert draft["execution_spec"]["sub_plans"][0]["sub_plan_id"] == "sub_plan_1"
    assert draft["probe_findings"]["signals"][0]["tool"] == "probe_search_semantic"


def test_save_search_elements_tool_persists_message_explicitly(tmp_path):
    context, storage, task_id = _mount_context(tmp_path)
    runtime = _runtime(context)
    search_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_search_elements_tools()}

    result = json.loads(
        search_tools["save_search_elements"](
            payload={
                "status": "complete",
                "objective": "无效检索",
                "applicants": [],
                "filing_date": "",
                "priority_date": "",
                "missing_items": ["申请人"],
                "search_elements": [
                    {
                        "element_name": "参数窗口控制",
                        "keywords_zh": ["参数窗口控制"],
                        "keywords_en": ["parameter window control"],
                    }
                ],
            },
            runtime=runtime,
        )
    )

    latest = context.current_search_elements()
    messages = storage.list_ai_search_messages(task_id)

    assert result["status"] == "complete"
    assert result["search_element_count"] == 1
    assert latest["objective"] == "无效检索"
    assert any(str(item.get("kind") or "") == "search_elements_update" for item in messages)


def test_save_probe_findings_tool_persists_draft_explicitly(tmp_path):
    context, _storage, _task_id = _mount_context(tmp_path)
    runtime = _runtime(context)
    prober_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_plan_prober_tools()}

    result = json.loads(
        prober_tools["save_probe_findings"](
            payload={
                "retrieval_step_refs": ["step_1"],
                "signals": [
                    {
                        "tool": "probe_search_boolean",
                        "observation": "前5篇有3篇相关",
                        "impact": "召回质量可接受",
                        "recommendation": "维持原查询",
                    }
                ],
            },
            runtime=runtime,
        )
    )
    draft = context.current_planner_draft()

    assert result["signal_count"] == 1
    assert result["retrieval_step_ref_count"] == 1
    assert draft["probe_findings"]["signals"][0]["tool"] == "probe_search_boolean"


def test_publish_planner_draft_normalizes_database_names(tmp_path):
    context, storage, task_id = _mount_context(tmp_path)
    main_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_main_agent_tools()}
    runtime = _runtime(context)
    spec = _execution_spec().model_copy(deep=True)
    spec.search_scope["databases"] = ["zhihuiya", "openalex", "bad-db", "crossref", "openalex"]

    context.save_planner_draft_payload(
        review_markdown="# 检索计划\n\n## 检索目标\n测试目标",
        execution_spec=spec.model_dump(mode="python"),
    )
    plan_version = json.loads(main_tools["publish_planner_draft"](runtime=runtime))["plan_version"]
    plan = storage.get_ai_search_plan(task_id, plan_version)

    assert plan is not None
    assert plan["execution_spec_json"]["search_scope"]["databases"] == ["zhihuiya", "openalex", "crossref"]


@pytest.mark.parametrize("probe_findings", ["", "None", "{}", '{"signals":[{"type":"semantic_probe","count":2}]}'])
def test_planner_draft_overview_normalizes_string_probe_findings(tmp_path, probe_findings):
    context, storage, task_id = _mount_context(tmp_path)
    main_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_main_agent_tools()}
    runtime = _runtime(context)
    spec = _execution_spec()

    normalized_probe_findings = None
    if probe_findings.startswith("{\"signals\""):
        normalized_probe_findings = json.loads(probe_findings)
    payload = context.save_planner_draft_payload(
        review_markdown="# 检索计划\n\n## 检索目标\n测试目标",
        execution_spec=spec.model_dump(mode="python"),
        probe_findings=normalized_probe_findings,
    )

    draft = context.current_planner_draft()
    assert payload["draft_id"] == draft["draft_id"]
    if probe_findings.startswith("{\"signals\""):
        assert draft["probe_findings"]["signals"][0]["count"] == 2
    else:
        assert draft["probe_findings"] is None

    plan_version = json.loads(main_tools["publish_planner_draft"](runtime=runtime))["plan_version"]
    plan = storage.get_ai_search_plan(task_id, plan_version)
    assert plan is not None
    assert plan["review_markdown"].startswith("# 检索计划")
