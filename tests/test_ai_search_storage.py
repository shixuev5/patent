from __future__ import annotations

import sqlite3
from datetime import datetime

from backend.storage import Task, TaskStatus, TaskType
from backend.storage.ai_search_support import encode_typed_value
from backend.storage.sqlite_storage import SQLiteTaskStorage


def test_ai_search_storage_roundtrip(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_storage.db")
    now = datetime.now()
    storage.create_task(
        Task(
            id="task-ai-search",
            owner_id="guest:search-user",
            task_type=TaskType.AI_SEARCH.value,
            status=TaskStatus.PROCESSING,
            created_at=now,
            updated_at=now,
        )
    )

    assert storage.create_ai_search_message(
        {
            "message_id": "msg-1",
            "task_id": "task-ai-search",
            "plan_version": 1,
            "role": "assistant",
            "kind": "chat",
            "content": "初始说明",
            "metadata": {"phase": "collecting_requirements"},
        }
    )
    messages = storage.list_ai_search_messages("task-ai-search")
    assert len(messages) == 1
    assert messages[0]["metadata"]["phase"] == "collecting_requirements"

    assert storage.create_ai_search_plan(
        {
            "task_id": "task-ai-search",
            "plan_version": 1,
            "status": "draft",
            "review_markdown": "# 检索计划\n\n## 检索目标\n检索新能源控制方法",
            "execution_spec_json": {
                "search_scope": {"objective": "检索新能源控制方法"},
                "constraints": {},
                "execution_policy": {"dynamic_replanning": True, "planner_visibility": "summary_only", "max_rounds": 3},
                "sub_plans": [
                    {
                        "sub_plan_id": "sub_plan_1",
                        "title": "新能源控制",
                        "goal": "检索新能源控制方法",
                        "semantic_query_text": "",
                        "search_elements": [],
                        "retrieval_steps": [
                            {
                                "step_id": "step_1",
                                "title": "新能源控制 / 首轮宽召回",
                                "purpose": "执行首轮宽召回",
                                "feature_combination": "新能源控制核心特征",
                                "language_strategy": "中文优先，必要时补英文",
                                "ipc_cpc_mode": "按需补充 IPC/CPC",
                                "ipc_cpc_codes": [],
                                "expected_recall": "获取首轮候选池",
                                "fallback_action": "结果异常时调整同义词与分类号",
                                "query_blueprint_refs": ["b1"],
                                "phase_key": "execute_search",
                            }
                        ],
                        "query_blueprints": [{"batch_id": "b1", "goal": "检索新能源控制方法", "sub_plan_id": "sub_plan_1"}],
                        "classification_hints": [],
                    }
                ],
            },
        }
    )
    assert storage.update_ai_search_plan("task-ai-search", 1, status="confirmed", confirmed_at="2026-04-04T00:00:00Z")
    plan = storage.get_ai_search_plan("task-ai-search", 1)
    assert plan is not None
    assert plan["status"] == "confirmed"
    assert plan["execution_spec_json"]["sub_plans"][0]["query_blueprints"][0]["batch_id"] == "b1"

    assert storage.create_ai_search_run(
        {
            "run_id": "run-1",
            "task_id": "task-ai-search",
            "plan_version": 1,
            "phase": "execute_search",
            "status": "processing",
        }
    )

    assert storage.upsert_ai_search_documents(
        [
            {
                "run_id": "run-1",
                "document_id": "doc-1",
                "task_id": "task-ai-search",
                "plan_version": 1,
                "pn": "CN123456A",
                "title": "一种控制方法",
                "abstract": "摘要",
                "ipc_cpc_json": ["G06F"],
                "publication_date": "20240102",
                "application_date": "20230102",
                "primary_ipc": "G06F 9/00",
                "document_type": "Y",
                "claim_ids_json": ["1", "3"],
                "evidence_locations_json": ["paragraph_0001", "figure_2"],
                "evidence_summary": "说明书第01段；figure_2",
                "report_row_order": 2,
                "source_batches_json": ["b1"],
                "source_sub_plans_json": ["sub_plan_1"],
                "stage": "candidate",
                "score": 0.9,
            }
        ]
    ) >= 1
    assert storage.update_ai_search_document(
        "task-ai-search",
        1,
        "doc-1",
        stage="selected",
        user_pinned=True,
        key_passages_json=[{"passage": "关键段落"}],
    )
    documents = storage.list_ai_search_documents("task-ai-search", 1)
    assert len(documents) == 1
    assert documents[0]["stage"] == "selected"
    assert documents[0]["user_pinned"] is True
    assert documents[0]["source_sub_plans_json"] == ["sub_plan_1"]
    assert documents[0]["source_steps_json"] == []
    assert documents[0]["key_passages_json"][0]["passage"] == "关键段落"
    assert documents[0]["publication_date"] == "20240102"
    assert documents[0]["application_date"] == "20230102"
    assert documents[0]["primary_ipc"] == "G06F 9/00"
    assert documents[0]["document_type"] == "Y"
    assert documents[0]["claim_ids_json"] == ["1", "3"]
    assert documents[0]["evidence_locations_json"] == ["paragraph_0001", "figure_2"]
    assert documents[0]["evidence_summary"] == "说明书第01段；figure_2"
    assert documents[0]["report_row_order"] == 2

    assert storage.create_ai_search_batch(
        {
            "batch_id": "batch-feature-1",
            "run_id": "run-1",
            "task_id": "task-ai-search",
            "plan_version": 1,
            "batch_type": "feature_compare",
            "status": "loaded",
        }
    )
    assert storage.create_ai_search_feature_comparison(
        {
            "feature_comparison_id": "ft-1",
            "run_id": "run-1",
            "batch_id": "batch-feature-1",
            "task_id": "task-ai-search",
            "plan_version": 1,
            "table_json": [{"feature": "A", "doc": "CN123456A"}],
            "summary_markdown": "总结",
        }
    )
    feature_comparison = storage.get_ai_search_feature_comparison("task-ai-search", 1)
    assert feature_comparison is not None
    assert feature_comparison["table_json"][0]["feature"] == "A"
    assert feature_comparison["summary_markdown"] == "总结"

    assert storage.create_ai_search_execution_summary(
        {
            "summary_id": "summary-1",
            "run_id": "run-1",
            "task_id": "task-ai-search",
            "plan_version": 1,
            "todo_id": "plan_1:sub_plan_1:step_1",
            "step_id": "step_1",
            "sub_plan_id": "sub_plan_1",
            "result_summary": "首轮召回有效",
            "metadata": {
                "outcome_signals": {
                    "primary_goal_reached": True,
                    "recall_quality": "balanced",
                    "triggered_by_adjustment": False,
                }
            },
        }
    )
    summaries = storage.list_ai_search_execution_summaries("run-1")
    assert summaries[0]["outcome_signals"]["primary_goal_reached"] is True
    assert summaries[0]["metadata"]["outcome_signals"]["recall_quality"] == "balanced"

    checkpoint_json = encode_typed_value(("json", b'{"id":"cp-1","channel_versions":{"messages":1}}'))
    metadata_json = encode_typed_value(("json", b'{"source":"test"}'))
    assert storage.put_ai_search_checkpoint(
        {
            "thread_id": "thread-1",
            "checkpoint_ns": "ai_search_main",
            "checkpoint_id": "cp-1",
            "checkpoint_json": checkpoint_json,
            "metadata_json": metadata_json,
        }
    )
    assert storage.put_ai_search_checkpoint_blobs(
        [
            {
                "thread_id": "thread-1",
                "checkpoint_ns": "ai_search_main",
                "channel": "messages",
                "version": "1",
                "typed_value_json": encode_typed_value(("json", b'["hello"]')),
            }
        ]
    ) >= 1
    assert storage.put_ai_search_checkpoint_writes(
        [
            {
                "thread_id": "thread-1",
                "checkpoint_ns": "ai_search_main",
                "checkpoint_id": "cp-1",
                "task_id": "main-agent",
                "write_idx": 0,
                "channel": "messages",
                "typed_value_json": encode_typed_value(("json", b'"delta"')),
                "task_path": "",
            }
        ]
    ) >= 1
    checkpoint = storage.get_ai_search_checkpoint("thread-1", "ai_search_main", "cp-1")
    assert checkpoint is not None
    assert checkpoint["checkpoint_id"] == "cp-1"
    assert storage.get_ai_search_checkpoint_blobs("thread-1", "ai_search_main", {"messages": 1})["messages"]
    writes = storage.list_ai_search_checkpoint_writes("thread-1", "ai_search_main", "cp-1")
    assert len(writes) == 1
    assert writes[0]["task_id"] == "main-agent"


def test_ai_search_storage_migrates_old_document_conflict_index(tmp_path):
    db_path = tmp_path / "ai_search_storage_old_docs.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE ai_search_documents (
            document_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            plan_version INTEGER NOT NULL,
            pn TEXT,
            title TEXT,
            abstract TEXT,
            publication_date TEXT,
            application_date TEXT,
            primary_ipc TEXT,
            document_type TEXT,
            claim_ids_json TEXT,
            evidence_locations_json TEXT,
            evidence_summary TEXT,
            report_row_order INTEGER,
            ipc_cpc_json TEXT,
            source_batches_json TEXT,
            source_lanes_json TEXT,
            source_sub_plans_json TEXT,
            source_steps_json TEXT,
            stage TEXT NOT NULL,
            score REAL,
            agent_reason TEXT,
            key_passages_json TEXT,
            user_pinned INTEGER NOT NULL DEFAULT 0,
            user_removed INTEGER NOT NULL DEFAULT 0,
            coarse_status TEXT NOT NULL DEFAULT 'pending',
            coarse_reason TEXT,
            coarse_screened_at TEXT,
            close_read_status TEXT NOT NULL DEFAULT 'pending',
            close_read_reason TEXT,
            close_read_at TEXT,
            detail_fingerprint TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            run_id TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    storage = SQLiteTaskStorage(db_path)
    now = datetime.now()
    storage.create_task(
        Task(
            id="task-ai-search-migration",
            owner_id="guest:search-user",
            task_type=TaskType.AI_SEARCH.value,
            status=TaskStatus.PROCESSING,
            created_at=now,
            updated_at=now,
        )
    )
    storage.create_ai_search_run(
        {
            "run_id": "run-migration-1",
            "task_id": "task-ai-search-migration",
            "plan_version": 1,
            "phase": "execute_search",
            "status": "processing",
        }
    )

    changed = storage.upsert_ai_search_documents(
        [
            {
                "run_id": "run-migration-1",
                "document_id": "doc-migration-1",
                "task_id": "task-ai-search-migration",
                "plan_version": 1,
                "pn": "CN999999A",
                "title": "旧库迁移文献",
                "stage": "candidate",
            }
        ]
    )

    assert changed == 1

    raw_conn = sqlite3.connect(db_path)
    indexes = raw_conn.execute("PRAGMA index_list(ai_search_documents)").fetchall()
    raw_conn.close()
    assert any(row[1] == "idx_ai_search_documents_run_document" for row in indexes)
