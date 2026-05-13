from __future__ import annotations

import builtins
import importlib
import os
import threading
import time
import requests

import agents.ai_reply.src.external_evidence as external_evidence_module
from agents.ai_reply.src.external_evidence import ExternalEvidenceAggregator
from agents.ai_reply.src.retrieval_utils import make_query_spec
from agents.common.retrieval.academic_query_utils import normalize_academic_query
from agents.common.retrieval.academic_search import AcademicSearchClient
from agents.common.search_clients.factory import SearchClientFactory
from agents.common.search_clients.zhihuiya import ZhihuiyaClient
from agents.common.retrieval.external_rerank_service import (
    ExternalEvidenceRerankError,
    ExternalEvidenceRerankService,
)
from config import settings


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200, text: str = "", headers=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.headers = dict(headers or {})

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def _clear_external_env(monkeypatch):
    SearchClientFactory._instances.clear()
    ZhihuiyaClient._account_cooldowns.clear()
    AcademicSearchClient._provider_cooldowns.clear()
    AcademicSearchClient._provider_cooldown_log_deadlines.clear()
    AcademicSearchClient._provider_next_request_deadlines.clear()
    AcademicSearchClient._response_cache.clear()
    for env_name in list(os.environ):
        if env_name.startswith("ZHIHUIYA_ACCOUNTS__"):
            monkeypatch.delenv(env_name, raising=False)
    for env_name in [
        "OPENALEX_API_KEYS",
        "OPENALEX_API_KEY",
        "OPENALEX_EMAIL",
        "SEMANTIC_SCHOLAR_API_KEYS",
        "SEMANTIC_SCHOLAR_BASE_URL",
        "CROSSREF_MAILTO",
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
        if len(calls) <= 3:
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
    monkeypatch.setattr("agents.common.retrieval.academic_search.time.sleep", lambda *_args, **_kwargs: None)

    results = aggregator._search_openalex(
        [make_query_spec("solid electrolyte", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=2,
    )

    assert calls == ["key-a", "key-a", "key-a", "key-b"]
    assert len(results) == 1
    assert results[0]["title"] == "paper-b"


def test_openalex_query_rewrite_removes_legal_scaffolding():
    query = normalize_academic_query("\"locking structure\" AND handbook OR textbook")

    assert query == "\"locking structure\" AND handbook OR textbook"


def test_openalex_query_normalization_removes_filter_delimiters_only():
    query = normalize_academic_query("\"large language model\", AND patent claim generation")

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


def test_semanticscholar_search_uses_expected_fields(monkeypatch):
    _clear_external_env(monkeypatch)
    aggregator = ExternalEvidenceAggregator()
    aggregator.semanticscholar_api_keys = ["s2-key"]
    captured = {}

    def _fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = dict(params or {})
        captured["headers"] = dict(headers or {})
        return _FakeResponse(
            {
                "data": [
                    {
                        "title": "Graph neural network routing",
                        "abstract": "A survey of graph neural network routing methods.",
                        "url": "https://www.semanticscholar.org/paper/abc",
                        "venue": "NeurIPS",
                        "publicationDate": "2024-01-01",
                        "citationCount": 123,
                        "influentialCitationCount": 11,
                        "openAccessPdf": {"url": "https://example.com/paper.pdf"},
                    }
                ]
            }
        )

    monkeypatch.setattr("agents.ai_reply.src.external_evidence.requests.get", _fake_get)

    results = aggregator._search_semanticscholar(
        [make_query_spec("graph neural network routing", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=2,
    )

    assert captured["url"].endswith("/graph/v1/paper/search/bulk")
    assert captured["params"]["fields"] == aggregator.semanticscholar_fields
    assert captured["params"]["year"] == "-2024"
    assert "publicationDateOrYear" not in captured["params"]
    assert captured["headers"]["x-api-key"] == "s2-key"
    assert len(results) == 1
    assert results[0]["source_type"] == "semanticscholar"
    assert results[0]["venue"] == "NeurIPS"
    assert results[0]["citation_count"] == 123
    assert results[0]["influential_citation_count"] == 11


def test_semanticscholar_requests_are_serialized(monkeypatch):
    monkeypatch.setattr(settings, "SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS", 0.0)
    aggregator = ExternalEvidenceAggregator()
    aggregator.semanticscholar_api_keys = ["s2-key-a", "s2-key-b"]
    barrier = threading.Barrier(2)
    state = {"active": 0, "max_active": 0}
    state_lock = threading.Lock()

    def _fake_get(url, params=None, headers=None, timeout=None):
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.05)
        with state_lock:
            state["active"] -= 1
        return _FakeResponse({"data": []})

    monkeypatch.setattr("agents.ai_reply.src.external_evidence.requests.get", _fake_get)

    def _run_query(query_text):
        barrier.wait(timeout=1.0)
        return aggregator._search_semanticscholar(
            [make_query_spec(query_text, "boolean", "anchor")],
            priority_date="2024-12-31",
            per_query=2,
        )

    thread_a = threading.Thread(target=_run_query, args=("query-a",))
    thread_b = threading.Thread(target=_run_query, args=("query-b",))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=2.0)
    thread_b.join(timeout=2.0)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert state["max_active"] == 1


def test_semanticscholar_anonymous_rate_limit_enters_cooldown_and_skips_followup(monkeypatch):
    _clear_external_env(monkeypatch)
    aggregator = ExternalEvidenceAggregator()
    calls = []

    def _fake_get(url, params=None, headers=None, timeout=None):
        calls.append(
            {
                "url": url,
                "params": dict(params or {}),
                "headers": dict(headers or {}),
            }
        )
        return _FakeResponse({"error": "rate limit exceeded"}, status_code=429, text="rate limit exceeded")

    monkeypatch.setattr("agents.ai_reply.src.external_evidence.requests.get", _fake_get)
    monkeypatch.setattr("agents.common.retrieval.academic_search.time.sleep", lambda *_args, **_kwargs: None)

    first = aggregator._search_semanticscholar(
        [make_query_spec("query-a", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=2,
    )
    second = aggregator._search_semanticscholar(
        [make_query_spec("query-b", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=2,
    )

    assert first == []
    assert second == []
    assert len(calls) == 3


def test_semanticscholar_search_uses_cache_for_identical_request(monkeypatch):
    _clear_external_env(monkeypatch)
    aggregator = ExternalEvidenceAggregator()
    aggregator.semanticscholar_api_keys = ["s2-key"]
    calls = []

    def _fake_get(url, params=None, headers=None, timeout=None):
        calls.append({"params": dict(params or {}), "headers": dict(headers or {})})
        return _FakeResponse(
            {
                "data": [
                    {
                        "title": "Cached Paper",
                        "abstract": "cached abstract",
                        "url": "https://www.semanticscholar.org/paper/cached",
                        "publicationDate": "2024-01-01",
                    }
                ]
            }
        )

    monkeypatch.setattr("agents.ai_reply.src.external_evidence.requests.get", _fake_get)

    first = aggregator._search_semanticscholar(
        [make_query_spec("cached query", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=2,
    )
    second = aggregator._search_semanticscholar(
        [make_query_spec("cached query", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=2,
    )

    assert len(calls) == 1
    assert first == second


def test_semanticscholar_respects_retry_after_before_retrying(monkeypatch):
    _clear_external_env(monkeypatch)
    aggregator = ExternalEvidenceAggregator()
    aggregator.semanticscholar_api_keys = ["s2-key"]
    sleep_calls = []
    calls = []

    def _fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params or {}))
        if len(calls) == 1:
            return _FakeResponse(
                {"error": "rate limit exceeded"},
                status_code=429,
                text="rate limit exceeded",
                headers={"Retry-After": "7"},
            )
        return _FakeResponse({"data": []})

    monkeypatch.setattr("agents.ai_reply.src.external_evidence.requests.get", _fake_get)
    monkeypatch.setattr(
        "agents.common.retrieval.academic_search.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )

    results = aggregator._search_semanticscholar(
        [make_query_spec("retry after query", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=2,
    )

    assert results == []
    assert len(calls) == 2
    assert sleep_calls[0] == 7.0


def test_openalex_cache_key_ignores_api_key_rotation(monkeypatch):
    _clear_external_env(monkeypatch)
    monkeypatch.setenv("OPENALEX_API_KEYS", "key-a,key-b")
    aggregator = ExternalEvidenceAggregator()
    calls = []

    def _fake_get(url, params=None, timeout=None):
        calls.append(dict(params or {}))
        return _FakeResponse(
            {
                "results": [
                    {
                        "display_name": "paper-cache",
                        "primary_location": {
                            "landing_page_url": "https://example.com/paper-cache",
                            "source": {"display_name": "journal-cache"},
                        },
                        "abstract_inverted_index": {"cached": [0], "paper": [1]},
                        "publication_date": "2024-01-01",
                    }
                ]
            }
        )

    monkeypatch.setattr("agents.ai_reply.src.external_evidence.requests.get", _fake_get)

    first = aggregator._search_openalex(
        [make_query_spec("cache me", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=2,
    )
    aggregator._openalex_key_cursor = 1
    second = aggregator._search_openalex(
        [make_query_spec("cache me", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=2,
    )

    assert len(calls) == 1
    assert first == second



def test_crossref_search_normalizes_jats_abstract(monkeypatch):
    aggregator = ExternalEvidenceAggregator()
    monkeypatch.setenv("CROSSREF_MAILTO", "patent@example.com")
    aggregator.crossref_mailto = "patent@example.com"
    captured = {}

    def _fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = dict(params or {})
        captured["headers"] = dict(headers or {})
        return _FakeResponse(
            {
                "message": {
                    "items": [
                        {
                            "title": ["Battery separator design"],
                            "abstract": "<jats:p>Crossref abstract content.</jats:p>",
                            "URL": "https://doi.org/10.1000/test",
                            "DOI": "10.1000/test",
                            "issued": {"date-parts": [[2023, 5, 4]]},
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr("agents.ai_reply.src.external_evidence.requests.get", _fake_get)

    results = aggregator._search_crossref(
        [make_query_spec("battery separator design", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=3,
    )

    assert captured["params"]["query.bibliographic"] == "battery separator design"
    assert captured["params"]["filter"] == "until-pub-date:2024-12-31"
    assert captured["params"]["mailto"] == "patent@example.com"
    assert "mailto:patent@example.com" in captured["headers"].get("User-Agent", "")
    assert "language" not in captured["params"]["select"].split(",")
    assert len(results) == 1
    assert results[0]["snippet"] == "Crossref abstract content."
    assert results[0]["source_type"] == "crossref"


def test_crossref_search_retries_retryable_errors(monkeypatch):
    _clear_external_env(monkeypatch)
    aggregator = ExternalEvidenceAggregator()
    calls = []

    def _fake_get(url, params=None, timeout=None):
        calls.append(dict(params or {}))
        if len(calls) < 3:
            return _FakeResponse(
                {"message": "temporarily unavailable"},
                status_code=503,
                text="temporarily unavailable",
            )
        return _FakeResponse(
            {
                "message": {
                    "items": [
                        {
                            "title": ["Recovered Paper"],
                            "abstract": "<jats:p>Recovered abstract</jats:p>",
                            "URL": "https://doi.org/10.1000/test",
                            "DOI": "10.1000/test",
                            "issued": {"date-parts": [[2024, 1, 1]]},
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr("agents.ai_reply.src.external_evidence.requests.get", _fake_get)
    monkeypatch.setattr("agents.common.retrieval.academic_search.time.sleep", lambda *_args, **_kwargs: None)

    results = aggregator._search_crossref(
        [make_query_spec("recovered query", "boolean", "anchor")],
        priority_date="2024-12-31",
        per_query=2,
    )

    assert len(calls) == 3
    assert len(results) == 1
    assert results[0]["title"] == "Recovered Paper"


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


def test_tavily_rate_limit_enters_cooldown_and_skips_followup(monkeypatch):
    _clear_external_env(monkeypatch)
    monkeypatch.setenv("TAVILY_API_KEYS", "tvly-a,tvly-b")
    aggregator = ExternalEvidenceAggregator()
    calls = []

    def _fake_post(url, json=None, timeout=None):
        payload = dict(json or {})
        calls.append(payload.get("api_key"))
        return _FakeResponse({"error": "rate limit exceeded"}, status_code=429, text="rate limit exceeded")

    monkeypatch.setattr("agents.ai_reply.src.external_evidence.requests.post", _fake_post)

    first = aggregator._search_tavily(
        [make_query_spec("query-a", "web", "reference")],
        priority_date="2024-12-31",
        per_query=3,
    )
    second = aggregator._search_tavily(
        [make_query_spec("query-b", "web", "reference")],
        priority_date="2024-12-31",
        per_query=3,
    )

    assert first == []
    assert second == []
    assert calls == ["tvly-a", "tvly-b"]


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


def test_external_rerank_service_retries_transient_failure(monkeypatch):
    _clear_external_env(monkeypatch)
    monkeypatch.setenv("RETRIEVAL_API_KEY", "retrieval-key")
    monkeypatch.setenv("RETRIEVAL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(settings, "RETRIEVAL_API_KEY", "retrieval-key")
    monkeypatch.setattr(settings, "RETRIEVAL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    service = ExternalEvidenceRerankService()
    calls = []

    class _RetryResponse:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"http {self.status_code}")

        def json(self):
            return self._payload

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "body": json, "timeout": timeout})
        if len(calls) == 1:
            return _RetryResponse(503, {"error": "temporary overload"})
        return _RetryResponse(
            200,
            {
                "data": [
                    {"index": 0, "relevance_score": 0.73},
                    {"index": 1, "relevance_score": 0.41},
                ]
            },
        )

    monkeypatch.setattr("agents.common.retrieval.external_rerank_service.requests.post", _fake_post)
    monkeypatch.setattr("agents.common.retrieval.external_rerank_service.time.sleep", lambda *_args, **_kwargs: None)

    rows = service.rerank("query-a", ["doc-a", "doc-b"])

    assert len(calls) == 2
    assert rows == [
        {"index": 0, "relevance_score": 0.73},
        {"index": 1, "relevance_score": 0.41},
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
    monkeypatch.setattr(aggregator, "_search_semanticscholar", lambda *args, **kwargs: [])
    monkeypatch.setattr(aggregator, "_search_crossref", lambda *args, **kwargs: [])

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


def test_semanticscholar_academic_signal_bonus_affects_rank_and_trace(monkeypatch):
    aggregator = ExternalEvidenceAggregator()
    monkeypatch.setattr(
        aggregator,
        "_search_openalex",
        lambda *args, **kwargs: [
            {
                "source_type": "openalex",
                "title": "doc-openalex",
                "url": "https://example.com/openalex",
                "snippet": "snippet-openalex",
                "published": "2024-01-01",
            }
        ],
    )
    monkeypatch.setattr(
        aggregator,
        "_search_semanticscholar",
        lambda *args, **kwargs: [
            {
                "source_type": "semanticscholar",
                "title": "doc-s2",
                "url": "https://example.com/s2",
                "snippet": "snippet-s2",
                "published": "2024-01-02",
                "venue": "ICML",
                "citation_count": 500,
                "influential_citation_count": 100,
            }
        ],
    )
    monkeypatch.setattr(aggregator, "_search_crossref", lambda *args, **kwargs: [])

    class _StubRerank:
        model = "qwen3-rerank"

        def rerank(self, query, documents):
            return [
                {"index": 0, "relevance_score": 0.84},
                {"index": 1, "relevance_score": 0.83},
            ]

    monkeypatch.setattr(aggregator, "_get_rerank_service", lambda: _StubRerank())

    results, engines, meta = aggregator.search_evidence(
        queries={
            "openalex": [make_query_spec("query-a", "boolean", "anchor")],
            "semanticscholar": [make_query_spec("query-a semanticscholar", "boolean", "anchor")],
        },
        priority_date="2024-12-31",
        limit=2,
    )

    assert engines == ["openalex", "semanticscholar"]
    assert results[0]["source_type"] == "semanticscholar"
    assert results[0]["venue"] == "ICML"
    assert results[0]["citation_count"] == 500
    assert results[0]["influential_citation_count"] == 100
    assert meta["retrieval"]["semanticscholar"]["results"][0]["venue"] == "ICML"
    assert meta["retrieval"]["semanticscholar"]["results"][0]["citation_count"] == 500


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
    monkeypatch.setattr(aggregator, "_search_semanticscholar", lambda *args, **kwargs: [])
    monkeypatch.setattr(aggregator, "_search_crossref", lambda *args, **kwargs: [])

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


def test_search_evidence_does_not_fan_out_openalex_queries_to_semanticscholar_and_crossref(monkeypatch):
    aggregator = ExternalEvidenceAggregator()
    captured = {"semanticscholar": [], "crossref": []}
    monkeypatch.setattr(
        aggregator,
        "_search_openalex",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        aggregator,
        "_search_semanticscholar",
        lambda queries, *args, **kwargs: captured["semanticscholar"].append(list(queries)) or [],
    )
    monkeypatch.setattr(
        aggregator,
        "_search_crossref",
        lambda queries, *args, **kwargs: captured["crossref"].append(list(queries)) or [],
    )
    monkeypatch.setattr(aggregator, "_rerank_results", lambda candidates, queries_by_engine: candidates)

    _, _, meta = aggregator.search_evidence(
        queries={"openalex": [make_query_spec("query-a", "boolean", "anchor")]},
        priority_date="2024-12-31",
        limit=2,
    )

    assert captured["semanticscholar"] == []
    assert captured["crossref"] == []
    assert "semanticscholar" not in meta["retrieval"]
    assert "crossref" not in meta["retrieval"]


def test_search_evidence_dedupes_academic_dispatch_queries_and_caps_to_two(monkeypatch):
    aggregator = ExternalEvidenceAggregator()
    captured = {"openalex": [], "semanticscholar": []}
    monkeypatch.setattr(
        aggregator,
        "_search_openalex",
        lambda queries, *args, **kwargs: captured["openalex"].append(list(queries)) or [],
    )
    monkeypatch.setattr(
        aggregator,
        "_search_semanticscholar",
        lambda queries, *args, **kwargs: captured["semanticscholar"].append(list(queries)) or [],
    )
    monkeypatch.setattr(aggregator, "_search_crossref", lambda *args, **kwargs: [])
    monkeypatch.setattr(aggregator, "_rerank_results", lambda candidates, queries_by_engine: candidates)

    aggregator.search_evidence(
        queries={
            "openalex": [
                make_query_spec('wireless charging coil alignment review', "boolean", "anchor"),
                make_query_spec('wireless charging coil alignment survey', "boolean", "anchor"),
                make_query_spec('"wireless charging coil alignment"', "boolean", "expansion"),
                make_query_spec("battery thermal management", "boolean", "anchor"),
            ],
            "semanticscholar": [
                make_query_spec('wireless charging coil alignment tutorial', "boolean", "anchor"),
                make_query_spec('wireless charging coil alignment background', "boolean", "expansion"),
                make_query_spec("battery thermal management fundamentals", "boolean", "anchor"),
            ],
        },
        priority_date="2024-12-31",
        limit=2,
    )

    assert len(captured["openalex"]) == 1
    assert [item["text"] for item in captured["openalex"][0]] == [
        "wireless charging coil alignment review",
        "battery thermal management",
    ]
    assert len(captured["semanticscholar"]) == 1
    assert [item["text"] for item in captured["semanticscholar"][0]] == [
        "wireless charging coil alignment tutorial",
        "battery thermal management fundamentals",
    ]


def test_search_evidence_limits_crossref_when_primary_academic_sources_exist(monkeypatch):
    aggregator = ExternalEvidenceAggregator()
    monkeypatch.setattr(
        aggregator,
        "_search_openalex",
        lambda *args, **kwargs: [
            {
                "source_type": "openalex",
                "title": "openalex-a",
                "url": "https://example.com/openalex-a",
                "snippet": "snippet-a",
                "published": "2024-01-01",
            },
            {
                "source_type": "openalex",
                "title": "openalex-b",
                "url": "https://example.com/openalex-b",
                "snippet": "snippet-b",
                "published": "2023-01-01",
            },
        ],
    )
    monkeypatch.setattr(
        aggregator,
        "_search_semanticscholar",
        lambda *args, **kwargs: [
            {
                "source_type": "semanticscholar",
                "title": "s2-a",
                "url": "https://example.com/s2-a",
                "snippet": "snippet-c",
                "published": "2024-02-01",
            }
        ],
    )
    monkeypatch.setattr(
        aggregator,
        "_search_crossref",
        lambda *args, **kwargs: [
            {
                "source_type": "crossref",
                "title": "crossref-a",
                "url": "https://example.com/crossref-a",
                "snippet": "snippet-d",
                "published": "2024-03-01",
            },
            {
                "source_type": "crossref",
                "title": "crossref-b",
                "url": "https://example.com/crossref-b",
                "snippet": "snippet-e",
                "published": "2024-04-01",
            },
        ],
    )

    class _StubRerank:
        model = "qwen3-rerank"

        def rerank(self, query, documents):
            return [
                {"index": 3, "relevance_score": 0.97},
                {"index": 2, "relevance_score": 0.96},
                {"index": 1, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.94},
                {"index": 4, "relevance_score": 0.93},
            ]

    monkeypatch.setattr(aggregator, "_get_rerank_service", lambda: _StubRerank())

    results, engines, meta = aggregator.search_evidence(
        queries={
            "openalex": [make_query_spec("query-a", "boolean", "anchor")],
            "semanticscholar": [make_query_spec("query-a semanticscholar", "boolean", "anchor")],
            "crossref": [make_query_spec("query-a crossref", "boolean", "anchor")],
        },
        priority_date="2024-12-31",
        limit=4,
    )

    assert engines == ["openalex", "semanticscholar", "crossref"]
    assert [item["source_type"] for item in results].count("crossref") == 0
    assert [item["source_type"] for item in results][:3] == ["openalex", "semanticscholar", "openalex"]
    assert meta["retrieval"]["crossref"]["raw_result_count"] == 2
    assert meta["retrieval"]["crossref"]["result_count"] == 0


def test_crossref_rank_prior_keeps_primary_academic_result_ahead(monkeypatch):
    aggregator = ExternalEvidenceAggregator()
    monkeypatch.setattr(
        aggregator,
        "_search_openalex",
        lambda *args, **kwargs: [
            {
                "source_type": "openalex",
                "title": "openalex-a",
                "url": "https://example.com/openalex-a",
                "snippet": "snippet-a",
                "published": "2024-01-01",
            }
        ],
    )
    monkeypatch.setattr(
        aggregator,
        "_search_semanticscholar",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        aggregator,
        "_search_crossref",
        lambda *args, **kwargs: [
            {
                "source_type": "crossref",
                "title": "crossref-a",
                "url": "https://example.com/crossref-a",
                "snippet": "snippet-b",
                "published": "2024-02-01",
            }
        ],
    )

    class _StubRerank:
        model = "qwen3-rerank"

        def rerank(self, query, documents):
            return [
                {"index": 0, "relevance_score": 0.90},
                {"index": 1, "relevance_score": 0.93},
            ]

    monkeypatch.setattr(aggregator, "_get_rerank_service", lambda: _StubRerank())

    results, engines, meta = aggregator.search_evidence(
        queries={
            "openalex": [make_query_spec("query-a", "boolean", "anchor")],
            "crossref": [make_query_spec("query-a crossref", "boolean", "anchor")],
        },
        priority_date="2024-12-31",
        limit=2,
    )

    assert engines == ["openalex", "crossref"]
    assert [item["source_type"] for item in results] == ["openalex"]
    assert meta["retrieval"]["crossref"]["raw_result_count"] == 1
    assert meta["retrieval"]["crossref"]["result_count"] == 0


def test_crossref_is_metadata_only_when_primary_academic_hits_exist(monkeypatch):
    aggregator = ExternalEvidenceAggregator()
    monkeypatch.setattr(
        aggregator,
        "_search_openalex",
        lambda *args, **kwargs: [
            {
                "source_type": "openalex",
                "title": "openalex-a",
                "url": "https://example.com/openalex-a",
                "snippet": "snippet-a",
                "published": "2024-01-01",
            }
        ],
    )
    monkeypatch.setattr(
        aggregator,
        "_search_semanticscholar",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        aggregator,
        "_search_crossref",
        lambda *args, **kwargs: [
            {
                "source_type": "crossref",
                "title": "crossref-a",
                "url": "https://example.com/crossref-a",
                "snippet": "snippet-b",
                "published": "2024-02-01",
            }
        ],
    )

    class _StubRerank:
        model = "qwen3-rerank"

        def rerank(self, query, documents):
            assert len(documents) == 1
            return [{"index": 0, "relevance_score": 0.90}]

    monkeypatch.setattr(aggregator, "_get_rerank_service", lambda: _StubRerank())

    results, engines, meta = aggregator.search_evidence(
        queries={
            "openalex": [make_query_spec("query-a", "boolean", "anchor")],
            "crossref": [make_query_spec("query-a crossref", "boolean", "anchor")],
        },
        priority_date="2024-12-31",
        limit=3,
    )

    assert engines == ["openalex", "crossref"]
    assert [item["source_type"] for item in results] == ["openalex"]
    assert meta["retrieval"]["crossref"]["raw_result_count"] == 1
    assert meta["retrieval"]["crossref"]["result_count"] == 0


def test_crossref_enters_candidate_pool_when_primary_academic_hits_absent(monkeypatch):
    aggregator = ExternalEvidenceAggregator()
    monkeypatch.setattr(aggregator, "_search_openalex", lambda *args, **kwargs: [])
    monkeypatch.setattr(aggregator, "_search_semanticscholar", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        aggregator,
        "_search_crossref",
        lambda *args, **kwargs: [
            {
                "source_type": "crossref",
                "title": "crossref-a",
                "url": "https://example.com/crossref-a",
                "snippet": "snippet-a",
                "published": "2024-02-01",
            }
        ],
    )

    class _StubRerank:
        model = "qwen3-rerank"

        def rerank(self, query, documents):
            assert len(documents) == 1
            return [{"index": 0, "relevance_score": 0.93}]

    monkeypatch.setattr(aggregator, "_get_rerank_service", lambda: _StubRerank())

    results, engines, meta = aggregator.search_evidence(
        queries={"crossref": [make_query_spec("query-a crossref", "boolean", "anchor")]},
        priority_date="2024-12-31",
        limit=3,
    )

    assert engines == ["crossref"]
    assert [item["source_type"] for item in results] == ["crossref"]
    assert meta["retrieval"]["crossref"]["result_count"] == 1
