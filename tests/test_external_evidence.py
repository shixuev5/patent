from __future__ import annotations

from agents.ai_reply.src.external_evidence import ExternalEvidenceAggregator


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
    for env_name in [
        "OPENALEX_API_KEYS",
        "OPENALEX_API_KEY",
        "OPENALEX_EMAIL",
        "TAVILY_API_KEYS",
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

    results = aggregator._search_openalex(["battery material"], priority_date="2024-12-31", per_query=2)

    assert len(results) == 1
    assert captured["params"]["search"] == "battery material"
    assert captured["params"]["filter"] == "to_publication_date:2024-12-31"
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

    results = aggregator._search_openalex(["solid electrolyte"], priority_date="2024-12-31", per_query=2)

    assert calls == ["key-a", "key-b"]
    assert len(results) == 1
    assert results[0]["title"] == "paper-b"
