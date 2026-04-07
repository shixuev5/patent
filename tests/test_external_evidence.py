from __future__ import annotations

import builtins
import importlib
import os

import agents.ai_reply.src.external_evidence as external_evidence_module
from agents.ai_reply.src.external_evidence import ExternalEvidenceAggregator
from agents.ai_reply.src.retrieval_utils import make_query_spec
from agents.common.search_clients.factory import SearchClientFactory
from agents.common.search_clients.zhihuiya import ZhihuiyaClient
from agents.common.retrieval.external_rerank_service import (
    ExternalEvidenceRerankError,
    ExternalEvidenceRerankService,
)
from config import settings


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def _clear_external_env(monkeypatch):
    SearchClientFactory._instances.clear()
    ZhihuiyaClient._account_cooldowns.clear()
    for env_name in list(os.environ):
        if env_name.startswith("ZHIHUIYA_ACCOUNTS__"):
            monkeypatch.delenv(env_name, raising=False)
    for env_name in [
        "OPENALEX_API_KEYS",
        "OPENALEX_API_KEY",
        "OPENALEX_EMAIL",
        "TAVILY_API_KEYS",
        "RETRIEVAL_API_KEY",
        "RETRIEVAL_BASE_URL",
        "RETRIEVAL_RERANK_MODEL",
        "ZHIHUIYA_USERNAME",
        "ZHIHUIYA_PASSWORD",
    ]:
        monkeypatch.delenv(env_name, raising=False)


def test_openalex_api_key_supports_multi_value_config(monkeypatch):
    _clear_external_env(monkeypatch)
    monkeypatch.setenv("OPENALEX_API_KEYS", "key-a, key-b\nkey-c;key-a")

    aggregator = ExternalEvidenceAggregator()

    assert aggregator.openalex_api_keys == ["key-a", "key-b", "key-c"]


def test_openalex_legacy_single_key_env_is_ignored(monkeypatch):
    _clear_external_env(monkeypatch)
    monkeypatch.setenv("OPENALEX_API_KEY", "legacy-key")

    aggregator = ExternalEvidenceAggregator()

    assert aggregator.openalex_api_keys == []


def test_zhihuiya_multi_account_env_enables_client(monkeypatch):
    _clear_external_env(monkeypatch)
    monkeypatch.setenv("ZHIHUIYA_ACCOUNTS__0__USERNAME", "user-a@example.com")
    monkeypatch.setenv("ZHIHUIYA_ACCOUNTS__0__PASSWORD", "secret-a")

    aggregator = ExternalEvidenceAggregator()

    assert aggregator.zhihuiya_enabled is True


def test_zhihuiya_legacy_single_account_env_is_ignored(monkeypatch):
    _clear_external_env(monkeypatch)
    monkeypatch.setenv("ZHIHUIYA_USERNAME", "legacy@example.com")
    monkeypatch.setenv("ZHIHUIYA_PASSWORD", "legacy-password")

    aggregator = ExternalEvidenceAggregator()

    assert aggregator.zhihuiya_enabled is False


def test_openalex_search_keeps_anonymous_mode_when_key_missing(monkeypatch):
    _clear_external_env(monkeypatch)
    aggregator = ExternalEvidenceAggregator()
    captured = {}

    def _fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = dict(params or {})
        return _FakeResponse(
            {
                "results": [
                    {
                        "display_name": "paper-a",
                        "primary_location": {
                            "landing_page_url": "https://example.com/paper-a",
                            "source": {"display_name": "journal-a"},
                        },
                        "abstract_inverted_index": {"Paper": [0], "snippet": [1]},
                        "publication_date": "2024-01-01",
                    }
                ]
            }
        )

    monkeypatch.setattr("agents.ai_reply.src.external_evidence.requests.get", _fake_get)

    results = aggregator._search_openalex(
        [make_query_spec("battery material", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=2,
    )

    assert len(results) == 1
    assert captured["params"]["filter"] == (
        "title_and_abstract.search:battery material,"
        "language:en,"
        "has_abstract:true,"
        "to_publication_date:2024-12-31"
    )
    assert "api_key" not in captured["params"]


def test_openalex_search_rotates_key_on_limit_error(monkeypatch):
    _clear_external_env(monkeypatch)
    monkeypatch.setenv("OPENALEX_API_KEYS", "key-a,key-b")
    aggregator = ExternalEvidenceAggregator()
    calls = []

    def _fake_get(url, params=None, timeout=None):
        params = dict(params or {})
        calls.append(params.get("api_key"))
        if len(calls) == 1:
            return _FakeResponse({"error": "rate limit exceeded"}, status_code=429, text="rate limit exceeded")
        return _FakeResponse(
            {
                "results": [
                    {
                        "display_name": "paper-b",
                        "primary_location": {
                            "landing_page_url": "https://example.com/paper-b",
                            "source": {"display_name": "journal-b"},
                        },
                        "abstract_inverted_index": {"Useful": [0], "evidence": [1]},
                        "publication_date": "2023-06-01",
                    }
                ]
            }
        )

    monkeypatch.setattr("agents.ai_reply.src.external_evidence.requests.get", _fake_get)

    results = aggregator._search_openalex(
        [make_query_spec("solid electrolyte", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=2,
    )

    assert calls == ["key-a", "key-b"]
    assert len(results) == 1
    assert results[0]["title"] == "paper-b"


def test_openalex_query_rewrite_removes_legal_scaffolding():
    aggregator = ExternalEvidenceAggregator()

    query = aggregator._normalize_openalex_query(
        "\"locking structure\" AND handbook OR textbook"
    )

    assert query == "\"locking structure\" AND handbook OR textbook"


def test_openalex_query_normalization_removes_filter_delimiters_only():
    aggregator = ExternalEvidenceAggregator()

    query = aggregator._normalize_openalex_query(
        "\"large language model\", AND patent claim generation"
    )

    assert query == "\"large language model\" AND patent claim generation"


def test_zhihuiya_search_executes_lexical_and_semantic_queries(monkeypatch):
    aggregator = ExternalEvidenceAggregator()

    class _FakeClient:
        def __init__(self):
            self.calls = []

        def search(self, query, limit=50):
            self.calls.append(("lexical", query, limit))
            return {
                "results": [
                    {
                        "title": "基于人工智能的专利申请撰写方法及系统",
                        "pn": "US20240303416A1",
                        "abstract": "基于该简短描述撰写一个或多个专利权利要求。",
                        "publication_date": "2024-09-12",
                        "score": "88",
                    }
                ]
            }

        def search_semantic(self, text, to_date="", limit=50):
            self.calls.append(("semantic", text, to_date, limit))
            return {
                "results": [
                    {
                        "title": "一种基于人工智能的专利撰写方法及撰写系统",
                        "pn": "CN108491368A",
                        "abstract": "神经网络撰写模块智能生成独立权利要求。",
                        "publication_date": "2018-09-04",
                        "score": "77",
                    }
                ]
            }

    aggregator.zhihuiya_client = _FakeClient()

    results = aggregator._search_zhihuiya(
        [
            make_query_spec("\"专利权利要求\" AND 生成", "lexical", "core_patent"),
            make_query_spec("专利权利要求 自动生成", "semantic", "expansion"),
        ],
        priority_date="2024-12-31",
        per_query=2,
    )

    assert len(results) == 2
    assert aggregator.zhihuiya_client.calls[0][0] == "lexical"
    assert aggregator.zhihuiya_client.calls[1][0] == "semantic"
    assert results[0]["pn"] == "US20240303416A1"


def test_tavily_search_uses_advanced_params(monkeypatch):
    aggregator = ExternalEvidenceAggregator()
    aggregator.tavily_api_keys = ["tvly-key"]
    captured = {}

    def _fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = dict(json or {})
        return _FakeResponse(
            {
                "results": [
                    {
                        "title": "低信号文章",
                        "url": "https://blog.csdn.net/demo/article/details/1",
                        "content": "低信号内容",
                        "published_date": "2024-01-01",
                    },
                    {
                        "title": "高校 PDF 教材",
                        "url": "https://example.edu/reference.pdf",
                        "content": "高信号内容",
                        "published_date": "2023-01-01",
                    },
                ]
            }
        )

    monkeypatch.setattr("agents.ai_reply.src.external_evidence.requests.post", _fake_post)

    results = aggregator._search_tavily(
        [make_query_spec("锁定结构 教材 手册 标准 PDF 高校", "web", "reference")],
        priority_date="2024-12-31",
        per_query=3,
    )

    assert captured["json"]["search_depth"] == "advanced"
    assert captured["json"]["topic"] == "general"
    assert captured["json"]["chunks_per_source"] == 3
    assert captured["json"]["end_date"] == "2024-12-31"
    assert len(results) == 2
    assert results[1]["url"] == "https://example.edu/reference.pdf"


def test_external_evidence_module_import_does_not_touch_zhihuiya_factory(monkeypatch):
    original_import = builtins.__import__

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {
            "agents.common.search_clients.factory",
            "agents.common.search_clients.zhihuiya",
        }:
            raise AssertionError(f"unexpected import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _guarded_import)
    importlib.reload(external_evidence_module)


def test_external_rerank_service_uses_retrieval_gateway(monkeypatch):
    _clear_external_env(monkeypatch)
    monkeypatch.setenv("RETRIEVAL_API_KEY", "retrieval-key")
    monkeypatch.setenv("RETRIEVAL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("RETRIEVAL_RERANK_MODEL", "qwen3-rerank")
    monkeypatch.setattr(settings, "RETRIEVAL_API_KEY", "retrieval-key")
    monkeypatch.setattr(settings, "RETRIEVAL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(settings, "RETRIEVAL_RERANK_MODEL", "qwen3-rerank")

    service = ExternalEvidenceRerankService()
    captured = {}
    assert str(service.client.base_url).endswith("/compatible-api/v1/")

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"index": 1, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.34},
                ]
            }

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("agents.common.retrieval.external_rerank_service.requests.post", _fake_post)

    rows = service.rerank("query-a", ["doc-a", "doc-b"])

    assert captured["url"].endswith("/compatible-api/v1/reranks")
    assert captured["body"]["model"] == "qwen3-rerank"
    assert captured["body"]["documents"] == ["doc-a", "doc-b"]
    assert "Authorization" in captured["headers"]
    assert rows == [
        {"index": 1, "relevance_score": 0.91},
        {"index": 0, "relevance_score": 0.34},
    ]


def test_external_rerank_service_falls_back_to_llm_gateway(monkeypatch):
    _clear_external_env(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(settings, "RETRIEVAL_API_KEY", "")
    monkeypatch.setattr(settings, "RETRIEVAL_BASE_URL", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "llm-key")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    service = ExternalEvidenceRerankService()

    assert str(service.client.base_url).endswith("/compatible-api/v1/")


def test_search_evidence_reranks_and_rebuilds_doc_ids(monkeypatch):
    _clear_external_env(monkeypatch)
    monkeypatch.setenv("RETRIEVAL_API_KEY", "retrieval-key")
    monkeypatch.setenv("RETRIEVAL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    aggregator = ExternalEvidenceAggregator()
    monkeypatch.setattr(
        aggregator,
        "_search_openalex",
        lambda *args, **kwargs: [
            {
                "source_type": "openalex",
                "title": "doc-a",
                "url": "https://example.com/a",
                "snippet": "snippet-a",
                "published": "2024-01-01",
            },
            {
                "source_type": "openalex",
                "title": "doc-b",
                "url": "https://example.com/b",
                "snippet": "snippet-b",
                "published": "2023-01-01",
            },
        ],
    )

    class _StubRerank:
        model = "qwen3-rerank"

        def rerank(self, query, documents):
            assert query == "query-a"
            assert len(documents) == 2
            return [
                {"index": 1, "relevance_score": 0.88},
                {"index": 0, "relevance_score": 0.22},
            ]

    monkeypatch.setattr(aggregator, "_get_rerank_service", lambda: _StubRerank())

    results, engines, meta = aggregator.search_evidence(
        queries={"openalex": [make_query_spec("query-a", "boolean", "anchor")]},
        priority_date="2024-12-31",
        limit=2,
    )

    assert engines == ["openalex"]
    assert [item["title"] for item in results] == ["doc-b", "doc-a"]
    assert [item["doc_id"] for item in results] == ["EXT1", "EXT2"]
    assert meta["retrieval"]["openalex"]["rerank_enabled"] is True
    assert meta["retrieval"]["openalex"]["results"][0]["relevance_score"] == 0.88
    assert meta["retrieval"]["openalex"]["queries"][0]["mode"] == "boolean"
    assert meta["retrieval"]["openalex"]["queries"][0]["intent"] == "anchor"


def test_search_evidence_falls_back_when_rerank_fails(monkeypatch):
    _clear_external_env(monkeypatch)
    monkeypatch.setenv("RETRIEVAL_API_KEY", "retrieval-key")
    monkeypatch.setenv("RETRIEVAL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    aggregator = ExternalEvidenceAggregator()
    monkeypatch.setattr(
        aggregator,
        "_search_openalex",
        lambda *args, **kwargs: [
            {
                "source_type": "openalex",
                "title": "large language model patent claim generation",
                "url": "https://example.com/a",
                "snippet": "relevant snippet",
                "published": "2024-01-01",
            },
            {
                "source_type": "openalex",
                "title": "generic business model",
                "url": "https://example.com/b",
                "snippet": "generic snippet",
                "published": "2024-02-01",
            },
        ],
    )

    class _FailingRerank:
        model = "qwen3-rerank"

        def rerank(self, query, documents):
            raise ExternalEvidenceRerankError("timeout")

    monkeypatch.setattr(aggregator, "_get_rerank_service", lambda: _FailingRerank())

    results, _, meta = aggregator.search_evidence(
        queries={"openalex": [make_query_spec("large language model patent claim generation", "boolean", "anchor")]},
        priority_date="2024-12-31",
        limit=2,
    )

    assert results[0]["title"] == "large language model patent claim generation"
    assert meta["retrieval"]["openalex"]["rerank_enabled"] is False
    assert meta["retrieval"]["openalex"]["rerank_fallback_reason"] == "timeout"


def test_search_evidence_collapses_duplicate_zhihuiya_variants(monkeypatch):
    aggregator = ExternalEvidenceAggregator()
    monkeypatch.setattr(
        aggregator,
        "_search_zhihuiya",
        lambda *args, **kwargs: [
            {
                "source_type": "zhihuiya",
                "title": "基于人工智能的专利申请撰写方法及系统",
                "url": "https://patents.google.com/patent/US20240303416A1",
                "snippet": "根据简短描述撰写一个或多个专利权利要求。",
                "published": "2024-09-12",
                "pn": "US20240303416A1",
            },
            {
                "source_type": "zhihuiya",
                "title": "基于人工智能的专利申请撰写方法及系统",
                "url": "https://patents.google.com/patent/US20240220714A1",
                "snippet": "根据简短描述撰写一个或多个专利权利要求。",
                "published": "2024-07-04",
                "pn": "US20240220714A1",
            },
        ],
    )

    class _StubRerank:
        model = "qwen3-rerank"

        def rerank(self, query, documents):
            return [
                {"index": 0, "relevance_score": 0.92},
                {"index": 1, "relevance_score": 0.91},
            ]

    monkeypatch.setattr(aggregator, "_get_rerank_service", lambda: _StubRerank())

    results, engines, meta = aggregator.search_evidence(
        queries={"zhihuiya": [make_query_spec("\"专利权利要求\" AND 生成", "lexical", "core_patent")]},
        priority_date="2024-12-31",
        limit=4,
    )

    assert engines == ["zhihuiya"]
    assert len(results) == 1
    assert results[0]["url"].endswith("US20240303416A1")
    assert meta["retrieval"]["zhihuiya"]["result_count"] == 1
