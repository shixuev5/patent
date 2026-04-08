from __future__ import annotations

import json
from datetime import datetime

from agents.ai_search.src.context import AiSearchAgentContext
from backend.storage import Task, TaskStatus, TaskType
from backend.storage.sqlite_storage import SQLiteTaskStorage


def _plan_record(task_id: str, *, plan_version: int = 1, status: str = "confirmed") -> dict:
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
                    "retrieval_steps": [
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
                        }
                    ],
                    "query_blueprints": [{"batch_id": "b1", "goal": "检索目标", "sub_plan_id": "sub_plan_1"}],
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


def test_get_gap_context_reads_latest_close_read_and_feature_compare_results(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_context.db")
    _create_task(storage)
    storage.create_ai_search_message(
        {
            "message_id": "msg-close-read",
            "task_id": "task-gap",
            "role": "assistant",
            "kind": "close_read_result",
            "content": "仍有 limitation 未覆盖",
            "metadata": {
                "limitation_gaps": [{"claim_id": "1", "limitation_id": "1-L2", "gap_summary": "缺少约束条件"}],
                "follow_up_hints": ["补搜约束条件相关实现"],
            },
        }
    )
    storage.create_ai_search_message(
        {
            "message_id": "msg-feature",
            "task_id": "task-gap",
            "role": "assistant",
            "kind": "feature_compare_result",
            "content": "还不能完成创造性评价",
            "metadata": {
                "coverage_gaps": [{"claim_id": "1", "limitation_id": "1-L2", "gap_type": "missing_support"}],
                "creativity_readiness": "needs_more_evidence",
            },
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    get_gap_context = next(
        tool for tool in context.build_main_agent_tools() if str(getattr(tool, "__name__", "")) == "get_gap_context"
    )

    payload = json.loads(get_gap_context())

    assert payload["close_read_result"]["limitation_gaps"][0]["limitation_id"] == "1-L2"
    assert payload["feature_compare_result"]["creativity_readiness"] == "needs_more_evidence"


def test_evaluate_gap_progress_recommends_replan_when_gaps_remain(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_eval.db")
    _create_task(storage)
    storage.create_ai_search_message(
        {
            "message_id": "msg-close-read",
            "task_id": "task-gap",
            "role": "assistant",
            "kind": "close_read_result",
            "content": "仍有 gap",
            "metadata": {
                "limitation_gaps": [{"claim_id": "1", "limitation_id": "1-L2", "gap_summary": "缺少约束条件"}],
                "follow_up_hints": ["补搜约束条件相关实现"],
            },
        }
    )
    storage.create_ai_search_message(
        {
            "message_id": "msg-feature",
            "task_id": "task-gap",
            "role": "assistant",
            "kind": "feature_compare_result",
            "content": "还不能完成创造性评价",
            "metadata": {
                "coverage_gaps": [{"claim_id": "1", "limitation_id": "1-L2", "gap_type": "missing_support"}],
                "creativity_readiness": "needs_more_evidence",
            },
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    evaluate_gap_progress = next(
        tool for tool in context.build_main_agent_tools() if str(getattr(tool, "__name__", "")) == "evaluate_gap_progress"
    )

    payload = json.loads(evaluate_gap_progress())

    assert payload["should_continue_search"] is True
    assert payload["recommended_action"] == "replan_search"
    assert payload["coverage_gap_count"] == 1


def test_build_execution_step_directive_includes_gap_context(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_directive.db")
    _create_task(storage)
    storage.create_ai_search_plan(
        _plan_record("task-gap")
    )
    storage.update_task(
        "task-gap",
        metadata={"ai_search": {"current_phase": "execute_search", "active_plan_version": 1}},
    )
    storage.update_task(
        "task-gap",
        metadata={"ai_search": {"current_phase": "execute_search", "active_plan_version": 1, "current_task": "plan_1:sub_plan_1:step_1", "todos": [{"todo_id": "plan_1:sub_plan_1:step_1", "sub_plan_id": "sub_plan_1", "step_id": "step_1", "phase_key": "execute_search", "title": "子计划 1 / 首轮宽召回", "description": "目的：执行首轮宽召回", "status": "in_progress"}]}},
    )
    storage.create_ai_search_message(
        {
            "message_id": "msg-feature",
            "task_id": "task-gap",
            "role": "assistant",
            "kind": "feature_compare_result",
            "content": "还需要补证据",
            "metadata": {
                "coverage_gaps": [{"claim_id": "1", "limitation_id": "1-L3", "gap_type": "combination_gap"}],
                "follow_up_search_hints": ["补搜实现方式B"],
                "creativity_readiness": "needs_more_evidence",
            },
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    directive = context.build_execution_step_directive(1)

    assert directive["gap_context"]["feature_compare_result"]["creativity_readiness"] == "needs_more_evidence"
    assert directive["gap_context"]["feature_compare_result"]["coverage_gaps"][0]["gap_type"] == "combination_gap"
    assert directive["current_todo"]["todo_id"] == "plan_1:sub_plan_1:step_1"


def test_build_gap_strategy_seed_payload_extracts_targeted_gaps(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_seed.db")
    _create_task(storage)
    storage.create_ai_search_message(
        {
            "message_id": "msg-close-read",
            "task_id": "task-gap",
            "role": "assistant",
            "kind": "close_read_result",
            "content": "存在 limitation gap",
            "metadata": {
                "limitation_gaps": [
                    {
                        "claim_id": "1",
                        "limitation_id": "1-L2",
                        "gap_type": "missing_support",
                        "gap_summary": "缺少约束条件",
                        "suggested_keywords": ["约束条件", "参数窗口"],
                    }
                ],
                "follow_up_hints": ["补搜参数窗口实现"],
            },
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
    storage.update_task(
        "task-gap",
        metadata={
            "ai_search": {
                "current_phase": "feature_comparison",
                "active_plan_version": 1,
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
            }
        },
    )
    storage.upsert_ai_search_documents(
        [
            {
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
    storage.create_ai_search_message(
        {
            "message_id": "msg-step-summary",
            "task_id": "task-gap",
            "plan_version": 1,
            "role": "assistant",
            "kind": "execution_step_summary",
            "content": "{}",
            "metadata": {
                "todo_id": "plan_1:sub_plan_1:step_1",
                "sub_plan_id": "sub_plan_1",
                "step_id": "step_1",
                "new_unique_candidates": 0,
                "candidate_pool_size": 1,
            },
        }
    )
    storage.create_ai_search_message(
        {
            "message_id": "msg-feature",
            "task_id": "task-gap",
            "role": "assistant",
            "kind": "feature_compare_result",
            "content": "还需要更多证据",
            "metadata": {
                "coverage_gaps": [{"claim_id": "1", "limitation_id": "1-L3", "gap_type": "combination_gap"}],
                "creativity_readiness": "needs_more_evidence",
            },
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    payload = context.evaluate_exhaustion_payload(1)

    assert payload["is_no_progress"] is True
    assert payload["no_progress_round_count"] == 2
    assert payload["triggered_limit"] == "max_no_progress_rounds"
    assert payload["should_request_decision"] is True


def test_complete_execution_is_blocked_when_gap_replan_is_required(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_block.db")
    _create_task(storage)
    storage.update_task(
        "task-gap",
        metadata={"ai_search": {"current_phase": "feature_comparison", "active_plan_version": 1}},
    )
    storage.create_ai_search_message(
        {
            "message_id": "msg-feature",
            "task_id": "task-gap",
            "role": "assistant",
            "kind": "feature_compare_result",
            "content": "还需要更多证据",
            "metadata": {
                "coverage_gaps": [{"claim_id": "1", "limitation_id": "1-L3", "gap_type": "combination_gap"}],
                "creativity_readiness": "needs_more_evidence",
            },
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    complete_execution = next(
        tool for tool in context.build_main_agent_tools() if str(getattr(tool, "__name__", "")) == "complete_execution"
    )

    payload = json.loads(complete_execution(plan_version=1))

    assert payload["blocked"] is True
    assert payload["reason"] == "gap_replan_required"
    assert payload["recommended_action"] == "replan_search"


def test_run_feature_compare_commit_persists_feature_compare_result_message(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_feature.db")
    _create_task(storage)

    context = AiSearchAgentContext(storage, "task-gap")
    run_feature_compare = next(
        tool for tool in context.build_feature_comparer_tools() if str(getattr(tool, "__name__", "")) == "run_feature_compare"
    )

    run_feature_compare(
        operation="commit",
        payload_json=json.dumps(
            {
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
    feature_messages = [item for item in messages if str(item.get("kind") or "") == "feature_compare_result"]

    assert len(feature_messages) == 1
    assert feature_messages[0]["metadata"]["creativity_readiness"] == "needs_more_evidence"
    assert feature_messages[0]["metadata"]["coverage_gaps"][0]["gap_type"] == "combination_gap"


def test_begin_execution_sets_resume_metadata_on_todo(tmp_path):
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
                "todos": [
                    {"todo_id": "plan_1:sub_plan_1:step_1", "sub_plan_id": "sub_plan_1", "step_id": "step_1", "phase_key": "execute_search", "title": "执行步骤 1", "description": "目的：执行首轮宽召回", "status": "pending"},
                    {"todo_id": "plan_1:sub_plan_1:step_2", "sub_plan_id": "sub_plan_1", "step_id": "step_2", "phase_key": "execute_search", "title": "执行步骤 2", "description": "目的：收窄检索", "status": "pending"},
                ],
            }
        },
    )

    context = AiSearchAgentContext(storage, "task-gap")
    begin_execution = next(
        tool for tool in context.build_main_agent_tools() if str(getattr(tool, "__name__", "")) == "begin_execution"
    )
    read_todos = next(tool for tool in context.build_main_agent_tools() if str(getattr(tool, "__name__", "")) == "read_todos")

    begin_execution(plan_version=1)
    payload = json.loads(read_todos())
    execute_todo = next(item for item in payload["todos"] if item["todo_id"] == "plan_1:sub_plan_1:step_1")

    assert execute_todo["status"] == "in_progress"
    assert execute_todo["resume_from"] == "run_execution_step.load"
    assert execute_todo["attempt_count"] == 1
    assert execute_todo["started_at"]


def test_run_execution_step_commit_persists_step_summary(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_round_dedupe.db")
    _create_task(storage)
    storage.create_ai_search_plan(
        _plan_record("task-gap")
    )
    storage.update_task(
        "task-gap",
        metadata={
            "ai_search": {
                "current_phase": "execute_search",
                "active_plan_version": 1,
                "current_task": "plan_1:sub_plan_1:step_1",
                "todos": [{"todo_id": "plan_1:sub_plan_1:step_1", "sub_plan_id": "sub_plan_1", "step_id": "step_1", "phase_key": "execute_search", "title": "执行步骤 1", "description": "目的：执行首轮宽召回", "status": "in_progress"}],
            }
        },
    )

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
    summaries = [item for item in storage.list_ai_search_messages("task-gap") if str(item.get("kind") or "") == "execution_step_summary"]
    assert result == "execution step summary saved"
    assert len(summaries) == 1
    assert summaries[0]["metadata"]["todo_id"] == "plan_1:sub_plan_1:step_1"
