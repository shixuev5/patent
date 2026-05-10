from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime_context import build_runtime_context
from agents.ai_search.src.state import default_ai_search_meta
from agents.ai_search.src.main_agent.search_plan_schemas import SearchPlanExecutionSpecDraftInput
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage import SQLiteTaskStorage


def _mount_context(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_compile_plan.db")
    manager = PipelineTaskManager(storage)
    task = manager.create_task(owner_id="guest_ai_search", task_type="ai_search", title="AI 检索会话")
    storage.update_task(task.id, metadata={"ai_search": default_ai_search_meta(f"ai-search-{task.id}")})
    return AiSearchAgentContext(storage, task.id), storage, task.id


def _runtime(context: AiSearchAgentContext) -> SimpleNamespace:
    return SimpleNamespace(context=build_runtime_context(context.storage, context.task_id))


def _execution_spec() -> SearchPlanExecutionSpecDraftInput:
    return SearchPlanExecutionSpecDraftInput.model_validate(
        {
            "search_scope": {
                "objective": "测试目标",
                "applicants": ["测试申请人"],
                "languages": ["zh"],
                "databases": ["zhihuiya"],
            },
            "constraints": {},
            "execution_policy": {},
            "search_elements_snapshot": {
                "status": "complete",
                "objective": "测试目标",
                "applicants": ["测试申请人"],
                "missing_items": [],
                "search_elements": [
                    {
                        "element_name": "参数窗口控制",
                        "keywords_zh": ["参数窗口控制"],
                        "keywords_en": ["parameter window control"],
                    }
                ],
            },
            "sub_plans": [
                {
                    "sub_plan_id": "sub_plan_1",
                    "title": "子计划 1",
                    "goal": "测试目标",
                    "semantic_query_text": "",
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
                            "probe_summary": {"observation": "预检结果可接受"},
                        }
                    ],
                    "query_blueprints": [{"batch_id": "b1", "goal": "测试目标", "sub_plan_id": "sub_plan_1"}],
                }
            ],
        }
    )


def test_compile_confirmed_search_plan_creates_confirmed_plan(tmp_path):
    context, storage, task_id = _mount_context(tmp_path)
    main_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_main_agent_tools()}
    runtime = _runtime(context)
    spec = _execution_spec()

    payload = json.loads(
        main_tools["compile_confirmed_search_plan"](
            review_markdown="# 检索计划\n\n## 检索目标\n测试目标",
            execution_spec=spec.model_dump(mode="python"),
            runtime=runtime,
        )
    )

    plan = storage.get_ai_search_plan(task_id, payload["plan_version"])
    fetched = json.loads(main_tools["get_workflow_context"](runtime=runtime))["planning"]

    assert payload == {"plan_version": 1, "status": "confirmed"}
    assert plan["status"] == "confirmed"
    assert plan["confirmed_at"]
    assert plan["execution_spec_json"]["sub_plans"][0]["retrieval_steps"][0]["phase_key"] == "execute_search"
    assert plan["execution_spec_json"]["search_elements_snapshot"]["search_elements"][0]["element_name"] == "参数窗口控制"
    assert fetched["current_plan"]["status"] == "confirmed"
    assert "search_plan_draft" not in fetched


def test_compile_confirmed_search_plan_normalizes_database_names(tmp_path):
    context, storage, task_id = _mount_context(tmp_path)
    main_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_main_agent_tools()}
    runtime = _runtime(context)
    spec = _execution_spec().model_copy(deep=True)
    spec.search_scope.databases = ["zhihuiya", "openalex", "crossref", "openalex"]

    plan_version = json.loads(
        main_tools["compile_confirmed_search_plan"](
            review_markdown="# 检索计划\n\n## 检索目标\n测试目标",
            execution_spec=spec.model_dump(mode="python"),
            runtime=runtime,
        )
    )["plan_version"]
    plan = storage.get_ai_search_plan(task_id, plan_version)

    assert plan is not None
    assert plan["execution_spec_json"]["search_scope"]["databases"] == ["zhihuiya", "openalex", "crossref"]


def test_compile_confirmed_search_plan_rejects_internal_phase_key_field(tmp_path):
    context, _storage, _task_id = _mount_context(tmp_path)
    main_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_main_agent_tools()}
    runtime = _runtime(context)
    spec = _execution_spec().model_dump(mode="python")
    spec["sub_plans"][0]["retrieval_steps"][0]["phase_key"] = "execute_search"

    with pytest.raises(Exception) as exc_info:
        main_tools["compile_confirmed_search_plan"](
            review_markdown="# 检索计划\n\n## 检索目标\n测试目标",
            execution_spec=spec,
            runtime=runtime,
        )

    assert "phase_key" in str(exc_info.value)


def test_compile_confirmed_search_plan_rejects_invalid_database_name(tmp_path):
    context, _storage, _task_id = _mount_context(tmp_path)
    main_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_main_agent_tools()}
    runtime = _runtime(context)
    spec = _execution_spec().model_dump(mode="python")
    spec["search_scope"]["databases"] = ["zhihuiya", "bad-db"]

    with pytest.raises(Exception) as exc_info:
        main_tools["compile_confirmed_search_plan"](
            review_markdown="# 检索计划\n\n## 检索目标\n测试目标",
            execution_spec=spec,
            runtime=runtime,
        )

    assert "databases" in str(exc_info.value)


def test_current_search_elements_reads_plan_level_snapshot(tmp_path):
    context, storage, task_id = _mount_context(tmp_path)
    main_tools = {getattr(tool, "__name__", ""): tool for tool in context.build_main_agent_tools()}
    runtime = _runtime(context)
    spec = _execution_spec()

    plan_version = json.loads(
        main_tools["compile_confirmed_search_plan"](
            review_markdown="# 检索计划\n\n## 检索目标\n测试目标",
            execution_spec=spec.model_dump(mode="python"),
            runtime=runtime,
        )
    )["plan_version"]

    current = context.current_search_elements(plan_version)
    plan = storage.get_ai_search_plan(task_id, plan_version)

    assert plan is not None
    assert current["objective"] == "测试目标"
    assert current["search_elements"][0]["element_name"] == "参数窗口控制"


def test_current_search_elements_can_read_seed_message_until_plan_exists(tmp_path):
    context, storage, task_id = _mount_context(tmp_path)
    storage.create_ai_search_message(
        {
            "message_id": uuid.uuid4().hex,
            "task_id": task_id,
            "role": "assistant",
            "kind": "search_elements_update",
            "content": "原始检索目标",
            "stream_status": "completed",
            "metadata": {
                "status": "complete",
                "objective": "原始检索目标",
                "applicants": [],
                "missing_items": [],
                "search_elements": [{"element_name": "要素A", "keywords_zh": ["要素A"], "keywords_en": ["feature a"]}],
            },
        }
    )

    current = context.current_search_elements()

    assert current["objective"] == "原始检索目标"
    assert current["search_elements"][0]["element_name"] == "要素A"
