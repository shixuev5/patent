from __future__ import annotations

import json
from datetime import datetime

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.orchestration.execution_runtime import (
    build_conditional_todos_for_completed_step,
    build_step_directive,
    evaluate_exhaustion_payload,
)
from backend.storage import Task, TaskStatus, TaskType
from backend.storage.sqlite_storage import SQLiteTaskStorage


def _plan_record(task_id: str, *, plan_version: int = 1, status: str = "confirmed", include_conditional: bool = False) -> dict:
    retrieval_steps = [
        {
            "step_id": "step_1",
            "title": "子计划 1 / 首轮宽召回",
            "purpose": "执行首轮宽召回",
            "feature_combination": "检索目标核心特征",
            "language_strategy": "中文优先",
            "ipc_cpc_mode": "按需补 IPC/CPC",
            "ipc_cpc_codes": [],
            "expected_recall": "获取候选池",
            "fallback_action": "结果异常时调整同义词",
            "query_blueprint_refs": ["b1"],
            "phase_key": "execute_search",
            "activation_mode": "immediate",
        }
    ]
    query_blueprints = [{"batch_id": "b1", "goal": "检索目标", "sub_plan_id": "sub_plan_1"}]
    if include_conditional:
        retrieval_steps.append(
            {
                "step_id": "step_2",
                "title": "子计划 1 / Block C 条件分支",
                "purpose": "在命中主目标或结果过宽时追加 Block C 检索",
                "feature_combination": "A+C",
                "language_strategy": "中文优先",
                "ipc_cpc_mode": "沿用并补 IPC/CPC",
                "ipc_cpc_codes": ["G06N 3/08"],
                "expected_recall": "获得更聚焦的候选池",
                "fallback_action": "继续微调 Block C 条件",
                "query_blueprint_refs": ["b2"],
                "phase_key": "execute_search",
                "activation_mode": "conditional",
                "depends_on_step_ids": ["step_1"],
                "activation_conditions": {
                    "any_of": [
                        {"signal": "primary_goal_reached", "equals": True},
                        {"signal": "recall_quality", "equals": "too_broad"},
                    ]
                },
                "activation_summary": "命中主目标或结果过宽时激活。",
            }
        )
        query_blueprints.append({"batch_id": "b2", "goal": "Block C 条件检索", "sub_plan_id": "sub_plan_1"})
    return {
        "task_id": task_id,
        "plan_version": plan_version,
        "status": status,
        "review_markdown": "# 检索计划\n\n## 检索目标\n检索目标",
        "execution_spec_json": {
            "search_scope": {"objective": "检索目标"},
            "constraints": {},
            "execution_policy": {"dynamic_replanning": True, "planner_visibility": "summary_only", "max_rounds": 3},
            "sub_plans": [
                {
                    "sub_plan_id": "sub_plan_1",
                    "title": "子计划 1",
                    "goal": "检索目标",
                    "semantic_query_text": "",
                    "search_elements": [],
                    "retrieval_steps": retrieval_steps,
                    "query_blueprints": query_blueprints,
                    "classification_hints": [],
                }
            ],
        },
    }


def _create_task(storage: SQLiteTaskStorage, task_id: str = "task-gap") -> None:
    now = datetime.now()
    storage.create_task(
        Task(
            id=task_id,
            owner_id="guest:gap-user",
            task_type=TaskType.AI_SEARCH.value,
            status=TaskStatus.PROCESSING,
            created_at=now,
            updated_at=now,
            metadata={"ai_search": {"current_phase": "feature_comparison"}},
        )
    )


def _create_run(storage: SQLiteTaskStorage, task_id: str = "task-gap", *, plan_version: int = 1, phase: str = "feature_comparison") -> str:
    run_id = f"{task_id}-run-{plan_version}"
    storage.create_ai_search_run(
        {
            "run_id": run_id,
            "task_id": task_id,
            "plan_version": plan_version,
            "phase": phase,
            "status": "processing",
        }
    )
    task = storage.get_task(task_id)
    assert task is not None
    storage.update_task(
        task_id,
        metadata={"ai_search": {"current_phase": phase, "active_plan_version": plan_version}},
    )
    return run_id


def _create_feature_batch(storage: SQLiteTaskStorage, task_id: str, run_id: str, *, plan_version: int = 1) -> str:
    batch_id = f"{run_id}-feature-batch"
    storage.create_ai_search_batch(
        {
            "batch_id": batch_id,
            "run_id": run_id,
            "task_id": task_id,
            "plan_version": plan_version,
            "batch_type": "feature_compare",
            "status": "loaded",
        }
    )
    return batch_id


def _seed_gap_results(storage: SQLiteTaskStorage, task_id: str = "task-gap", *, plan_version: int = 1) -> tuple[str, str]:
    run_id = _create_run(storage, task_id, plan_version=plan_version, phase="feature_comparison")
    feature_batch_id = _create_feature_batch(storage, task_id, run_id, plan_version=plan_version)
    storage.create_ai_search_close_read_result(
        {
            "result_id": f"{run_id}-close-read",
            "run_id": run_id,
            "batch_id": f"{run_id}-close-batch",
            "task_id": task_id,
            "plan_version": plan_version,
            "follow_up_hints": ["补搜约束条件相关实现"],
            "limitation_gaps": [{"claim_id": "1", "limitation_id": "1-L2", "gap_summary": "缺少约束条件"}],
            "document_assessments": [],
            "key_passages": [],
            "claim_alignments": [],
            "limitation_coverage": [],
        }
    )
    storage.create_ai_search_feature_comparison(
        {
            "feature_comparison_id": f"{run_id}-feature-result",
            "run_id": run_id,
            "batch_id": feature_batch_id,
            "task_id": task_id,
            "plan_version": plan_version,
            "table_json": [{"feature": "A"}],
            "coverage_gaps": [{"claim_id": "1", "limitation_id": "1-L2", "gap_type": "missing_support"}],
            "creativity_readiness": "needs_more_evidence",
            "summary_markdown": "还不能完成创造性评价",
        }
    )
    return run_id, feature_batch_id


def test_get_planning_context_reads_latest_close_read_and_feature_compare_results(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_context.db")
    _create_task(storage)
    _seed_gap_results(storage)

    context = AiSearchAgentContext(storage, "task-gap")
    get_planning_context = next(
        tool for tool in context.build_main_agent_tools() if str(getattr(tool, "__name__", "")) == "get_planning_context"
    )

    payload = json.loads(get_planning_context())

    assert payload["gap_context"]["close_read_result"]["limitation_gaps"][0]["limitation_id"] == "1-L2"
    assert payload["gap_context"]["feature_compare_result"]["creativity_readiness"] == "needs_more_evidence"


def test_get_planning_context_reports_replan_when_gaps_remain(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_eval.db")
    _create_task(storage)
    _seed_gap_results(storage)

    context = AiSearchAgentContext(storage, "task-gap")
    get_planning_context = next(
        tool for tool in context.build_main_agent_tools() if str(getattr(tool, "__name__", "")) == "get_planning_context"
    )

    payload = json.loads(get_planning_context())

    assert payload["gap_progress"]["should_continue_search"] is True
    assert payload["gap_progress"]["recommended_action"] == "replan_search"
    assert payload["gap_progress"]["coverage_gap_count"] == 1


def test_build_execution_step_directive_includes_gap_context(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_directive.db")
    _create_task(storage)
    storage.create_ai_search_plan(
        _plan_record("task-gap")
    )
    run_id = _create_run(storage, "task-gap", plan_version=1, phase="execute_search")
    storage.replace_ai_search_retrieval_todos(
        run_id,
        "task-gap",
        1,
        [
            {
                "todo_id": "plan_1:sub_plan_1:step_1",
                "sub_plan_id": "sub_plan_1",
                "step_id": "step_1",
                "title": "子计划 1 / 首轮宽召回",
                "description": "目的：执行首轮宽召回",
                "status": "in_progress",
            }
        ],
    )
    storage.update_ai_search_run("task-gap", run_id, active_retrieval_todo_id="plan_1:sub_plan_1:step_1")
    feature_batch_id = _create_feature_batch(storage, "task-gap", run_id, plan_version=1)
    storage.create_ai_search_feature_comparison(
        {
            "feature_comparison_id": f"{run_id}-feature-result",
            "run_id": run_id,
            "batch_id": feature_batch_id,
            "task_id": "task-gap",
            "plan_version": 1,
            "coverage_gaps": [{"claim_id": "1", "limitation_id": "1-L3", "gap_type": "combination_gap"}],
            "follow_up_search_hints": ["补搜实现方式B"],
            "creativity_readiness": "needs_more_evidence",
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    directive = build_step_directive(context, 1)

    assert directive["gap_context"]["feature_compare_result"]["creativity_readiness"] == "needs_more_evidence"
    assert directive["gap_context"]["feature_compare_result"]["coverage_gaps"][0]["gap_type"] == "combination_gap"
    assert directive["current_todo"]["todo_id"] == "plan_1:sub_plan_1:step_1"


def test_build_gap_strategy_seed_payload_extracts_targeted_gaps(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_seed.db")
    _create_task(storage)
    run_id = _create_run(storage, "task-gap", plan_version=1, phase="feature_comparison")
    storage.create_ai_search_close_read_result(
        {
            "result_id": f"{run_id}-close-read",
            "run_id": run_id,
            "batch_id": f"{run_id}-close-batch",
            "task_id": "task-gap",
            "plan_version": 1,
            "follow_up_hints": ["补搜参数窗口实现"],
            "limitation_gaps": [
                {
                    "claim_id": "1",
                    "limitation_id": "1-L2",
                    "gap_type": "missing_support",
                    "gap_summary": "缺少约束条件",
                    "suggested_keywords": ["约束条件", "参数窗口"],
                }
            ],
            "document_assessments": [],
            "key_passages": [],
            "claim_alignments": [],
            "limitation_coverage": [],
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    payload = context.build_gap_strategy_seed_payload()

    assert payload["planning_mode"] == "gap_replan"
    assert payload["targeted_gaps"][0]["limitation_id"] == "1-L2"
    assert payload["seed_batch_specs"][0]["seed_terms"] == ["约束条件", "参数窗口"]


def test_execution_policy_uses_takeover_defaults(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_execution_policy.db")
    _create_task(storage)
    storage.create_ai_search_plan(_plan_record("task-gap"))
    storage.update_task(
        "task-gap",
        metadata={"ai_search": {"current_phase": "drafting_plan", "active_plan_version": 1}},
    )

    context = AiSearchAgentContext(storage, "task-gap")
    policy = context.execution_policy(1)

    assert policy["max_rounds"] == 3
    assert policy["max_no_progress_rounds"] == 2
    assert policy["max_selected_documents"] == 5
    assert policy["decision_on_exhaustion"] is True


def test_evaluate_exhaustion_payload_triggers_human_takeover_on_no_progress_limit(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_exhaustion_eval.db")
    _create_task(storage)
    plan = _plan_record("task-gap")
    plan["execution_spec_json"]["execution_policy"].update(
        {
            "max_rounds": 5,
            "max_no_progress_rounds": 2,
            "max_selected_documents": 5,
            "decision_on_exhaustion": True,
        }
    )
    storage.create_ai_search_plan(plan)
    run_id = _create_run(storage, "task-gap", plan_version=1, phase="feature_comparison")
    storage.update_ai_search_run(
        "task-gap",
        run_id,
        human_decision_state={
            "execution_round_count": 1,
            "no_progress_round_count": 1,
            "last_selected_count": 1,
            "last_readiness": "needs_more_evidence",
            "last_gap_signature": {
                "limitation_gap_count": 0,
                "coverage_gap_count": 1,
                "follow_up_hint_count": 0,
                "weak_evidence_count": 0,
            },
            "processed_execution_summary_count": 0,
        },
    )
    storage.upsert_ai_search_documents(
        [
            {
                "run_id": run_id,
                "document_id": "doc-1",
                "task_id": "task-gap",
                "plan_version": 1,
                "pn": "CN1",
                "title": "文献1",
                "abstract": "",
                "stage": "selected",
            }
        ]
    )
    storage.create_ai_search_execution_summary(
        {
            "summary_id": f"{run_id}-summary-1",
            "run_id": run_id,
            "task_id": "task-gap",
            "plan_version": 1,
            "todo_id": "plan_1:sub_plan_1:step_1",
            "sub_plan_id": "sub_plan_1",
            "step_id": "step_1",
            "new_unique_candidates": 0,
            "candidate_pool_size": 1,
        }
    )
    feature_batch_id = _create_feature_batch(storage, "task-gap", run_id, plan_version=1)
    storage.create_ai_search_feature_comparison(
        {
            "feature_comparison_id": f"{run_id}-feature-result",
            "run_id": run_id,
            "batch_id": feature_batch_id,
            "task_id": "task-gap",
            "plan_version": 1,
            "coverage_gaps": [{"claim_id": "1", "limitation_id": "1-L3", "gap_type": "combination_gap"}],
            "creativity_readiness": "needs_more_evidence",
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    payload = evaluate_exhaustion_payload(context, 1)

    assert payload["is_no_progress"] is True
    assert payload["no_progress_round_count"] == 2
    assert payload["triggered_limit"] == "max_no_progress_rounds"
    assert payload["should_request_decision"] is True


def test_complete_session_is_blocked_when_gap_replan_is_required(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_block.db")
    _create_task(storage)
    run_id = _create_run(storage, "task-gap", plan_version=1, phase="feature_comparison")
    feature_batch_id = _create_feature_batch(storage, "task-gap", run_id, plan_version=1)
    storage.create_ai_search_feature_comparison(
        {
            "feature_comparison_id": f"{run_id}-feature-result",
            "run_id": run_id,
            "batch_id": feature_batch_id,
            "task_id": "task-gap",
            "plan_version": 1,
            "coverage_gaps": [{"claim_id": "1", "limitation_id": "1-L3", "gap_type": "combination_gap"}],
            "creativity_readiness": "needs_more_evidence",
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    complete_session = next(
        tool for tool in context.build_main_agent_tools() if str(getattr(tool, "__name__", "")) == "complete_session"
    )

    payload = json.loads(complete_session(plan_version=1))

    assert payload["blocked"] is True
    assert payload["reason"] == "gap_replan_required"
    assert payload["recommended_action"] == "replan_search"


def test_run_feature_compare_commit_persists_feature_compare_result_message(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_feature.db")
    _create_task(storage)
    storage.create_ai_search_plan(_plan_record("task-gap"))
    run_id = _create_run(storage, "task-gap", plan_version=1, phase="feature_comparison")
    storage.upsert_ai_search_documents(
        [
            {
                "run_id": run_id,
                "document_id": "doc-1",
                "task_id": "task-gap",
                "plan_version": 1,
                "pn": "CN1",
                "title": "文献1",
                "abstract": "",
                "stage": "selected",
            }
        ]
    )

    context = AiSearchAgentContext(storage, "task-gap")
    run_feature_compare = next(
        tool for tool in context.build_feature_comparer_tools() if str(getattr(tool, "__name__", "")) == "run_feature_compare"
    )
    load_payload = json.loads(run_feature_compare(operation="load", plan_version=1))

    run_feature_compare(
        operation="commit",
        payload_json=json.dumps(
            {
                "batch_id": load_payload["batch_id"],
                "table_rows": [{"feature": "A", "document_id": "doc-1"}],
                "summary_markdown": "summary",
                "overall_findings": "仍需补一篇组合文献。",
                "coverage_gaps": [{"claim_id": "1", "limitation_id": "1-L3", "gap_type": "combination_gap"}],
                "follow_up_search_hints": ["补搜实现方式B"],
                "creativity_readiness": "needs_more_evidence",
                "readiness_rationale": "当前仅覆盖部分区别特征。",
            },
            ensure_ascii=False,
        ),
        plan_version=1,
    )

    messages = storage.list_ai_search_messages("task-gap")
    chat_messages = [item for item in messages if str(item.get("kind") or "") == "chat"]
    feature_result = storage.get_ai_search_feature_comparison("task-gap", 1)

    assert feature_result is not None
    assert feature_result["creativity_readiness"] == "needs_more_evidence"
    assert feature_result["coverage_gaps"][0]["gap_type"] == "combination_gap"
    assert any(str(item.get("content") or "") == "仍需补一篇组合文献。" for item in chat_messages)


def test_advance_workflow_begin_execution_sets_resume_metadata_on_todo(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_resume_todo.db")
    _create_task(storage)
    storage.create_ai_search_plan(
        _plan_record("task-gap")
    )
    storage.update_task(
        "task-gap",
        metadata={
            "ai_search": {
                "current_phase": "drafting_plan",
                "active_plan_version": 1,
                "draft_todos": [
                    {"todo_id": "plan_1:sub_plan_1:step_1", "sub_plan_id": "sub_plan_1", "step_id": "step_1", "phase_key": "execute_search", "title": "执行步骤 1", "description": "目的：执行首轮宽召回", "status": "pending"},
                    {"todo_id": "plan_1:sub_plan_1:step_2", "sub_plan_id": "sub_plan_1", "step_id": "step_2", "phase_key": "execute_search", "title": "执行步骤 2", "description": "目的：收窄检索", "status": "pending"},
                ],
            }
        },
    )

    context = AiSearchAgentContext(storage, "task-gap")
    advance_workflow = next(
        tool for tool in context.build_main_agent_tools() if str(getattr(tool, "__name__", "")) == "advance_workflow"
    )
    get_execution_context = next(tool for tool in context.build_main_agent_tools() if str(getattr(tool, "__name__", "")) == "get_execution_context")

    advance_workflow(action="begin_execution", plan_version=1)
    payload = json.loads(get_execution_context(plan_version=1))
    todos = context._current_todos()
    execute_todo = next(item for item in todos if item["todo_id"] == "plan_1:sub_plan_1:step_1")

    assert execute_todo["status"] == "in_progress"
    assert execute_todo["resume_from"] == "run_execution_step.load"
    assert execute_todo["attempt_count"] == 1
    assert execute_todo["started_at"]
    assert payload["current_todo"]["todo_id"] == "plan_1:sub_plan_1:step_1"


def test_run_execution_step_commit_persists_step_summary(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_round_dedupe.db")
    _create_task(storage)
    storage.create_ai_search_plan(
        _plan_record("task-gap")
    )
    run_id = _create_run(storage, "task-gap", plan_version=1, phase="execute_search")
    storage.replace_ai_search_retrieval_todos(
        run_id,
        "task-gap",
        1,
        [
            {
                "todo_id": "plan_1:sub_plan_1:step_1",
                "sub_plan_id": "sub_plan_1",
                "step_id": "step_1",
                "title": "执行步骤 1",
                "description": "目的：执行首轮宽召回",
                "status": "in_progress",
            }
        ],
    )
    storage.update_ai_search_run("task-gap", run_id, active_retrieval_todo_id="plan_1:sub_plan_1:step_1")

    context = AiSearchAgentContext(storage, "task-gap")
    run_execution_step = next(
        tool for tool in context.build_query_executor_tools() if str(getattr(tool, "__name__", "")) == "run_execution_step"
    )

    result = run_execution_step(
            operation="commit",
            payload_json=json.dumps(
                {
                    "todo_id": "plan_1:sub_plan_1:step_1",
                    "step_id": "step_1",
                    "sub_plan_id": "sub_plan_1",
                    "result_summary": "首轮召回有效",
                    "adjustments": ["补充英文同义词"],
                    "plan_change_assessment": {"requires_replan": False, "reason_codes": []},
                    "next_recommendation": "advance_to_next_step",
                    "new_unique_candidates": 2,
                    "candidate_pool_size": 3,
                },
                ensure_ascii=False,
            ),
                plan_version=1,
            )
    summaries = storage.list_ai_search_execution_summaries(run_id)
    assert result == "execution step summary saved"
    assert len(summaries) == 1
    assert summaries[0]["metadata"]["todo_id"] == "plan_1:sub_plan_1:step_1"
    assert summaries[0]["outcome_signals"]["primary_goal_reached"] is False
    assert summaries[0]["outcome_signals"]["recall_quality"] == "balanced"


def test_conditional_todo_activates_when_primary_goal_reached(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_conditional_primary_goal.db")
    _create_task(storage)
    storage.create_ai_search_plan(_plan_record("task-gap", include_conditional=True))
    run_id = _create_run(storage, "task-gap", plan_version=1, phase="execute_search")
    storage.replace_ai_search_retrieval_todos(
        run_id,
        "task-gap",
        1,
        [
            {
                "todo_id": "plan_1:sub_plan_1:step_1",
                "sub_plan_id": "sub_plan_1",
                "step_id": "step_1",
                "title": "执行步骤 1",
                "description": "目的：执行首轮宽召回",
                "status": "completed",
            }
        ],
    )
    storage.create_ai_search_execution_summary(
        {
            "summary_id": "summary-step-1",
            "run_id": run_id,
            "task_id": "task-gap",
            "plan_version": 1,
            "todo_id": "plan_1:sub_plan_1:step_1",
            "sub_plan_id": "sub_plan_1",
            "step_id": "step_1",
            "metadata": {
                "outcome_signals": {
                    "primary_goal_reached": True,
                    "recall_quality": "balanced",
                    "triggered_by_adjustment": False,
                }
            },
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    activated = build_conditional_todos_for_completed_step(context, 1, "plan_1:sub_plan_1:step_1")

    assert [item["todo_id"] for item in activated] == ["plan_1:sub_plan_1:step_2"]


def test_conditional_todo_activates_when_recall_quality_too_broad(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_conditional_too_broad.db")
    _create_task(storage)
    storage.create_ai_search_plan(_plan_record("task-gap", include_conditional=True))
    run_id = _create_run(storage, "task-gap", plan_version=1, phase="execute_search")
    storage.replace_ai_search_retrieval_todos(
        run_id,
        "task-gap",
        1,
        [
            {
                "todo_id": "plan_1:sub_plan_1:step_1",
                "sub_plan_id": "sub_plan_1",
                "step_id": "step_1",
                "title": "执行步骤 1",
                "description": "目的：执行首轮宽召回",
                "status": "completed",
            }
        ],
    )
    storage.create_ai_search_execution_summary(
        {
            "summary_id": "summary-step-1",
            "run_id": run_id,
            "task_id": "task-gap",
            "plan_version": 1,
            "todo_id": "plan_1:sub_plan_1:step_1",
            "sub_plan_id": "sub_plan_1",
            "step_id": "step_1",
            "metadata": {
                "outcome_signals": {
                    "primary_goal_reached": False,
                    "recall_quality": "too_broad",
                    "triggered_by_adjustment": True,
                }
            },
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    activated = build_conditional_todos_for_completed_step(context, 1, "plan_1:sub_plan_1:step_1")

    assert [item["todo_id"] for item in activated] == ["plan_1:sub_plan_1:step_2"]


def test_conditional_todo_stays_dormant_when_signals_do_not_match(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_conditional_no_match.db")
    _create_task(storage)
    storage.create_ai_search_plan(_plan_record("task-gap", include_conditional=True))
    run_id = _create_run(storage, "task-gap", plan_version=1, phase="execute_search")
    storage.replace_ai_search_retrieval_todos(
        run_id,
        "task-gap",
        1,
        [
            {
                "todo_id": "plan_1:sub_plan_1:step_1",
                "sub_plan_id": "sub_plan_1",
                "step_id": "step_1",
                "title": "执行步骤 1",
                "description": "目的：执行首轮宽召回",
                "status": "completed",
            }
        ],
    )
    storage.create_ai_search_execution_summary(
        {
            "summary_id": "summary-step-1",
            "run_id": run_id,
            "task_id": "task-gap",
            "plan_version": 1,
            "todo_id": "plan_1:sub_plan_1:step_1",
            "sub_plan_id": "sub_plan_1",
            "step_id": "step_1",
            "metadata": {
                "outcome_signals": {
                    "primary_goal_reached": False,
                    "recall_quality": "balanced",
                    "triggered_by_adjustment": False,
                }
            },
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")

    assert build_conditional_todos_for_completed_step(context, 1, "plan_1:sub_plan_1:step_1") == []


def test_advance_workflow_step_completed_materializes_and_starts_conditional_todo(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_conditional_complete.db")
    _create_task(storage)
    storage.create_ai_search_plan(_plan_record("task-gap", include_conditional=True))
    run_id = _create_run(storage, "task-gap", plan_version=1, phase="execute_search")
    storage.replace_ai_search_retrieval_todos(
        run_id,
        "task-gap",
        1,
        [
            {
                "todo_id": "plan_1:sub_plan_1:step_1",
                "sub_plan_id": "sub_plan_1",
                "step_id": "step_1",
                "phase_key": "execute_search",
                "title": "执行步骤 1",
                "description": "目的：执行首轮宽召回",
                "status": "in_progress",
            }
        ],
    )
    storage.update_ai_search_run("task-gap", run_id, active_retrieval_todo_id="plan_1:sub_plan_1:step_1")
    storage.create_ai_search_execution_summary(
        {
            "summary_id": "summary-step-1",
            "run_id": run_id,
            "task_id": "task-gap",
            "plan_version": 1,
            "todo_id": "plan_1:sub_plan_1:step_1",
            "sub_plan_id": "sub_plan_1",
            "step_id": "step_1",
            "metadata": {
                "outcome_signals": {
                    "primary_goal_reached": True,
                    "recall_quality": "balanced",
                    "triggered_by_adjustment": False,
                }
            },
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    advance_workflow = next(
        tool for tool in context.build_main_agent_tools() if str(getattr(tool, "__name__", "")) == "advance_workflow"
    )
    payload = json.loads(advance_workflow(action="step_completed", plan_version=1))
    todos = {item["todo_id"]: item for item in context._current_todos()}

    assert payload["activated_todo_ids"] == ["plan_1:sub_plan_1:step_2"]
    assert todos["plan_1:sub_plan_1:step_1"]["status"] == "completed"
    assert todos["plan_1:sub_plan_1:step_2"]["status"] == "in_progress"
    assert context.current_todo()["todo_id"] == "plan_1:sub_plan_1:step_2"
