from agents.ai_reply.src.external_evidence import ExternalEvidenceAggregator
from backend import task_usage_tracking


def test_external_evidence_workers_keep_task_usage_context(monkeypatch):
    monkeypatch.delenv("ZHIHUIYA_USERNAME", raising=False)
    monkeypatch.delenv("ZHIHUIYA_PASSWORD", raising=False)

    aggregator = ExternalEvidenceAggregator()
    captured_context = {}

    monkeypatch.setattr(
        aggregator,
        "_normalize_engine_queries",
        lambda _: {"openalex": ["query-a"], "zhihuiya": [], "tavily": []},
    )

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

    collector = task_usage_tracking.create_task_usage_collector(
        task_id="task-oar-ctx",
        owner_id="authing:user-oar",
        task_type="ai_reply",
    )

    with task_usage_tracking.task_usage_collection(collector):
        evidence, engines, _ = aggregator.search_evidence({}, priority_date="2025-12-31", limit=8)

    assert evidence
    assert engines == ["openalex"]
    assert captured_context["task_id"] == "task-oar-ctx"
    assert captured_context["task_type"] == "ai_reply"
