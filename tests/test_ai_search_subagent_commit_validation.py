from __future__ import annotations

import json
from datetime import datetime

from agents.ai_search.src.context import AiSearchAgentContext
from backend.storage import Task, TaskStatus, TaskType
from backend.storage.sqlite_storage import SQLiteTaskStorage


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
    tool = next(tool for tool in context.build_coarse_screener_tools() if tool.__name__ == "run_coarse_screen_batch")

    result = json.loads(
        tool(
            operation="commit",
            plan_version=1,
            payload_json=json.dumps({"keep": ["doc-1"], "discard": []}, ensure_ascii=False),
        )
    )

    assert result["ok"] is False
    assert "遗漏了待处理 document_id" in result["error"]
    assert "doc-2" in result["error"]


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
    tool = next(tool for tool in context.build_close_reader_tools() if tool.__name__ == "run_close_read_batch")

    result = json.loads(
        tool(
            operation="commit",
            plan_version=1,
            payload_json=json.dumps({"selected": ["doc-1"], "rejected": ["doc-1"]}, ensure_ascii=False),
        )
    )

    assert result["ok"] is False
    assert "存在重复 document_id" in result["error"]
    assert "doc-1" in result["error"]


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
    tool = next(tool for tool in context.build_close_reader_tools() if tool.__name__ == "run_close_read_batch")

    payload = {
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
                "decision": "selected",
                "confidence": 0.9,
                "evidence_sufficiency": "sufficient",
            }
        ],
        "coverage_summary": "覆盖充分",
        "follow_up_hints": [],
        "selection_summary": "选入对比文件",
    }

    result = json.loads(tool(operation="commit", plan_version=1, payload_json=json.dumps(payload, ensure_ascii=False)))
    documents = storage.list_ai_search_documents("task-align", 1, stages=["selected"])

    assert result["selected_count"] == 1
    assert documents[0]["claim_ids_json"] == ["1"]
    assert "paragraph_0003" in documents[0]["evidence_locations_json"]
    assert "paragraph_0005" in documents[0]["evidence_locations_json"]
