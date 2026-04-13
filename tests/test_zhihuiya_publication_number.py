from __future__ import annotations

from agents.common.search_clients.zhihuiya import ZhihuiyaClient


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def test_get_publication_number_by_application_number_from_count(monkeypatch):
    client = ZhihuiyaClient()
    client.token = "token"
    client.headers["Authorization"] = "Bearer token"

    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["query"] = (json or {}).get("q")
        return _FakeResponse(
            {
                "status": True,
                "data": {
                    "patent_info": {
                        "PN": "CN115655695A",
                        "PATENT_ID": "pid-1",
                    }
                },
            }
        )

    monkeypatch.setattr(client.session, "post", _fake_post)
    publication_number = client.get_publication_number_by_application_number("202310001234.5")

    assert publication_number == "CN115655695A"
    assert captured["query"] == "APNO:(202310001234.5)"


def test_get_publication_number_by_application_number_returns_none_when_missing(monkeypatch):
    client = ZhihuiyaClient()
    client.token = "token"
    client.headers["Authorization"] = "Bearer token"

    monkeypatch.setattr(
        client.session,
        "post",
        lambda *args, **kwargs: _FakeResponse(
            {
                "status": True,
                "data": {
                    "patent_info": {
                        "PATENT_ID": "pid-1",
                    }
                },
            }
        ),
    )
    assert client.get_publication_number_by_application_number("202310001234.5") is None


def test_get_patent_id_by_pn_uses_application_query_for_application_number(monkeypatch):
    client = ZhihuiyaClient()
    captured = {}

    def _fake_query(query):
        captured["query"] = query
        return {"PATENT_ID": "pid-1"}

    monkeypatch.setattr(client, "_query_patent_info_by_count", _fake_query)

    patent_id = client._get_patent_id_by_pn("PCT/CN2024/123456")

    assert patent_id == "pid-1"
    assert captured["query"] == "APNO:(PCT/CN2024/123456)"


def test_get_patent_id_by_pn_uses_application_query_for_chinese_application_number_with_x(monkeypatch):
    client = ZhihuiyaClient()
    captured = {}

    def _fake_query(query):
        captured["query"] = query
        return {"PATENT_ID": "pid-x"}

    monkeypatch.setattr(client, "_query_patent_info_by_count", _fake_query)

    patent_id = client._get_patent_id_by_pn("202310658730.X")

    assert patent_id == "pid-x"
    assert captured["query"] == "APNO:(202310658730.X)"


def test_has_patent_record_uses_publication_query(monkeypatch):
    client = ZhihuiyaClient()
    captured = {}

    def _fake_query(query, raise_on_error=False):
        captured["query"] = query
        captured["raise_on_error"] = raise_on_error
        return {"PATENT_ID": "pid-1"}

    monkeypatch.setattr(client, "_query_patent_info_by_count_with_options", _fake_query)

    assert client.has_patent_record("CH708501A1") is True
    assert captured["query"] == "PN:(CH708501A1)"
    assert captured["raise_on_error"] is True


def test_has_patent_record_uses_application_query(monkeypatch):
    client = ZhihuiyaClient()
    captured = {}

    def _fake_query(query, raise_on_error=False):
        captured["query"] = query
        return {"PATENT_ID": "pid-1"}

    monkeypatch.setattr(client, "_query_patent_info_by_count_with_options", _fake_query)

    assert client.has_patent_record("PCT/CN2024/123456") is True
    assert captured["query"] == "APNO:(PCT/CN2024/123456)"
