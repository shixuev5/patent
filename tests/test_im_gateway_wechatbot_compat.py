from __future__ import annotations

import asyncio
from types import SimpleNamespace

from im_gateway.wechatbot_compat import (
    OpenClawCompatibleWeChatBot,
    _resolve_upload_targets,
    _resolve_upload_url,
    _sanitize_upload_info,
    _sanitize_upload_target,
)


def test_resolve_upload_url_prefers_upload_full_url() -> None:
    resolved = _resolve_upload_url(
        {
            "upload_full_url": "https://cdn.example/upload?sig=abc",
            "upload_param": "legacy-param",
        },
        filekey="file-key",
    )

    assert resolved == "https://cdn.example/upload?sig=abc"


def test_resolve_upload_url_falls_back_to_legacy_upload_param() -> None:
    resolved = _resolve_upload_url({"upload_param": "legacy-param"}, filekey="file-key")

    assert resolved.endswith("encrypted_query_param=legacy-param&filekey=file-key")


def test_resolve_upload_url_matches_sdk_quoting_for_slash_in_upload_param() -> None:
    resolved = _resolve_upload_url({"upload_param": "legacy/param+value"}, filekey="file-key")

    assert resolved.endswith("encrypted_query_param=legacy/param%2Bvalue&filekey=file-key")


def test_resolve_upload_targets_adds_strict_quote_fallback_when_needed() -> None:
    targets = _resolve_upload_targets({"upload_param": "legacy/param+value"}, filekey="file-key")

    assert [target["mode"] for target in targets] == [
        "upload_param_default_quote",
        "upload_param_strict_quote",
    ]
    assert targets[1]["url"].endswith("encrypted_query_param=legacy%2Fparam%2Bvalue&filekey=file-key")


def test_cdn_upload_uses_upload_full_url_from_openclaw_response(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeApi:
        async def get_upload_url(self, *args, **kwargs):
            captured["request"] = kwargs
            return {
                "upload_full_url": "https://cdn.example/upload?sig=abc",
                "upload_param": "legacy-param",
            }

    bot = OpenClawCompatibleWeChatBot()
    bot._api = FakeApi()

    async def fake_upload_ciphertext(upload_info, ciphertext: bytes, *, filekey: str) -> str:
        captured["upload_url"] = _resolve_upload_url(upload_info, filekey=filekey)
        captured["ciphertext_len"] = len(ciphertext)
        return "encrypted-param"

    monkeypatch.setattr(bot, "_upload_ciphertext", fake_upload_ciphertext)

    result = asyncio.run(
        bot._cdn_upload(
            SimpleNamespace(base_url="https://ilink.example", token="token"),
            b"hello world",
            "wx-user-1",
            3,
        )
    )

    assert captured["upload_url"] == "https://cdn.example/upload?sig=abc"
    assert captured["request"]["to_user_id"] == "wx-user-1"
    assert result.media.encrypt_query_param == "encrypted-param"


def test_upload_ciphertext_uses_post_method(monkeypatch) -> None:
    calls: list[tuple[str, bytes, dict[str, str]]] = []

    class FakeResponse:
        def __init__(self) -> None:
            self.status = 200
            self.headers = {"x-encrypted-param": "encrypted-param"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, *, data: bytes, headers: dict[str, str]):
            calls.append((url, data, headers))
            return FakeResponse()

    import im_gateway.wechatbot_compat as compat_module

    monkeypatch.setattr(compat_module.aiohttp, "ClientSession", FakeSession)

    bot = OpenClawCompatibleWeChatBot()
    encrypt_query_param = asyncio.run(
        bot._upload_ciphertext(
            {"upload_full_url": "https://cdn.example/upload?sig=abc"},
            b"ciphertext",
            filekey="file-key",
        )
    )

    assert encrypt_query_param == "encrypted-param"
    assert calls == [("https://cdn.example/upload?sig=abc", b"ciphertext", {"Content-Type": "application/octet-stream"})]


def test_upload_ciphertext_falls_back_to_strict_quote_on_server_error(monkeypatch) -> None:
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
            self.status = status
            self.headers = headers or {}

        async def text(self) -> str:
            return "server exploded"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, *, data: bytes, headers: dict[str, str]):
            calls.append(url)
            if len(calls) < 4:
                return FakeResponse(500, {"x-error-message": "HTTP 500"})
            return FakeResponse(200, {"x-encrypted-param": "encrypted-param"})

    import im_gateway.wechatbot_compat as compat_module

    monkeypatch.setattr(compat_module.aiohttp, "ClientSession", FakeSession)

    bot = OpenClawCompatibleWeChatBot()
    encrypt_query_param = asyncio.run(
        bot._upload_ciphertext(
            {"upload_param": "legacy/param+value"},
            b"ciphertext",
            filekey="file-key",
        )
    )

    assert encrypt_query_param == "encrypted-param"
    assert calls[:3] == [
        "https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param=legacy/param%2Bvalue&filekey=file-key"
    ] * 3
    assert calls[3] == (
        "https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param=legacy%2Fparam%2Bvalue&filekey=file-key"
    )


def test_sanitize_upload_info_redacts_signed_values() -> None:
    sanitized = _sanitize_upload_info(
        {
            "upload_full_url": "https://cdn.example/upload?sig=abc&token=secret",
            "upload_param": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "thumb_upload_param": "thumb-secret",
        }
    )

    assert sanitized["has_upload_full_url"] is True
    assert sanitized["upload_full_url_origin"] == "https://cdn.example"
    assert sanitized["upload_full_url_path"] == "/upload"
    assert sanitized["upload_param_preview"] == "ABCDEFGHIJKLMNOPQRSTUVWX...(36)"
    assert sanitized["thumb_upload_param_preview"] == "thumb-secret"


def test_sanitize_upload_target_marks_selected_mode() -> None:
    sanitized = _sanitize_upload_target({"upload_full_url": "https://cdn.example/upload?sig=abc"}, filekey="file-key")

    assert sanitized == {
        "mode": "upload_full_url",
        "origin": "https://cdn.example",
        "path": "/upload",
        "candidate_count": 1,
        "fallback_mode": None,
    }
