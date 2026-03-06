"""
认证与授权功能
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import Header, HTTPException, Query

from backend.models import CurrentUser


AUTH_SECRET = os.getenv("AUTH_SECRET", "change-this-secret-in-production")
AUTH_TOKEN_TTL_DAYS = int(os.getenv("AUTH_TOKEN_TTL_DAYS", "30"))
APP_TZ_OFFSET_HOURS = int(os.getenv("APP_TZ_OFFSET_HOURS", "8"))
AUTHING_APP_ID = os.getenv("AUTHING_APP_ID", "").strip()
AUTHING_APP_SECRET = os.getenv("AUTHING_APP_SECRET", "").strip()
AUTHING_DOMAIN = os.getenv("AUTHING_DOMAIN", "").strip().rstrip("/")
AUTHING_JWKS_TTL_SECONDS = 3600
AUTHING_JWKS_TIMEOUT_SECONDS = 5.0

_jwks_cache_lock = threading.Lock()
_jwks_cache: Dict[str, Any] = {
    "fetched_at": 0.0,
    "keys": {},
}


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sign_payload(payload_b64: str) -> str:
    return hmac.new(AUTH_SECRET.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def _issue_token(user_id: str) -> tuple[str, int]:
    now = int(time.time())
    exp = now + AUTH_TOKEN_TTL_DAYS * 24 * 60 * 60
    payload = {
        "uid": user_id,
        "iat": now,
        "exp": exp,
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    signature = _sign_payload(payload_b64)
    return f"{payload_b64}.{signature}", exp


def _verify_token(token: str) -> Optional[dict]:
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None

    expected = _sign_payload(payload_b64)
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None

    exp = payload.get("exp")
    uid = payload.get("uid")
    if not uid or not isinstance(uid, str):
        return None
    if not isinstance(exp, int) or exp <= int(time.time()):
        return None
    return payload


def _parse_jwt(token: str) -> Tuple[Dict[str, Any], Dict[str, Any], bytes, str]:
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        signature = _b64url_decode(signature_b64)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as exc:
        raise HTTPException(status_code=401, detail=f"Authing token 格式无效：{exc}") from exc
    signing_input = f"{header_b64}.{payload_b64}"
    return header, payload, signature, signing_input


def _build_authing_owner_id(sub: str) -> str:
    return f"authing:{sub}"


def _expected_authing_issuer() -> str:
    if AUTHING_DOMAIN:
        return f"{AUTHING_DOMAIN}/oidc"
    return ""


def _expected_authing_jwks_url() -> str:
    if AUTHING_DOMAIN:
        return f"{AUTHING_DOMAIN}/oidc/.well-known/jwks.json"
    return ""


def _verify_authing_hs256(signature: bytes, signing_input: str):
    if not AUTHING_APP_SECRET:
        raise HTTPException(status_code=500, detail="AUTHING_APP_SECRET 未配置，无法验证 HS256 token。")
    expected = hmac.new(
        AUTHING_APP_SECRET.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Authing token 签名校验失败。")


def _rsa_public_key_from_jwk(jwk: Dict[str, Any]) -> rsa.RSAPublicKey:
    n_raw = str(jwk.get("n", "")).strip()
    e_raw = str(jwk.get("e", "")).strip()
    if not n_raw or not e_raw:
        raise HTTPException(status_code=401, detail="Authing JWKS 缺少 n/e 字段。")
    n = int.from_bytes(_b64url_decode(n_raw), byteorder="big")
    e = int.from_bytes(_b64url_decode(e_raw), byteorder="big")
    return rsa.RSAPublicNumbers(e, n).public_key()


def _fetch_authing_jwks() -> Dict[str, Dict[str, Any]]:
    now = time.time()
    with _jwks_cache_lock:
        fetched_at = float(_jwks_cache.get("fetched_at") or 0)
        if now - fetched_at < AUTHING_JWKS_TTL_SECONDS and _jwks_cache.get("keys"):
            return _jwks_cache["keys"]

    jwks_url = _expected_authing_jwks_url()
    if not jwks_url:
        raise HTTPException(status_code=500, detail="AUTHING_DOMAIN 未配置，无法验证 RS256 token。")

    try:
        response = requests.get(jwks_url, timeout=AUTHING_JWKS_TIMEOUT_SECONDS)
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"获取 Authing JWKS 失败：{exc}") from exc

    keys_by_kid: Dict[str, Dict[str, Any]] = {}
    for key in body.get("keys", []):
        if key.get("kty") != "RSA":
            continue
        kid = str(key.get("kid", "")).strip()
        if kid:
            keys_by_kid[kid] = key

    if not keys_by_kid:
        raise HTTPException(status_code=502, detail="Authing JWKS 返回为空。")

    with _jwks_cache_lock:
        _jwks_cache["fetched_at"] = now
        _jwks_cache["keys"] = keys_by_kid
    return keys_by_kid


def _verify_authing_rs256(header: Dict[str, Any], signature: bytes, signing_input: str):
    kid = str(header.get("kid", "")).strip()
    if not kid:
        raise HTTPException(status_code=401, detail="Authing token 缺少 kid。")
    jwks = _fetch_authing_jwks()
    jwk = jwks.get(kid)
    if not jwk:
        with _jwks_cache_lock:
            _jwks_cache["fetched_at"] = 0.0
        jwks = _fetch_authing_jwks()
        jwk = jwks.get(kid)
        if not jwk:
            raise HTTPException(status_code=401, detail="未找到匹配 kid 的 Authing 公钥。")

    public_key = _rsa_public_key_from_jwk(jwk)
    try:
        public_key.verify(
            signature,
            signing_input.encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Authing token RS256 验签失败：{exc}") from exc


def _validate_authing_claims(payload: Dict[str, Any]) -> Dict[str, Any]:
    now = int(time.time())
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise HTTPException(status_code=401, detail="Authing token 缺少 sub。")

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or int(exp) <= now:
        raise HTTPException(status_code=401, detail="Authing token 已过期。")

    nbf = payload.get("nbf")
    if isinstance(nbf, (int, float)) and int(nbf) > now:
        raise HTTPException(status_code=401, detail="Authing token 尚未生效。")

    expected_issuer = _expected_authing_issuer()
    if expected_issuer:
        iss = str(payload.get("iss", "")).strip().rstrip("/")
        if iss != expected_issuer.rstrip("/"):
            raise HTTPException(status_code=401, detail="Authing token issuer 不匹配。")

    if AUTHING_APP_ID:
        aud = payload.get("aud")
        aud_valid = (
            isinstance(aud, str) and aud == AUTHING_APP_ID
        ) or (
            isinstance(aud, list) and AUTHING_APP_ID in aud
        )
        if not aud_valid:
            raise HTTPException(status_code=401, detail="Authing token audience 不匹配。")

    return payload


def _verify_authing_id_token(id_token: str) -> Dict[str, Any]:
    if not id_token.strip():
        raise HTTPException(status_code=400, detail="idToken 不能为空。")

    if not AUTHING_APP_ID:
        raise HTTPException(status_code=500, detail="AUTHING_APP_ID 未配置。")

    header, payload, signature, signing_input = _parse_jwt(id_token)
    alg = str(header.get("alg", "")).upper()
    if alg == "HS256":
        _verify_authing_hs256(signature, signing_input)
    elif alg == "RS256":
        _verify_authing_rs256(header, signature, signing_input)
    else:
        raise HTTPException(status_code=401, detail=f"不支持的 Authing token 算法：{alg or 'unknown'}")

    return _validate_authing_claims(payload)


def _extract_token_from_request(
    authorization: Optional[str],
    query_token: Optional[str],
) -> Optional[str]:
    if authorization:
        value = authorization.strip()
        if value.lower().startswith("bearer "):
            token = value[7:].strip()
            if token:
                return token
    if query_token:
        return query_token.strip()
    return None


def _get_current_user(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> CurrentUser:
    raw_token = _extract_token_from_request(authorization, token)
    if not raw_token:
        raise HTTPException(status_code=401, detail="需要身份认证。")

    payload = _verify_token(raw_token)
    if not payload:
        raise HTTPException(status_code=401, detail="令牌无效或已过期。")

    return CurrentUser(user_id=payload["uid"])


def _quota_reset_utc() -> datetime:
    local_now = datetime.now(timezone.utc) + timedelta(hours=APP_TZ_OFFSET_HOURS)
    next_local_day = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_local_day - timedelta(hours=APP_TZ_OFFSET_HOURS)
