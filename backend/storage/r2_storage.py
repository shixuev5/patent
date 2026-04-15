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
        return f"{self.config.key_prefix}/{pn}/ai_analysis.pdf"

    def build_analysis_json_key(self, patent_number: str) -> str:
        pn = self._clean_token((patent_number or "").upper(), fallback="unknown")
        return f"{self.config.key_prefix}/{pn}/ai_analysis.json"

    def build_patent_json_key(self, patent_number: str) -> str:
        pn = self._clean_token((patent_number or "").upper(), fallback="unknown")
        return f"{self.config.key_prefix}/{pn}/patent.json"

    def build_ai_review_pdf_key(self, patent_number: str) -> str:
        pn = self._clean_token((patent_number or "").upper(), fallback="unknown")
        return f"{self.config.key_prefix}/{pn}/ai_review.pdf"

    def build_ai_review_json_key(self, patent_number: str) -> str:
        pn = self._clean_token((patent_number or "").upper(), fallback="unknown")
        return f"{self.config.key_prefix}/{pn}/ai_review.json"

    def build_ai_reply_pdf_key(self, patent_number: str) -> str:
        pn = self._clean_token((patent_number or "").upper(), fallback="unknown")
        return f"{self.config.key_prefix}/{pn}/ai_reply.pdf"

    def build_ai_reply_json_key(self, patent_number: str) -> str:
        pn = self._clean_token((patent_number or "").upper(), fallback="unknown")
        return f"{self.config.key_prefix}/{pn}/ai_reply.json"

    def build_upload_key(self, task_id: str, filename: str) -> str:
        tid = self._clean_token(task_id, fallback="task")
        fname = self._clean_token(filename or "upload.bin", fallback="upload.bin")
        date_prefix = datetime.utcnow().strftime("%Y/%m/%d")
        return f"{self.config.key_prefix}/uploads/{date_prefix}/{tid}-{fname}"

    def build_avatar_key(self, owner_id: str, filename: str) -> str:
        user_id = self._clean_token(owner_id, fallback="user")
        fname = self._clean_token(filename or "avatar.bin", fallback="avatar.bin")
        date_prefix = datetime.utcnow().strftime("%Y/%m/%d")
        return f"avatar/{date_prefix}/{user_id}-{fname}"

    def get_bytes(self, key: str, *, log_missing: bool = True) -> Optional[bytes]:
        if not self.enabled or not self.client:
            return None
        try:
            response = self.client.get_object(Bucket=self.config.bucket, Key=key)
            body = response.get("Body")
            data = body.read() if body else None
            if data:
                logger.info(f"[R2] 文件已成功读取，key={key}，大小={len(data)}字节")
            return data
        except ClientError as exc:
            error_code = (
                exc.response.get("Error", {}).get("Code", "") if hasattr(exc, "response") else ""
            )
            if str(error_code) in {"NoSuchKey", "404"}:
                if log_missing:
                    logger.debug(f"[R2] 文件不存在，key={key}")
                return None
            logger.warning(f"[R2] 读取失败，key={key}，错误：{exc}")
            return None
        except BotoCoreError as exc:
            logger.warning(f"[R2] 读取失败，key={key}，错误：{exc}")
            return None

    def key_exists(self, key: str) -> bool:
        if not self.enabled or not self.client:
            return False
        try:
            self.client.head_object(Bucket=self.config.bucket, Key=key)
            return True
        except ClientError as exc:
            error_code = (
                exc.response.get("Error", {}).get("Code", "") if hasattr(exc, "response") else ""
            )
            if str(error_code) in {"404", "NoSuchKey", "NotFound"}:
                return False
            logger.warning(f"[R2] key_exists 失败，key={key}，错误：{exc}")
            return False
        except BotoCoreError as exc:
            logger.warning(f"[R2] key_exists 失败，key={key}，错误：{exc}")
            return False

    def list_keys(self, prefix: str, max_keys: int = 1000) -> list[str]:
        if not self.enabled or not self.client:
            return []
        keys: list[str] = []
        continuation_token = None
        try:
            while True:
                kwargs = {
                    "Bucket": self.config.bucket,
                    "Prefix": str(prefix or ""),
                    "MaxKeys": int(max(1, min(1000, max_keys))),
                }
                if continuation_token:
                    kwargs["ContinuationToken"] = continuation_token
                response = self.client.list_objects_v2(**kwargs)
                for item in response.get("Contents", []) or []:
                    key = str(item.get("Key", "")).strip()
                    if key:
                        keys.append(key)
                if not response.get("IsTruncated"):
                    break
                continuation_token = response.get("NextContinuationToken")
                if not continuation_token:
                    break
        except (ClientError, BotoCoreError) as exc:
            logger.warning(f"[R2] list_keys 失败，prefix={prefix}，错误：{exc}")
            return keys
        return keys

    def copy_key(self, source_key: str, target_key: str) -> bool:
        if not self.enabled or not self.client:
            return False
        try:
            self.client.copy_object(
                Bucket=self.config.bucket,
                CopySource={"Bucket": self.config.bucket, "Key": source_key},
                Key=target_key,
            )
            logger.info(f"[R2] 文件已复制，source={source_key} target={target_key}")
            return True
        except (ClientError, BotoCoreError) as exc:
            logger.warning(f"[R2] 复制失败，source={source_key} target={target_key}，错误：{exc}")
            return False

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
            logger.info(f"[R2] 文件已成功存储，key={key}")
            return True
        except (ClientError, BotoCoreError) as exc:
            logger.warning(f"[R2] 写入失败，key={key}，错误：{exc}")
            return False

    def delete_key(self, key: str) -> bool:
        if not self.enabled or not self.client:
            return False
        try:
            self.client.delete_object(Bucket=self.config.bucket, Key=key)
            logger.info(f"[R2] 文件已删除，key={key}")
            return True
        except (ClientError, BotoCoreError) as exc:
            logger.warning(f"[R2] 删除失败，key={key}，错误：{exc}")
            return False
