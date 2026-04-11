import os

from agents.ai_reply.src.external_evidence import ExternalEvidenceAggregator
from agents.ai_reply.src.retrieval_utils import make_query_spec
from backend import task_usage_tracking


def test_external_evidence_workers_keep_task_usage_context(monkeypatch):
    for env_name in list(os.environ):
        if env_name.startswith("ZHIHUIYA_ACCOUNTS__"):
            monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv("ZHIHUIYA_USERNAME", raising=False)
    monkeypatch.delenv("ZHIHUIYA_PASSWORD", raising=False)

    aggregator = ExternalEvidenceAggregator()
    captured_context = {}

    def _fake_openalex(*args, **kwargs):
        captured_context.update(task_usage_tracking.get_current_task_usage_context())
        return [
            {
                "source_type": "openalex",
                "title": "doc",
                "url": "https://example.com/doc",
                "snippet": "snippet",
                "published": "2025-01-01",
            }
        ]

    monkeypatch.setattr(aggregator, "_search_openalex", _fake_openalex)
    monkeypatch.setattr(aggregator, "_search_semanticscholar", lambda *args, **kwargs: [])
    monkeypatch.setattr(aggregator, "_search_crossref", lambda *args, **kwargs: [])
    monkeypatch.setattr(aggregator, "_rerank_results", lambda candidates, queries_by_engine: candidates)

    collector = task_usage_tracking.create_task_usage_collector(
        task_id="task-oar-ctx",
        owner_id="authing:user-oar",
        task_type="ai_reply",
    )

    with task_usage_tracking.task_usage_collection(collector):
        evidence, engines, _ = aggregator.search_evidence(
            {"openalex": [make_query_spec("query-a", "boolean", "anchor")]},
            priority_date="2025-12-31",
            limit=8,
        )

    assert evidence
    assert engines == ["openalex"]
    assert captured_context["task_id"] == "task-oar-ctx"
    assert captured_context["task_type"] == "ai_reply"
