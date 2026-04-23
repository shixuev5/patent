from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import httpx


class BackendClient:
    def __init__(
        self,
        *,
        api_base_url: str,
        internal_gateway_token: str,
        inbound_request_timeout_seconds: float = 180.0,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.internal_gateway_token = internal_gateway_token
        self._client = httpx.AsyncClient(timeout=60.0)
        self.inbound_request_timeout_seconds = max(6.0, float(inbound_request_timeout_seconds or 0.0))

    async def close(self) -> None:
        await self._client.aclose()

    async def wait_until_ready(
        self,
        *,
        poll_interval_seconds: float = 1.0,
        request_timeout_seconds: float = 5.0,
    ) -> None:
        interval = max(0.2, float(poll_interval_seconds or 0.0))
        timeout = max(1.0, float(request_timeout_seconds or 0.0))
        while True:
            try:
                response = await self._client.get(
                    f"{self.api_base_url}/api/health",
                    timeout=timeout,
                )
                response.raise_for_status()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(interval)

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
        timeout_seconds: Optional[float] = None,
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
            timeout=max(6.0, float(timeout_seconds or self.inbound_request_timeout_seconds or 0.0)),
        )
        return await self._parse_json(response)

    async def fetch_runtime_snapshot(self) -> Dict[str, Any]:
        response = await self._client.get(
            f"{self.api_base_url}/api/internal/wechat/runtime-snapshot",
            headers=self._headers,
        )
        return await self._parse_json(response)

    async def update_login_session_state(
        self,
        *,
        login_session_id: str,
        status: str,
        qr_url: Optional[str] = None,
        account_id: Optional[str] = None,
        wechat_user_id: Optional[str] = None,
        wechat_display_name: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        response = await self._client.post(
            f"{self.api_base_url}/api/internal/wechat/login-sessions/{login_session_id}/state",
            headers=self._headers,
            json={
                "status": status,
                "qrUrl": qr_url,
                "accountId": account_id,
                "wechatUserId": wechat_user_id,
                "wechatDisplayName": wechat_display_name,
                "errorMessage": error_message,
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

    async def await_delivery_event(self, *, cursor: int = 0, timeout_seconds: float = 30.0) -> Dict[str, Any]:
        response = await self._client.get(
            f"{self.api_base_url}/api/internal/wechat/delivery-events/await",
            headers=self._headers,
            params={"cursor": cursor, "timeoutSeconds": timeout_seconds},
            timeout=max(5.0, float(timeout_seconds or 0.0) + 5.0),
        )
        return await self._parse_json(response)

    async def update_delivery_job_progress(
        self,
        delivery_job_id: str,
        *,
        stage: str,
        stage_details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        response = await self._client.post(
            f"{self.api_base_url}/api/internal/wechat/delivery-jobs/{delivery_job_id}/progress",
            headers=self._headers,
            json={"stage": stage, "stageDetails": stage_details or {}},
        )
        return await self._parse_json(response)

    async def complete_delivery_job(self, delivery_job_id: str) -> None:
        response = await self._client.post(
            f"{self.api_base_url}/api/internal/wechat/delivery-jobs/{delivery_job_id}/complete",
            headers=self._headers,
            json={},
        )
        response.raise_for_status()

    async def fail_delivery_job(
        self,
        delivery_job_id: str,
        error_message: str,
        *,
        retryable: Optional[bool] = None,
        retry_after_seconds: Optional[int] = None,
    ) -> None:
        response = await self._client.post(
            f"{self.api_base_url}/api/internal/wechat/delivery-jobs/{delivery_job_id}/fail",
            headers=self._headers,
            json={
                "errorMessage": error_message,
                "retryable": retryable,
                "retryAfterSeconds": retry_after_seconds,
            },
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
