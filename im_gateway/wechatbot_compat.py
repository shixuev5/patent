from __future__ import annotations

import hashlib
import os
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

from wechatbot import WeChatBot
from wechatbot.crypto import (
    encode_aes_key_base64,
    encode_aes_key_hex,
    encrypt_aes_ecb,
    generate_aes_key,
)
from wechatbot.errors import MediaError
from wechatbot.protocol import CDN_BASE_URL
from wechatbot.types import CDNMedia, Credentials, UploadResult


class OpenClawCompatibleWeChatBot(WeChatBot):
    """Wechat bot client patched to follow the current OpenClaw upload protocol."""

    async def _cdn_upload(
        self,
        creds: Credentials,
        data: bytes,
        user_id: str,
        media_type: int,
    ) -> UploadResult:
        aes_key = generate_aes_key()
        ciphertext = encrypt_aes_ecb(data, aes_key)
        filekey = os.urandom(16).hex()
        raw_md5 = hashlib.md5(data).hexdigest()

        upload_info = await self._api.get_upload_url(
            creds.base_url,
            creds.token,
            filekey=filekey,
            media_type=media_type,
            to_user_id=user_id,
            rawsize=len(data),
            rawfilemd5=raw_md5,
            filesize=len(ciphertext),
            no_need_thumb=True,
            aeskey=encode_aes_key_hex(aes_key),
        )

        print(
            "[im-gateway] getuploadurl response: "
            f"user_id={user_id} media_type={media_type} raw_size={len(data)} "
            f"encrypted_size={len(ciphertext)} payload={_sanitize_upload_info(upload_info)}"
        )
        print(
            "[im-gateway] resolved upload target: "
            f"user_id={user_id} media_type={media_type} "
            f"target={_sanitize_upload_target(upload_info, filekey=filekey)}"
        )
        encrypt_query_param = await self._upload_ciphertext(upload_info, ciphertext, filekey=filekey)

        return UploadResult(
            media=CDNMedia(
                encrypt_query_param=encrypt_query_param,
                aes_key=encode_aes_key_base64(aes_key),
                encrypt_type=1,
            ),
            aes_key=aes_key,
            encrypted_file_size=len(ciphertext),
        )

    async def _upload_ciphertext(self, upload_info: Any, ciphertext: bytes, *, filekey: str) -> str:
        timeout = aiohttp.ClientTimeout(total=60)
        last_error: Exception | None = None
        upload_targets = _resolve_upload_targets(upload_info, filekey=filekey)
        for target in upload_targets:
            upload_url = target["url"]
            upload_mode = str(target["mode"])
            for attempt in range(1, 4):
                try:
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.post(
                            upload_url,
                            data=ciphertext,
                            headers={"Content-Type": "application/octet-stream"},
                        ) as resp:
                            if resp.status >= 400:
                                err_msg = resp.headers.get("x-error-message", f"HTTP {resp.status}")
                                body_preview = _preview(await resp.text(), keep=120)
                                raise MediaError(
                                    f"CDN upload failed via {upload_mode}: {err_msg}"
                                    + (f" body={body_preview}" if body_preview else "")
                                )
                            encrypt_query_param = resp.headers.get("x-encrypted-param")
                            if not encrypt_query_param:
                                raise MediaError(
                                    f"CDN upload succeeded via {upload_mode} but x-encrypted-param header missing"
                                )
                            return encrypt_query_param
                except MediaError as exc:
                    last_error = exc
                    if "http 5" not in str(exc).lower():
                        raise
                    if attempt < 3:
                        continue
                except Exception as exc:
                    last_error = exc
                    if attempt < 3:
                        continue
            if last_error is not None:
                print(
                    "[im-gateway] upload target attempt exhausted: "
                    f"target={upload_mode} error={last_error}"
                )
        if isinstance(last_error, Exception):
            raise last_error
        raise MediaError("CDN upload failed")


def build_wechat_bot(**kwargs: Any) -> OpenClawCompatibleWeChatBot:
    return OpenClawCompatibleWeChatBot(**kwargs)


def _resolve_upload_targets(upload_info: Any, *, filekey: str) -> list[dict[str, str]]:
    payload = upload_info if isinstance(upload_info, dict) else {}
    upload_full_url = str(payload.get("upload_full_url") or "").strip()
    if upload_full_url:
        return [{"mode": "upload_full_url", "url": upload_full_url}]

    upload_param = str(payload.get("upload_param") or "").strip()
    if upload_param:
        default_url = _build_upload_url(upload_param, filekey=filekey, strict=False)
        strict_url = _build_upload_url(upload_param, filekey=filekey, strict=True)
        targets = [{"mode": "upload_param_default_quote", "url": default_url}]
        if strict_url != default_url:
            targets.append({"mode": "upload_param_strict_quote", "url": strict_url})
        return targets

    raise MediaError("getuploadurl did not return upload URL")


def _resolve_upload_url(upload_info: Any, *, filekey: str) -> str:
    return _resolve_upload_targets(upload_info, filekey=filekey)[0]["url"]


def _build_upload_url(upload_param: str, *, filekey: str, strict: bool) -> str:
    safe = "" if strict else "/"
    return (
        f"{CDN_BASE_URL}/upload"
        f"?encrypted_query_param={quote(upload_param, safe=safe)}"
        f"&filekey={quote(filekey)}"
    )


def _sanitize_upload_info(upload_info: Any) -> dict[str, Any]:
    payload = upload_info if isinstance(upload_info, dict) else {}
    upload_full_url = str(payload.get("upload_full_url") or "").strip()
    upload_param = str(payload.get("upload_param") or "").strip()
    thumb_upload_param = str(payload.get("thumb_upload_param") or "").strip()
    return {
        "keys": sorted(str(key) for key in payload.keys()),
        "has_upload_full_url": bool(upload_full_url),
        "upload_full_url_origin": _url_origin(upload_full_url) if upload_full_url else None,
        "upload_full_url_path": _url_path(upload_full_url) if upload_full_url else None,
        "has_upload_param": bool(upload_param),
        "upload_param_preview": _preview(upload_param),
        "has_thumb_upload_param": bool(thumb_upload_param),
        "thumb_upload_param_preview": _preview(thumb_upload_param),
    }


def _sanitize_upload_target(upload_info: Any, *, filekey: str) -> dict[str, Any]:
    targets = _resolve_upload_targets(upload_info, filekey=filekey)
    primary = targets[0]
    return {
        "mode": primary["mode"],
        "origin": _url_origin(primary["url"]),
        "path": _url_path(primary["url"]),
        "candidate_count": len(targets),
        "fallback_mode": targets[1]["mode"] if len(targets) > 1 else None,
    }


def _url_origin(value: str) -> str | None:
    parsed = urlparse(str(value or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _url_path(value: str) -> str | None:
    parsed = urlparse(str(value or "").strip())
    return parsed.path or None


def _preview(value: str, *, keep: int = 24) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= keep:
        return text
    return f"{text[:keep]}...({len(text)})"


__all__ = ["OpenClawCompatibleWeChatBot", "build_wechat_bot"]
