import pytest
import requests

from agents.common.utils.http import request_with_retry


class _DummyResponse:
    pass


def test_request_with_retry_retries_then_succeeds(monkeypatch):
    attempts = []
    sleeps = []
    response = _DummyResponse()

    def fake_request(method, url, **kwargs):
        attempts.append((method, url, kwargs))
        if len(attempts) < 3:
            raise requests.exceptions.Timeout("timeout")
        return response

    monkeypatch.setattr("agents.common.utils.http.requests.request", fake_request)
    monkeypatch.setattr("agents.common.utils.http.time.sleep", sleeps.append)

    result = request_with_retry("get", "https://example.com", attempts=3, backoff_seconds=1.5)

    assert result is response
    assert len(attempts) == 3
    assert sleeps == [1.5, 3.0]


def test_request_with_retry_raises_after_last_attempt(monkeypatch):
    sleeps = []

    def fake_request(method, url, **kwargs):
        raise requests.exceptions.ConnectionError("boom")

    monkeypatch.setattr("agents.common.utils.http.requests.request", fake_request)
    monkeypatch.setattr("agents.common.utils.http.time.sleep", sleeps.append)

    with pytest.raises(requests.exceptions.ConnectionError):
        request_with_retry("post", "https://example.com", attempts=2, backoff_seconds=2.0)

    assert sleeps == [2.0]
