from __future__ import annotations

import logging

import pytest

from backend.logging_setup import should_suppress_uvicorn_access_log


def _make_access_record(path: str, status_code: int) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:8000", "POST", path, "1.1", status_code),
        exc_info=None,
    )


@pytest.mark.parametrize(
    ("path", "status_code", "expected"),
    [
        ("/api/internal/wechat/delivery-jobs/claim", 200, True),
        ("/api/internal/wechat/delivery-events/await", 200, True),
        ("/api/internal/wechat/runtime-snapshot", 200, True),
        ("/api/health", 200, True),
        ("/api/account/wechat-integration", 200, True),
        ("/api/account/wechat-integration/login-session/wls-ca9d81a6a756", 200, True),
        ("/api/internal/wechat/login-sessions/wls-ca9d81a6a756/state", 200, True),
        ("/api/internal/wechat/delivery-jobs/claim", 500, False),
        ("/api/account/wechat-integration/settings", 200, False),
    ],
)
def test_should_suppress_uvicorn_access_log(path: str, status_code: int, expected: bool):
    record = _make_access_record(path, status_code)
    assert should_suppress_uvicorn_access_log(record) is expected
