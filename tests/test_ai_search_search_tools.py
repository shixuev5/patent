from __future__ import annotations

import json
from types import SimpleNamespace

from agents.ai_search.src.context import AiSearchAgentContext
from agents.ai_search.src.runtime_context import build_runtime_context
from agents.ai_search.src.subagents.query_executor import build_search_tools
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage import SQLiteTaskStorage


class _StubStorage:
    def list_ai_search_documents(self, _task_id: str, _plan_version: int):
        return []


class _StubContext:
    def __init__(self):
        self.storage = _StubStorage()
        self.task_id = "task-search-tools"


def _runtime(context) -> SimpleNamespace:
    return SimpleNamespace(context=build_runtime_context(context.storage, context.task_id))


def _mount_context(tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_search_search_tools.db")
    manager = PipelineTaskManager(storage)
    task = manager.create_task(owner_id="guest_ai_search", task_type="ai_search", title="AI 检索会话")
    storage.create_ai_search_run(
        {
            "run_id": f"{task.id}-run-1",
            "task_id": task.id,
            "plan_version": 1,
            "phase": "execute_search",
            "status": "processing",
        }
    )
    return AiSearchAgentContext(storage, task.id), storage


def test_prepare_lane_queries_includes_gap_seed_fields():
    tools = build_search_tools()
    prepare_lane_queries = next(tool for tool in tools if str(getattr(tool, "__name__", "")) == "prepare_lane_queries")

    payload = json.loads(
        prepare_lane_queries(
            1,
            json.dumps(
                {
                    "batch_id": "gap-1",
                    "goal": "补强限制",
                    "gap_type": "missing_support",
                    "claim_id": "1",
                    "limitation_id": "1-L2",
                    "seed_terms": ["参数窗口", "约束条件"],
                    "pivot_terms": ["边缘端部署"],
                },
                ensure_ascii=False,
            ),
            json.dumps({"priority_date": "2023-10-15"}, ensure_ascii=False),
            "semantic",
        )
    )

    assert payload["gap_type"] == "missing_support"
    assert payload["claim_id"] == "1"
    assert payload["limitation_id"] == "1-L2"
    assert payload["seed_terms"] == ["参数窗口", "约束条件"]
    assert payload["pivot_terms"] == ["边缘端部署"]
    assert "参数窗口" in payload["query_text"]
    assert "目标限制：1 1-L2" in payload["semantic_text"]
    assert payload["academic_query_text"]
    assert payload["academic_semantic_text"]
    assert payload["crossref_query_text"]
    assert payload["cutoff_date"] == "2023-10-15"


def test_search_academic_openalex_persists_npl_and_dedupes_by_doi(tmp_path, monkeypatch):
    context, storage = _mount_context(tmp_path)
    tools = {getattr(tool, "__name__", ""): tool for tool in build_search_tools()}

    class _FakeAggregator:
        def search_openalex(self, **_kwargs):
            return [
                {
                    "source_type": "openalex",
                    "external_id": "W1",
                    "doi": "10.1000/test-doi",
                    "url": "https://example.com/paper-a",
                    "title": "Paper A",
                    "abstract": "test paper",
                    "snippet": "test paper",
                    "venue": "Nature",
                    "publication_date": "2023-10-01",
                    "published": "2023-10-01",
                    "language": "en",
                },
                {
                    "source_type": "openalex",
                    "external_id": "W2",
                    "doi": "10.1000/test-doi",
                    "url": "https://example.com/paper-a-dup",
                    "title": "Paper A duplicate",
                    "abstract": "duplicate",
                    "snippet": "duplicate",
                    "venue": "Nature",
                    "publication_date": "2023-10-02",
                    "published": "2023-10-02",
                    "language": "en",
                },
            ]

    monkeypatch.setattr(
        "agents.ai_search.src.subagents.query_executor.search_backend_tools._academic_aggregator",
        lambda: _FakeAggregator(),
    )

    payload = json.loads(
        tools["search_academic_openalex"](
            plan_version=1,
            batch_id="batch-openalex-1",
            query_text="battery thermal management",
            limit=5,
            runtime=_runtime(context),
        )
    )
    documents = storage.list_ai_search_documents(context.task_id, 1)

    assert payload["new_unique_candidates"] == 1
    assert len(documents) == 1
    assert documents[0]["source_type"] == "openalex"
    assert documents[0]["canonical_id"] == "doi:10.1000/test-doi"
    assert documents[0]["doi"] == "10.1000/test-doi"
    assert documents[0]["pn"] in {None, ""}
    assert documents[0]["detail_source"] == "abstract_only"
