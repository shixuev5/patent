"""Email sender abstractions and provider implementations."""

from __future__ import annotations

import base64
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Any, Dict, Optional

import requests


@dataclass
class EmailSendResult:
    provider: str
    provider_message_id: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


class EmailSender(ABC):
    @abstractmethod
    def send(
        self,
        message: EmailMessage,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult:
        """Send a prepared email message."""


@dataclass
class SmtpSenderConfig:
    host: str
    port: int
    username: str
    password: str
    from_address: str
    from_name: str = ""
    use_tls: bool = False
    use_ssl: bool = False

    def validate(self) -> None:
        if not self.host:
            raise ValueError("SMTP_HOST 未配置。")
        if int(self.port or 0) <= 0:
            raise ValueError("SMTP_PORT 配置无效。")
        if not self.from_address:
            raise ValueError("SMTP_FROM_ADDRESS 未配置。")
        if self.use_ssl and self.use_tls:
            raise ValueError("SMTP_USE_SSL 与 SMTP_USE_TLS 不能同时启用。")


class SmtpEmailSender(EmailSender):
    def __init__(self, config: SmtpSenderConfig):
        config.validate()
        self.config = config

    def send(
        self,
        message: EmailMessage,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult:
        smtp_cls = smtplib.SMTP_SSL if self.config.use_ssl else smtplib.SMTP
        with smtp_cls(self.config.host, self.config.port, timeout=30) as server:
            if not self.config.use_ssl:
                server.ehlo()
                if self.config.use_tls:
                    server.starttls()
                    server.ehlo()
            if self.config.username:
                server.login(self.config.username, self.config.password)
            server.send_message(message)
        return EmailSendResult(provider="smtp")


@dataclass
class BrevoSenderConfig:
    api_key: str
    api_base_url: str = "https://api.brevo.com/v3"
    from_address: str = ""
    from_name: str = ""
    timeout_seconds: int = 30

    def validate(self) -> None:
        if not self.api_key:
            raise ValueError("BREVO_API_KEY 未配置。")
        if not self.api_base_url:
            raise ValueError("BREVO_API_BASE_URL 未配置。")
        if not self.from_address:
            raise ValueError("BREVO_FROM_ADDRESS 未配置。")


class BrevoEmailSender(EmailSender):
    def __init__(self, config: BrevoSenderConfig):
        config.validate()
        self.config = config

    def send(
        self,
        message: EmailMessage,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EmailSendResult:
        payload = self._build_payload(message, metadata=metadata or {})
        response = requests.post(
            f"{self.config.api_base_url.rstrip('/')}/smtp/email",
            headers={
                "accept": "application/json",
                "api-key": self.config.api_key,
                "content-type": "application/json",
            },
            json=payload,
            timeout=max(5, int(self.config.timeout_seconds or 30)),
        )
        response.raise_for_status()
        body = response.json() if response.content else {}
        return EmailSendResult(
            provider="brevo",
            provider_message_id=str(body.get("messageId") or "").strip() or None,
            raw_response=body if isinstance(body, dict) else None,
        )

    def _build_payload(self, message: EmailMessage, *, metadata: Dict[str, Any]) -> Dict[str, Any]:
        to_entries = [
            {"email": address}
            for _name, address in getaddresses(message.get_all("To", []))
            if str(address or "").strip()
        ]
        payload: Dict[str, Any] = {
            "sender": {
                "email": self.config.from_address,
            },
            "to": to_entries,
            "subject": str(message.get("Subject") or "").strip(),
            "textContent": self._extract_text_content(message),
        }
        html_content = self._extract_html_content(message)
        if html_content:
            payload["htmlContent"] = html_content
        if self.config.from_name:
            payload["sender"]["name"] = self.config.from_name

        tags = [item for item in self._build_tags(metadata) if item]
        if tags:
            payload["tags"] = tags

        attachments = self._build_attachments(message)
        if attachments:
            payload["attachment"] = attachments
        return payload

    def _extract_text_content(self, message: EmailMessage) -> str:
        if message.is_multipart():
            body = message.get_body(preferencelist=("plain",))
            return body.get_content() if body is not None else ""
        return message.get_content()

    def _extract_html_content(self, message: EmailMessage) -> str:
        if message.is_multipart():
            body = message.get_body(preferencelist=("html",))
            return body.get_content() if body is not None else ""
        content_type = str(message.get_content_type() or "").strip().lower()
        if content_type == "text/html":
            return message.get_content()
        return ""

    def _build_attachments(self, message: EmailMessage) -> list[dict[str, str]]:
        attachments: list[dict[str, str]] = []
        for item in message.iter_attachments():
            content = item.get_payload(decode=True) or b""
            if not content:
                continue
            attachments.append(
                {
                    "name": str(item.get_filename() or "attachment.bin"),
                    "content": base64.b64encode(content).decode("ascii"),
                }
            )
        return attachments

    def _build_tags(self, metadata: Dict[str, Any]) -> list[str]:
        task_id = str(metadata.get("task_id") or "").strip()
        owner_id = str(metadata.get("owner_id") or "").strip()
        task_type = str(metadata.get("task_type") or "").strip()
        terminal_status = str(metadata.get("terminal_status") or "").strip()
        return [
            f"task_id:{task_id}" if task_id else "",
            f"owner_id:{owner_id}" if owner_id else "",
            f"task_type:{task_type}" if task_type else "",
            f"terminal_status:{terminal_status}" if terminal_status else "",
        ]
