from __future__ import annotations

import json
from datetime import datetime

from agents.ai_search.src.context import AiSearchAgentContext
from backend.storage import Task, TaskStatus, TaskType
from backend.storage.sqlite_storage import SQLiteTaskStorage


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
            metadata={"ai_search": {"current_phase": "generate_feature_table"}},
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
    assert payload["recommended_action"] == "replan_search_strategy"
    assert payload["coverage_gap_count"] == 1


def test_build_execution_directive_includes_gap_context(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_directive.db")
    _create_task(storage)
    storage.create_ai_search_plan(
        {
            "task_id": "task-gap",
            "plan_version": 1,
            "status": "confirmed",
            "objective": "检索目标",
            "search_elements_json": {"status": "complete", "search_elements": []},
            "plan_json": {"plan_version": 1, "query_batches": [{"batch_id": "b1"}]},
        }
    )
    storage.update_task(
        "task-gap",
        metadata={"ai_search": {"current_phase": "execute_search", "active_plan_version": 1}},
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
    directive = context.build_execution_directive(1)

    assert directive["gap_context"]["feature_compare_result"]["creativity_readiness"] == "needs_more_evidence"
    assert directive["gap_context"]["feature_compare_result"]["coverage_gaps"][0]["gap_type"] == "combination_gap"


def test_build_gap_strategy_seed_extracts_targeted_gaps(tmp_path):
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
    build_gap_strategy_seed = next(
        tool for tool in context.build_claim_search_strategist_tools() if str(getattr(tool, "__name__", "")) == "build_gap_strategy_seed"
    )

    payload = json.loads(build_gap_strategy_seed())

    assert payload["planning_mode"] == "gap_replan"
    assert payload["targeted_gaps"][0]["limitation_id"] == "1-L2"
    assert payload["seed_batch_specs"][0]["seed_terms"] == ["约束条件", "参数窗口"]


def test_save_claim_search_strategy_backfills_seed_fields(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_strategy_save.db")
    _create_task(storage)
    storage.create_ai_search_message(
        {
            "message_id": "msg-feature",
            "task_id": "task-gap",
            "role": "assistant",
            "kind": "feature_compare_result",
            "content": "还需补搜",
            "metadata": {
                "coverage_gaps": [
                    {
                        "claim_id": "1",
                        "limitation_id": "1-L3",
                        "gap_type": "combination_gap",
                        "gap_summary": "需要补一篇组合文献",
                    }
                ],
                "follow_up_search_hints": ["补搜实现方式B"],
                "creativity_readiness": "needs_more_evidence",
            },
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    save_claim_search_strategy = next(
        tool for tool in context.build_claim_search_strategist_tools() if str(getattr(tool, "__name__", "")) == "save_claim_search_strategy"
    )

    save_claim_search_strategy(json.dumps({"strategy_summary": "按 gap 重规划"}, ensure_ascii=False))

    messages = storage.list_ai_search_messages("task-gap")
    strategy_messages = [item for item in messages if str(item.get("kind") or "") == "claim_search_strategy"]

    assert len(strategy_messages) == 1
    metadata = strategy_messages[0]["metadata"]
    assert metadata["planning_mode"] == "gap_replan"
    assert metadata["targeted_gaps"][0]["gap_type"] == "combination_gap"
    assert metadata["batch_specs"][0]["batch_id"] == "gap-1"


def test_complete_execution_is_blocked_when_gap_replan_is_required(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_gap_block.db")
    _create_task(storage)
    storage.update_task(
        "task-gap",
        metadata={"ai_search": {"current_phase": "generate_feature_table", "active_plan_version": 1}},
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
    assert payload["recommended_action"] == "replan_search_strategy"


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
        {
            "task_id": "task-gap",
            "plan_version": 1,
            "status": "confirmed",
            "objective": "检索目标",
            "search_elements_json": {"status": "complete", "search_elements": []},
            "plan_json": {"plan_version": 1, "query_batches": [{"batch_id": "b1"}]},
        }
    )
    storage.update_task(
        "task-gap",
        metadata={
            "ai_search": {
                "current_phase": "drafting_plan",
                "active_plan_version": 1,
                "todos": [
                    {"key": "execute_search", "title": "执行检索召回", "status": "pending"},
                    {"key": "coarse_screen", "title": "粗筛候选文献", "status": "pending"},
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
    execute_todo = next(item for item in payload["todos"] if item["key"] == "execute_search")

    assert execute_todo["status"] == "in_progress"
    assert execute_todo["resume_from"] == "run_search_round.load"
    assert execute_todo["attempt_count"] == 1
    assert execute_todo["started_at"]


def test_run_search_round_commit_dedupes_existing_round_id(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_round_dedupe.db")
    _create_task(storage)
    storage.create_ai_search_plan(
        {
            "task_id": "task-gap",
            "plan_version": 1,
            "status": "confirmed",
            "objective": "检索目标",
            "search_elements_json": {"status": "complete", "search_elements": []},
            "plan_json": {"plan_version": 1, "query_batches": [{"batch_id": "b1"}]},
        }
    )
    storage.update_task(
        "task-gap",
        metadata={
            "ai_search": {
                "current_phase": "execute_search",
                "active_plan_version": 1,
                "current_task": "execute_search",
                "todos": [{"key": "execute_search", "title": "执行检索召回", "status": "in_progress"}],
            }
        },
    )
    storage.create_ai_search_message(
        {
            "message_id": "msg-round-1",
            "task_id": "task-gap",
            "plan_version": 1,
            "role": "assistant",
            "kind": "execution_summary",
            "content": "{}",
            "metadata": {"round_id": "round-1", "new_unique_candidates": 1, "candidate_pool_size": 1},
        }
    )

    context = AiSearchAgentContext(storage, "task-gap")
    run_search_round = next(
        tool for tool in context.build_query_executor_tools() if str(getattr(tool, "__name__", "")) == "run_search_round"
    )

    result = json.loads(
        run_search_round(
            operation="commit",
            payload_json=json.dumps({"round_id": "round-1", "new_unique_candidates": 2, "candidate_pool_size": 3}, ensure_ascii=False),
            plan_version=1,
        )
    )

    summaries = [item for item in storage.list_ai_search_messages("task-gap") if str(item.get("kind") or "") == "execution_summary"]
    assert result["deduped"] is True
    assert len(summaries) == 1
