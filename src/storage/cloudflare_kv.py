"""
Cloudflare KV 存储适配器，用于专利报告缓存复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import requests
from loguru import logger


MAX_KV_VALUE_BYTES = 25 * 1024 * 1024


@dataclass
class CloudflareKVConfig:
    account_id: str
    namespace_id: str
    api_token: str
    enabled: bool = False
    key_prefix: str = "patent-report"


class CloudflareKVStorage:
    def __init__(self, config: CloudflareKVConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.config.api_token}",
            }
        )

    @property
    def enabled(self) -> bool:
        return (
            self.config.enabled
            and bool(self.config.account_id)
            and bool(self.config.namespace_id)
            and bool(self.config.api_token)
        )

    def _build_value_url(self, key: str) -> str:
        encoded_key = quote(key, safe="")
        return (
            f"https://api.cloudflare.com/client/v4/accounts/{self.config.account_id}"
            f"/storage/kv/namespaces/{self.config.namespace_id}/values/{encoded_key}"
        )

    def build_patent_pdf_key(self, patent_number: str) -> str:
        normalized = "".join(ch for ch in patent_number.upper() if ch.isalnum() or ch in {"-", "_"})
        return f"{self.config.key_prefix}:{normalized}:pdf"

    def get_bytes(self, key: str) -> Optional[bytes]:
        if not self.enabled:
            return None

        url = self._build_value_url(key)
        try:
            resp = self.session.get(url, timeout=30)
        except requests.RequestException as exc:
            logger.warning(f"[CF-KV] 读取请求失败，key={key}，错误：{exc}")
            return None

        if resp.status_code == 404:
            return None

        if not resp.ok:
            logger.warning(
                f"[CF-KV] 读取失败，key={key}，status={resp.status_code}，body={resp.text[:200]}"
            )
            return None

        return resp.content

    def put_bytes(self, key: str, content: bytes, content_type: str = "application/pdf") -> bool:
        if not self.enabled:
            return False

        if len(content) > MAX_KV_VALUE_BYTES:
            logger.warning(
                f"[CF-KV] 跳过写入，key={key}，内容过大（{len(content)} bytes > {MAX_KV_VALUE_BYTES}）"
            )
            return False

        url = self._build_value_url(key)
        headers = {"Content-Type": content_type}
        try:
            resp = self.session.put(url, data=content, headers=headers, timeout=60)
        except requests.RequestException as exc:
            logger.warning(f"[CF-KV] 写入请求失败，key={key}，错误：{exc}")
            return False

        if not resp.ok:
            logger.warning(
                f"[CF-KV] 写入失败，key={key}，status={resp.status_code}，body={resp.text[:200]}"
            )
            return False

        return True
