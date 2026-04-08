from __future__ import annotations

import json

from agents.ai_search.src.subagents.query_executor import build_search_tools


class _StubStorage:
    def list_ai_search_documents(self, _task_id: str, _plan_version: int):
        return []


class _StubContext:
    def __init__(self):
        self.storage = _StubStorage()
        self.task_id = "task-search-tools"


def test_prepare_lane_queries_includes_gap_seed_fields():
    tools = build_search_tools(_StubContext())
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
