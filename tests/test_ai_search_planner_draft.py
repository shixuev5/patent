from __future__ import annotations

import json
import pytest

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.main_agent.schemas import SearchPlanExecutionSpecInput
from agents.ai_search.src.state import default_ai_search_meta
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage.sqlite_storage import SQLiteTaskStorage


def _mount_context(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_planner_draft.db")
    manager = PipelineTaskManager(storage)
    task = manager.create_task(owner_id="guest_ai_search", task_type="ai_search", title="AI 检索会话")
    storage.update_task(task.id, metadata={"ai_search": default_ai_search_meta(f"ai-search-{task.id}")})
    return AiSearchAgentContext(storage, task.id), storage, task.id


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
    tools = {getattr(tool, "__name__", ""): tool for tool in context.build_planner_tools()}
    main_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_main_agent_tools()}

    result = tools["commit_plan_draft"](
        review_markdown="# 检索计划\n\n## 检索目标\n测试目标",
        execution_spec=_execution_spec(),
        probe_findings={"signals": [{"type": "semantic_probe", "count": 3}]},
    )
    payload = json.loads(result)
    draft = context.current_planner_draft()
    fetched = json.loads(main_tools["get_planning_context"]())

    assert payload["draft_id"] == draft["draft_id"]
    assert draft["draft_version"] == 1
    assert draft["execution_spec"]["sub_plans"][0]["retrieval_steps"][0]["query_blueprint_refs"] == ["b1"]
    assert fetched["planner_draft"]["draft_id"] == draft["draft_id"]
    assert fetched["planner_draft"]["probe_findings"]["signals"][0]["type"] == "semantic_probe"

    context.clear_planner_draft()

    assert context.current_planner_draft() == {}
    assert json.loads(main_tools["get_planning_context"]())["planner_draft"] == {}


@pytest.mark.parametrize("probe_findings", ["", "None", "{}", '{"signals":[{"type":"semantic_probe","count":2}]}'])
def test_planner_draft_commit_normalizes_string_probe_findings(tmp_path, probe_findings):
    context, storage, task_id = _mount_context(tmp_path)
    tools = {getattr(tool, "__name__", ""): tool for tool in context.build_planner_tools()}
    main_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_main_agent_tools()}

    payload = json.loads(
        tools["commit_plan_draft"](
            review_markdown="# 检索计划\n\n## 检索目标\n测试目标",
            execution_spec=_execution_spec(),
            probe_findings=probe_findings,
        )
    )

    draft = context.current_planner_draft()
    assert payload["draft_id"] == draft["draft_id"]
    if probe_findings.startswith("{\"signals\""):
        assert draft["probe_findings"]["signals"][0]["count"] == 2
    else:
        assert draft["probe_findings"] is None

    plan_version = json.loads(main_tools["publish_planner_draft"]())["plan_version"]
    plan = storage.get_ai_search_plan(task_id, plan_version)
    assert plan is not None
    assert plan["review_markdown"].startswith("# 检索计划")
