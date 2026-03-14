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
