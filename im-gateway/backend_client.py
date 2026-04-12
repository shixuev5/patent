from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import httpx


class BackendClient:
    def __init__(self, *, api_base_url: str, internal_gateway_token: str) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.internal_gateway_token = internal_gateway_token
        self._client = httpx.AsyncClient(timeout=60.0)

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Internal-Gateway-Token": self.internal_gateway_token,
        }

    async def _parse_json(self, response: httpx.Response) -> Dict[str, Any]:
        response.raise_for_status()
        return response.json()

    async def post_inbound_message(
        self,
        *,
        bot_account_id: str,
        wechat_peer_id: str,
        wechat_peer_name: Optional[str],
        text: Optional[str],
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        response = await self._client.post(
            f"{self.api_base_url}/api/internal/wechat/messages",
            headers=self._headers,
            json={
                "botAccountId": bot_account_id,
                "wechatPeerId": wechat_peer_id,
                "wechatPeerName": wechat_peer_name,
                "text": text,
                "attachments": attachments or [],
            },
        )
        return await self._parse_json(response)

    async def complete_bind_session(
        self,
        *,
        bind_session_id: str,
        bot_account_id: str,
        wechat_peer_id: str,
        wechat_peer_name: Optional[str],
    ) -> Dict[str, Any]:
        response = await self._client.post(
            f"{self.api_base_url}/api/internal/wechat/bind-sessions/{bind_session_id}/complete",
            headers=self._headers,
            json={
                "botAccountId": bot_account_id,
                "wechatPeerId": wechat_peer_id,
                "wechatPeerName": wechat_peer_name,
            },
        )
        return await self._parse_json(response)

    async def complete_bind_session_by_code(
        self,
        *,
        bind_code: str,
        bot_account_id: str,
        wechat_peer_id: str,
        wechat_peer_name: Optional[str],
    ) -> Dict[str, Any]:
        response = await self._client.post(
            f"{self.api_base_url}/api/internal/wechat/bind-sessions/complete-by-code",
            headers=self._headers,
            json={
                "bindCode": bind_code,
                "botAccountId": bot_account_id,
                "wechatPeerId": wechat_peer_id,
                "wechatPeerName": wechat_peer_name,
            },
        )
        return await self._parse_json(response)

    async def claim_delivery_jobs(self, limit: int = 5) -> Dict[str, Any]:
        response = await self._client.post(
            f"{self.api_base_url}/api/internal/wechat/delivery-jobs/claim",
            headers=self._headers,
            json={"limit": limit},
        )
        return await self._parse_json(response)

    async def complete_delivery_job(self, delivery_job_id: str) -> None:
        response = await self._client.post(
            f"{self.api_base_url}/api/internal/wechat/delivery-jobs/{delivery_job_id}/complete",
            headers=self._headers,
            json={},
        )
        response.raise_for_status()

    async def fail_delivery_job(self, delivery_job_id: str, error_message: str) -> None:
        response = await self._client.post(
            f"{self.api_base_url}/api/internal/wechat/delivery-jobs/{delivery_job_id}/fail",
            headers=self._headers,
            json={"errorMessage": error_message},
        )
        response.raise_for_status()

    async def download_task_artifact(self, download_path: str) -> Tuple[bytes, str, Optional[str]]:
        response = await self._client.get(
            f"{self.api_base_url}{download_path}",
            headers={"X-Internal-Gateway-Token": self.internal_gateway_token},
        )
        response.raise_for_status()
        filename = None
        disposition = response.headers.get("content-disposition") or ""
        if "filename*=" in disposition:
            filename = disposition.split("filename*=", 1)[1].split("''", 1)[-1]
        return response.content, response.headers.get("content-type", "application/octet-stream"), filename
