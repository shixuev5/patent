"""
Cloudflare R2 storage adapter (S3 compatible).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from loguru import logger

try:
    import boto3
    from botocore.client import Config
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover - guarded by runtime env
    boto3 = None
    Config = None
    BotoCoreError = Exception
    ClientError = Exception


@dataclass
class R2Config:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    enabled: bool = False
    region: str = "auto"
    key_prefix: str = "patent"


class R2Storage:
    def __init__(self, config: R2Config):
        self.config = config
        self.client = None

        if self.enabled:
            if boto3 is None:
                raise RuntimeError(
                    "boto3 is not installed. Please add dependency `boto3` to use R2."
                )
            self.client = boto3.client(
                "s3",
                endpoint_url=self.config.endpoint_url,
                aws_access_key_id=self.config.access_key_id,
                aws_secret_access_key=self.config.secret_access_key,
                region_name=self.config.region,
                config=Config(signature_version="s3v4"),
            )

    @property
    def enabled(self) -> bool:
        return (
            self.config.enabled
            and bool(self.config.endpoint_url)
            and bool(self.config.access_key_id)
            and bool(self.config.secret_access_key)
            and bool(self.config.bucket)
        )

    @staticmethod
    def _clean_token(value: str, fallback: str = "unknown") -> str:
        if not value:
            return fallback
        cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
        return cleaned.strip("-._") or fallback

    def build_patent_pdf_key(self, patent_number: str) -> str:
        pn = self._clean_token((patent_number or "").upper(), fallback="unknown")
        return f"{self.config.key_prefix}/reports/{pn}.pdf"

    def build_upload_key(self, task_id: str, filename: str) -> str:
        tid = self._clean_token(task_id, fallback="task")
        fname = self._clean_token(filename or "upload.bin", fallback="upload.bin")
        date_prefix = datetime.utcnow().strftime("%Y/%m/%d")
        return f"{self.config.key_prefix}/uploads/{date_prefix}/{tid}-{fname}"

    def get_bytes(self, key: str) -> Optional[bytes]:
        if not self.enabled or not self.client:
            return None
        try:
            response = self.client.get_object(Bucket=self.config.bucket, Key=key)
            body = response.get("Body")
            return body.read() if body else None
        except ClientError as exc:
            error_code = (
                exc.response.get("Error", {}).get("Code", "") if hasattr(exc, "response") else ""
            )
            if str(error_code) in {"NoSuchKey", "404"}:
                return None
            logger.warning(f"[R2] 读取失败，key={key}，错误：{exc}")
            return None
        except BotoCoreError as exc:
            logger.warning(f"[R2] 读取失败，key={key}，错误：{exc}")
            return None

    def put_bytes(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> bool:
        if not self.enabled or not self.client:
            return False
        try:
            self.client.put_object(
                Bucket=self.config.bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
            )
            return True
        except (ClientError, BotoCoreError) as exc:
            logger.warning(f"[R2] 写入失败，key={key}，错误：{exc}")
            return False
