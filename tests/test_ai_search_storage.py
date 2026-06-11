from __future__ import annotations

from datetime import datetime

from backend.storage import SQLiteTaskStorage, Task, TaskStatus, TaskType
from patent_agents.ai_search.src.state import merge_ai_search_meta


def _table_names(storage: SQLiteTaskStorage) -> set[str]:
    rows = storage._fetchall("SELECT name FROM sqlite_master WHERE type = 'table'", [])
    return {str(row["name"]) for row in rows}


def test_ai_search_sqlite_schema_excludes_legacy_phase_tables(tmp_path) -> None:
    storage = SQLiteTaskStorage(tmp_path / "ai_search_schema.db")
    tables = _table_names(storage)

    assert "ai_search_messages" in tables
    assert "ai_search_runs" in tables
    assert "ai_search_documents" in tables
    assert "ai_search_stream_events" in tables
    assert "ai_search_pending_actions" not in tables
    assert "ai_search_retrieval_todos" not in tables
    assert "ai_search_batches" not in tables
    assert "ai_search_execution_message_queue" not in tables
    assert "ai_search_feature_compare_results" not in tables
    assert not hasattr(storage, "create_ai_search_pending_action")
    assert not hasattr(storage, "create_ai_search_feature_comparison")


def test_ai_search_storage_roundtrip_for_agent_run_and_documents(tmp_path) -> None:
    storage = SQLiteTaskStorage(tmp_path / "ai_search_roundtrip.db")
    now = datetime.now()
    storage.create_task(
        Task(
            id="task-ai-search",
            owner_id="guest:search-user",
            task_type=TaskType.AI_SEARCH.value,
            status=TaskStatus.PROCESSING,
            created_at=now,
            updated_at=now,
            metadata=merge_ai_search_meta(None, active_plan_version=1),
        )
    )
    assert storage.create_ai_search_run(
        {
            "run_id": "run-1",
            "task_id": "task-ai-search",
            "plan_version": 1,
            "phase": "running",
            "status": "processing",
        }
    )
    assert storage.update_ai_search_run("task-ai-search", "run-1", phase="idle", selected_document_count=1)
    storage.upsert_ai_search_documents(
        [
            {
                "run_id": "run-1",
                "task_id": "task-ai-search",
                "plan_version": 1,
                "document_id": "doc-1",
                "source_type": "patent",
                "canonical_id": "patent:CN123",
                "pn": "CN123",
                "title": "检索结果",
                "stage": "selected",
                "score": 0.8,
                "key_passages_json": [{"text": "关键段落"}],
            }
        ]
    )

    run = storage.get_ai_search_run("task-ai-search", "run-1")
    docs = storage.list_ai_search_documents("task-ai-search", 1, stages=["selected"])

    assert run["phase"] == "idle"
    assert run["selected_document_count"] == 1
    assert docs[0]["document_id"] == "doc-1"
    assert docs[0]["key_passages_json"] == [{"text": "关键段落"}]
