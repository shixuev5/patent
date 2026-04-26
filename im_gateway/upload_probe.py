from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv

from .credential_store import build_owner_credential_store_from_env, owner_credential_local_path

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _owner_token(owner_id: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(owner_id or "").strip())
    token = token.strip("-_")
    return token or "owner"


def _default_download_dir() -> Path:
    raw = str(os.getenv("IM_GATEWAY_DOWNLOAD_DIR", str(ROOT_DIR / "data" / "im_gateway")) or "").strip()
    return Path(raw)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe real WeChat CDN upload/send_media locally with an owner credential.",
    )
    parser.add_argument("--owner-id", required=True, help="Owner id used by im-gateway, for example authing:69aaf...")
    parser.add_argument("--peer-id", required=True, help="Target WeChat peer id, for example o9cq80-...@im.wechat")
    parser.add_argument("--file", required=True, help="Local file path to upload")
    parser.add_argument(
        "--mode",
        choices=("upload", "send", "both"),
        default="upload",
        help="upload = bot.upload only; send = send_media only; both = upload then send_media",
    )
    parser.add_argument(
        "--context-token",
        default="",
        help="Required for send/both if this process has not observed an inbound message from the peer.",
    )
    parser.add_argument("--file-name", default="", help="Override outgoing file name; defaults to the local file basename.")
    parser.add_argument(
        "--download-dir",
        default=str(_default_download_dir()),
        help="Base im-gateway download dir used to restore credentials locally.",
    )
    return parser


async def _restore_credentials(owner_id: str, download_dir: Path) -> Path:
    local_cred_path = owner_credential_local_path(download_dir=download_dir, owner_id=owner_id)
    store = build_owner_credential_store_from_env(download_dir=download_dir, owner_id=owner_id)
    if store is None:
        print(f"[upload-probe] credential store disabled, using local credentials only: path={local_cred_path}")
        return local_cred_path
    restored = store.restore_local_credentials()
    print(
        "[upload-probe] credential restore: "
        f"owner={owner_id} restored={restored} path={local_cred_path} r2_key={store.r2_key}"
    )
    return local_cred_path


async def _run_probe(args: argparse.Namespace) -> int:
    owner_id = str(args.owner_id).strip()
    peer_id = str(args.peer_id).strip()
    file_path = Path(str(args.file)).expanduser().resolve()
    if not file_path.exists() or not file_path.is_file():
        print(f"[upload-probe] file not found: {file_path}")
        return 2

    download_dir = Path(str(args.download_dir)).expanduser().resolve()
    cred_path = await _restore_credentials(owner_id, download_dir)

    try:
        from wechatbot import WeChatBot
    except Exception as exc:
        print(f"[upload-probe] failed to import wechatbot-sdk: {exc}")
        return 2

    bot = WeChatBot(cred_path=str(cred_path))
    data = file_path.read_bytes()
    file_name = str(args.file_name or "").strip() or file_path.name

    print(
        "[upload-probe] input: "
        f"owner={owner_id} peer_id={peer_id} mode={args.mode} file={file_path} "
        f"file_name={file_name} size_bytes={len(data)} md5={hashlib.md5(data).hexdigest()}"
    )

    try:
        creds = await bot.login(force=False)
        print(
            "[upload-probe] login ok: "
            f"account_id={getattr(creds, 'account_id', '') or '-'} user_id={getattr(creds, 'user_id', '') or '-'} "
            f"base_url={getattr(creds, 'base_url', '') or '-'}"
        )

        if args.mode in {"upload", "both"}:
            print("[upload-probe] starting upload()")
            result = await bot.upload(data, peer_id, 3)
            print(
                "[upload-probe] upload ok: "
                f"encrypted_size={getattr(result, 'encrypted_file_size', None)} "
                f"encrypt_query_param_len={len(getattr(result.media, 'encrypt_query_param', '') or '')}"
            )

        if args.mode in {"send", "both"}:
            context_token = str(args.context_token or "").strip()
            if not context_token:
                print("[upload-probe] send_media requires --context-token in a standalone local process")
                return 2
            bot._context_tokens[peer_id] = context_token
            print("[upload-probe] starting send_media()")
            await bot.send_media(peer_id, {"file": data, "file_name": file_name})
            print("[upload-probe] send_media ok")

        return 0
    except Exception as exc:
        print(f"[upload-probe] failed: {type(exc).__name__}: {exc}")
        return 1
    finally:
        close = getattr(bot, "stop", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run_probe(args))


if __name__ == "__main__":
    raise SystemExit(main())
