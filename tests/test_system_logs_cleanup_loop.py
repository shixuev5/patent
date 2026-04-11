from __future__ import annotations

import asyncio

from backend import system_logs


def test_cleanup_loop_runs_cleanup_before_first_sleep(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        system_logs,
        "cleanup_expired_system_logs",
        lambda: calls.append("cleanup") or {"deleted_db": 0, "deleted_payload_files": 0},
    )

    async def _fake_sleep(_seconds: int):
        raise asyncio.CancelledError()

    monkeypatch.setattr(system_logs.asyncio, "sleep", _fake_sleep)

    try:
        asyncio.run(system_logs._cleanup_loop())
    except asyncio.CancelledError:
        pass

    assert calls == ["cleanup"]
