from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

from backend.utils import _build_r2_storage


class R2CredentialStore:
    def __init__(
        self,
        *,
        r2_storage: Any,
        r2_key: str,
        local_path: Path,
        encryption_secret: str,
    ) -> None:
        self.r2_storage = r2_storage
        self.r2_key = str(r2_key or "").strip()
        self.local_path = Path(local_path)
        secret = str(encryption_secret or "")
        if not self.r2_key:
            raise ValueError("missing r2 credential key")
        if not secret:
            raise ValueError("missing credential encryption key")
        self._key = hashlib.sha256(secret.encode("utf-8")).digest()

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        return base64.b64decode(value.encode("ascii"))

    def _encrypt(self, content: bytes) -> bytes:
        nonce = get_random_bytes(12)
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(content)
        return json.dumps(
            {
                "v": 1,
                "n": self._b64encode(nonce),
                "c": self._b64encode(ciphertext),
                "t": self._b64encode(tag),
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def _decrypt(self, payload: bytes) -> bytes:
        try:
            data = json.loads(payload.decode("utf-8"))
            if int(data.get("v") or 0) != 1:
                raise ValueError("unsupported credential payload version")
            nonce = self._b64decode(str(data["n"]))
            ciphertext = self._b64decode(str(data["c"]))
            tag = self._b64decode(str(data["t"]))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid encrypted credential payload") from exc
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag)

    def restore_local_credentials(self) -> bool:
        payload = self.r2_storage.get_bytes(self.r2_key)
        if not payload:
            return False
        content = self._decrypt(payload)
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_path.write_bytes(content)
        with contextlib.suppress(OSError):
            self.local_path.chmod(0o600)
        return True

    def persist_local_credentials(self) -> bool:
        if not self.local_path.exists() or not self.local_path.is_file():
            return False
        payload = self._encrypt(self.local_path.read_bytes())
        return bool(
            self.r2_storage.put_bytes(
                self.r2_key,
                payload,
                content_type="application/octet-stream",
            )
        )

    def clear_remote_credentials(self) -> bool:
        return bool(self.r2_storage.delete_key(self.r2_key))

    def path_matches(self, path: Path | str | None) -> bool:
        if path is None:
            return False
        try:
            target = Path(path).expanduser().resolve(strict=False)
            local = self.local_path.expanduser().resolve(strict=False)
        except Exception:
            return False
        return target == local


def build_credential_store_from_env(*, download_dir: Path) -> Optional[R2CredentialStore]:
    r2_key = str(os.getenv("IM_GATEWAY_CRED_R2_KEY", "") or "").strip()
    if not r2_key:
        return None

    encryption_secret = os.getenv("IM_GATEWAY_CRED_ENCRYPTION_KEY", "")
    if not encryption_secret:
        raise RuntimeError("启用 R2 凭证持久化时必须设置 IM_GATEWAY_CRED_ENCRYPTION_KEY。")

    r2_storage = _build_r2_storage()
    if not getattr(r2_storage, "enabled", False):
        raise RuntimeError("启用 R2 凭证持久化时必须正确配置 R2_ENABLED/R2_* 环境变量。")

    local_path_text = str(os.getenv("IM_GATEWAY_CRED_PATH", "") or "").strip()
    local_path = Path(local_path_text) if local_path_text else (download_dir / "credentials.json")
    return R2CredentialStore(
        r2_storage=r2_storage,
        r2_key=r2_key,
        local_path=local_path,
        encryption_secret=encryption_secret,
    )
