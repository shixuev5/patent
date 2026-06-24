from __future__ import annotations

import asyncio

import httpx

from im_gateway.backend_client import BackendClient


class _TimeoutHttpClient:
    async def get(self, *args, **kwargs):
        raise httpx.ReadTimeout("timeout")

    async def post(self, *args, **kwargs):
        raise httpx.ReadTimeout("timeout")

    async def aclose(self):
        return None


def test_fetch_runtime_snapshot_timeout_degrades_to_empty_snapshot():
    client = BackendClient(api_base_url="http://backend", internal_gateway_token="token")
    client._client = _TimeoutHttpClient()  # type: ignore[assignment]

    payload = asyncio.run(client.fetch_runtime_snapshot())

    assert payload == {"activeBindings": [], "pendingLoginSessions": [], "timedOut": True}


def test_claim_delivery_jobs_timeout_degrades_to_empty_result():
    client = BackendClient(api_base_url="http://backend", internal_gateway_token="token")
    client._client = _TimeoutHttpClient()  # type: ignore[assignment]

    payload = asyncio.run(client.claim_delivery_jobs(limit=5))

    assert payload == {"items": [], "total": 0, "timedOut": True}
