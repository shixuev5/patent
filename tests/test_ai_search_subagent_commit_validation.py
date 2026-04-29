from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime_context import build_runtime_context
from backend.storage import Task, TaskStatus, TaskType
from backend.storage import SQLiteTaskStorage


def _create_task(storage: SQLiteTaskStorage, task_id: str, phase: str, *, active_plan_version: int = 1) -> None:
    now = datetime.now()
    storage.create_task(
        Task(
            id=task_id,
            owner_id="guest:ai-search-user",
            task_type=TaskType.AI_SEARCH.value,
            status=TaskStatus.PROCESSING,
            created_at=now,
            updated_at=now,
            metadata={"ai_search": {"current_phase": phase, "active_plan_version": active_plan_version}},
        )
    )
    storage.create_ai_search_run(
        {
            "run_id": f"{task_id}-run-{active_plan_version}",
            "task_id": task_id,
            "plan_version": active_plan_version,
            "phase": phase,
            "status": TaskStatus.PROCESSING.value,
        }
    )


def _create_batch(
    storage: SQLiteTaskStorage,
    task_id: str,
    *,
    plan_version: int = 1,
    batch_type: str,
    document_ids: list[str],
) -> str:
    run_id = f"{task_id}-run-{plan_version}"
    batch_id = f"{task_id}-{batch_type}-batch"
    storage.create_ai_search_batch(
        {
            "batch_id": batch_id,
            "run_id": run_id,
            "task_id": task_id,
            "plan_version": plan_version,
            "batch_type": batch_type,
            "status": "loaded",
        }
    )
    storage.replace_ai_search_batch_documents(batch_id, run_id, document_ids)
    storage.update_ai_search_run(task_id, run_id, active_batch_id=batch_id)
    return batch_id


def _runtime(context: AiSearchAgentContext) -> SimpleNamespace:
    return SimpleNamespace(context=build_runtime_context(context.storage, context.task_id))


def test_coarse_screen_commit_rejects_missing_pending_documents(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_coarse_validation.db")
    _create_task(storage, "task-coarse", "coarse_screen")
    storage.upsert_ai_search_documents(
        [
            {
                "document_id": "doc-1",
                "task_id": "task-coarse",
                "plan_version": 1,
                "pn": "CN1000001A",
                "title": "文献1",
                "stage": "candidate",
                "coarse_status": "pending",
            },
            {
                "document_id": "doc-2",
                "task_id": "task-coarse",
                "plan_version": 1,
                "pn": "CN1000002A",
                "title": "文献2",
                "stage": "candidate",
                "coarse_status": "pending",
            },
        ]
    )

    context = AiSearchAgentContext(storage, "task-coarse")
    runtime = _runtime(context)
    tool = next(tool for tool in context.build_coarse_screener_tools() if tool.__name__ == "run_coarse_screen_batch")
    _create_batch(storage, "task-coarse", batch_type="coarse_screen", document_ids=["doc-1", "doc-2"])
    try:
        tool(
            operation="commit",
            payload={"keep": ["doc-1"], "discard": []},
            plan_version=1,
            runtime=runtime,
        )
        raise AssertionError("expected validation failure")
    except ValueError as exc:
        assert "遗漏了待处理 document_id" in str(exc)
        assert "doc-2" in str(exc)


def test_close_read_commit_rejects_overlapping_document_ids(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_close_validation.db")
    _create_task(storage, "task-close", "close_read")
    storage.upsert_ai_search_documents(
        [
            {
                "document_id": "doc-1",
                "task_id": "task-close",
                "plan_version": 1,
                "pn": "CN2000001A",
                "title": "文献1",
                "stage": "shortlisted",
                "coarse_status": "kept",
                "close_read_status": "pending",
            }
        ]
    )

    context = AiSearchAgentContext(storage, "task-close")
    runtime = _runtime(context)
    tool = next(tool for tool in context.build_close_reader_tools() if tool.__name__ == "run_close_read_batch")
    _create_batch(storage, "task-close", batch_type="close_read", document_ids=["doc-1"])
    try:
        tool(
            operation="commit",
            payload={"selected": ["doc-1"], "rejected": ["doc-1"]},
            plan_version=1,
            runtime=runtime,
        )
        raise AssertionError("expected validation failure")
    except ValueError as exc:
        assert "存在重复 document_id" in str(exc)
        assert "doc-1" in str(exc)


def test_close_read_commit_uses_claim_alignments_for_claim_ids_and_locations(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_close_alignments.db")
    _create_task(storage, "task-align", "close_read")
    storage.upsert_ai_search_documents(
        [
            {
                "document_id": "doc-1",
                "task_id": "task-align",
                "plan_version": 1,
                "pn": "CN3000001A",
                "title": "文献1",
                "abstract": "摘要",
                "stage": "shortlisted",
                "coarse_status": "kept",
                "close_read_status": "pending",
            }
        ]
    )

    context = AiSearchAgentContext(storage, "task-align")
    runtime = _runtime(context)
    tool = next(tool for tool in context.build_close_reader_tools() if tool.__name__ == "run_close_read_batch")
    batch_id = _create_batch(storage, "task-align", batch_type="close_read", document_ids=["doc-1"])

    payload = {
        "batch_id": batch_id,
        "selected": ["doc-1"],
        "rejected": [],
        "key_passages": [
            {
                "document_id": "doc-1",
                "passage": "公开了参数窗口控制逻辑。",
                "reason": "对应核心区别特征",
                "location": "paragraph_0003",
            }
        ],
        "claim_alignments": [
            {
                "document_id": "doc-1",
                "claim_id": "1",
                "limitation_id": "1-L2",
                "passage": "公开了参数窗口控制逻辑。",
                "reason": "支持权利要求1的限制特征",
                "location": "paragraph_0005",
            }
        ],
        "limitation_coverage": [
            {
                "claim_id": "1",
                "limitation_id": "1-L2",
                "supporting_document_ids": ["doc-1"],
                "reason": "已有直接证据",
            }
        ],
        "limitation_gaps": [],
        "document_assessments": [
            {
                "document_id": "doc-1",
                "confidence": 0.9,
                "evidence_sufficiency": "sufficient",
            }
        ],
    }

    selected_count = json.loads(
        tool(
            operation="commit",
            payload=payload,
            plan_version=1,
            runtime=runtime,
        )
    )["selected_count"]
    documents = storage.list_ai_search_documents("task-align", 1, stages=["selected"])

    assert selected_count == 1
    assert documents[0]["claim_ids_json"] == ["1"]
    assert "paragraph_0003" in documents[0]["evidence_locations_json"]
    assert "paragraph_0005" in documents[0]["evidence_locations_json"]


def test_close_read_load_supports_abstract_only_npl_documents(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_close_npl.db")
    _create_task(storage, "task-close-npl", "close_read")
    storage.upsert_ai_search_documents(
        [
            {
                "document_id": "doc-npl-1",
                "task_id": "task-close-npl",
                "plan_version": 1,
                "source_type": "openalex",
                "canonical_id": "doi:10.1000/npl",
                "doi": "10.1000/npl",
                "title": "A paper",
                "abstract": "摘要级证据",
                "venue": "Science",
                "stage": "shortlisted",
                "coarse_status": "kept",
                "close_read_status": "pending",
                "detail_source": "abstract_only",
            }
        ]
    )

    context = AiSearchAgentContext(storage, "task-close-npl")
    runtime = _runtime(context)
    tool = next(tool for tool in context.build_close_reader_tools() if tool.__name__ == "run_close_read_batch")

    payload = json.loads(tool(operation="load", plan_version=1, runtime=runtime))

    assert payload["documents"][0]["source_type"] == "openalex"
    assert payload["documents"][0]["detail_source"] == "abstract_only"
    assert payload["documents"][0]["fulltext_path"].endswith("doc-npl-1.txt")


def test_feature_compare_commit_rejects_duplicate_batch_submission(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_feature_commit.db")
    _create_task(storage, "task-feature", "feature_comparison")
    storage.upsert_ai_search_documents(
        [
            {
                "document_id": "doc-1",
                "task_id": "task-feature",
                "plan_version": 1,
                "pn": "CN4000001A",
                "title": "文献1",
                "stage": "selected",
            }
        ]
    )

    context = AiSearchAgentContext(storage, "task-feature")
    runtime = _runtime(context)
    tool = next(tool for tool in context.build_feature_comparer_tools() if tool.__name__ == "run_feature_compare")
    json.loads(tool(operation="load", plan_version=1, runtime=runtime))
    json.loads(
        tool(
            operation="commit",
            payload={
                "table_rows": [{"feature": "A", "document_id": "doc-1"}],
                "document_roles": [{"document_id": "doc-1", "role": "X", "rationale": "直接公开"}],
                "coverage_gaps": [],
                "follow_up_search_hints": [],
                "creativity_readiness": "ready",
            },
            plan_version=1,
            runtime=runtime,
        )
    )

    try:
        tool(
            operation="commit",
            payload={
                "table_rows": [{"feature": "A", "document_id": "doc-1"}],
                "document_roles": [{"document_id": "doc-1", "role": "X", "rationale": "直接公开"}],
                "coverage_gaps": [],
                "follow_up_search_hints": [],
                "creativity_readiness": "ready",
            },
            plan_version=1,
            runtime=runtime,
        )
        raise AssertionError("expected duplicate commit failure")
    except ValueError as exc:
        assert "已提交" in str(exc)


def test_search_elements_save_rejects_invalid_status(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_search_elements_validation.db")
    _create_task(storage, "task-elements", "drafting_plan")
    context = AiSearchAgentContext(storage, "task-elements")
    runtime = _runtime(context)
    tool = next(tool for tool in context.build_search_elements_tools() if tool.__name__ == "save_search_elements")

    with pytest.raises(Exception) as exc_info:
        tool(
            payload={
                "status": "unknown",
                "objective": "检索异常检测方案",
                "search_elements": [{"element_name": "异常检测", "keywords_zh": ["异常检测"], "keywords_en": ["anomaly detection"]}],
            },
            runtime=runtime,
        )

    assert "status" in str(exc_info.value)


def test_query_executor_commit_rejects_invalid_recall_quality(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_execution_commit_validation.db")
    _create_task(storage, "task-exec", "execute_search")
    context = AiSearchAgentContext(storage, "task-exec")
    runtime = _runtime(context)
    tool = next(tool for tool in context.build_query_executor_tools() if tool.__name__ == "run_execution_step")

    with pytest.raises(Exception) as exc_info:
        tool(
            operation="commit",
            plan_version=1,
            payload={
                "todo_id": "plan_1:sub_plan_1:step_1",
                "step_id": "step_1",
                "sub_plan_id": "sub_plan_1",
                "candidate_pool_size": 10,
                "new_unique_candidates": 2,
                "outcome_signals": {
                    "primary_goal_reached": False,
                    "recall_quality": "wide",
                    "triggered_by_adjustment": False,
                },
                "plan_change_assessment": {"requires_replan": False},
            },
            runtime=runtime,
        )

    assert "recall_quality" in str(exc_info.value)


def test_query_executor_commit_rejects_removed_reason_codes_field(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_execution_reason_codes_validation.db")
    _create_task(storage, "task-exec-reason-codes", "execute_search")
    context = AiSearchAgentContext(storage, "task-exec-reason-codes")
    runtime = _runtime(context)
    tool = next(tool for tool in context.build_query_executor_tools() if tool.__name__ == "run_execution_step")

    with pytest.raises(Exception) as exc_info:
        tool(
            operation="commit",
            plan_version=1,
            payload={
                "todo_id": "plan_1:sub_plan_1:step_1",
                "step_id": "step_1",
                "sub_plan_id": "sub_plan_1",
                "candidate_pool_size": 10,
                "new_unique_candidates": 0,
                "outcome_signals": {
                    "primary_goal_reached": False,
                    "recall_quality": "balanced",
                    "triggered_by_adjustment": False,
                },
                "plan_change_assessment": {"requires_replan": False, "reason_codes": []},
            },
            runtime=runtime,
        )

    assert "reason_codes" in str(exc_info.value)


def test_close_reader_commit_rejects_removed_document_assessment_decision_field(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_close_reader_decision_validation.db")
    _create_task(storage, "task-close-decision", "close_read")
    storage.upsert_ai_search_documents(
        [
            {
                "document_id": "doc-1",
                "task_id": "task-close-decision",
                "plan_version": 1,
                "pn": "CN2000001A",
                "title": "文献1",
                "stage": "shortlisted",
                "coarse_status": "kept",
                "close_read_status": "pending",
            }
        ]
    )
    context = AiSearchAgentContext(storage, "task-close-decision")
    runtime = _runtime(context)
    tool = next(tool for tool in context.build_close_reader_tools() if tool.__name__ == "run_close_read_batch")
    _create_batch(storage, "task-close-decision", batch_type="close_read", document_ids=["doc-1"])

    with pytest.raises(Exception) as exc_info:
        tool(
            operation="commit",
            plan_version=1,
            payload={
                "selected": ["doc-1"],
                "rejected": [],
                "document_assessments": [
                    {
                        "document_id": "doc-1",
                        "decision": "selected",
                        "confidence": 0.9,
                        "evidence_sufficiency": "sufficient",
                    }
                ],
            },
            runtime=runtime,
        )

    assert "decision" in str(exc_info.value)


def test_feature_compare_commit_rejects_invalid_document_role(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_feature_role_validation.db")
    _create_task(storage, "task-feature-role", "feature_comparison")
    context = AiSearchAgentContext(storage, "task-feature-role")
    runtime = _runtime(context)
    tool = next(tool for tool in context.build_feature_comparer_tools() if tool.__name__ == "run_feature_compare")

    with pytest.raises(Exception) as exc_info:
        tool(
            operation="commit",
            plan_version=1,
            payload={
                "table_rows": [],
                "document_roles": [{"document_id": "doc-1", "role": "B", "rationale": "未知角色"}],
                "coverage_gaps": [],
                "follow_up_search_hints": [],
                "creativity_readiness": "ready",
            },
            runtime=runtime,
        )

    assert "role" in str(exc_info.value)


def test_feature_compare_commit_rejects_invalid_readiness(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_feature_readiness_validation.db")
    _create_task(storage, "task-feature-readiness", "feature_comparison")
    context = AiSearchAgentContext(storage, "task-feature-readiness")
    runtime = _runtime(context)
    tool = next(tool for tool in context.build_feature_comparer_tools() if tool.__name__ == "run_feature_compare")

    with pytest.raises(Exception) as exc_info:
        tool(
            operation="commit",
            plan_version=1,
            payload={
                "table_rows": [],
                "document_roles": [{"document_id": "doc-1", "role": "X", "rationale": "直接公开"}],
                "coverage_gaps": [],
                "follow_up_search_hints": [],
                "creativity_readiness": "unknown",
            },
            runtime=runtime,
        )

    assert "creativity_readiness" in str(exc_info.value)
