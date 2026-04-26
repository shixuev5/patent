from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
from pathlib import Path

from dotenv import load_dotenv

from .credential_store import build_owner_credential_store_from_env, owner_credential_local_path

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _default_download_dir() -> Path:
    raw = str(os.getenv("IM_GATEWAY_DOWNLOAD_DIR", str(ROOT_DIR / "data" / "im_gateway")) or "").strip()
    return Path(raw)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Listen for one inbound WeChat message and reply with a probe file.")
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--download-dir", default=str(_default_download_dir()))
    parser.add_argument("--file-name", default="local-probe.txt")
    parser.add_argument("--file-text", default="local file reply from codex\n")
    parser.add_argument("--file-path", default="")
    parser.add_argument("--expected-peer-id", default="")
    return parser


async def _restore_credentials(owner_id: str, download_dir: Path) -> Path:
    os.environ["R2_ENABLED"] = "true"
    store = build_owner_credential_store_from_env(download_dir=download_dir, owner_id=owner_id)
    if store is None:
        raise RuntimeError("R2 credential store is not configured")
    restored = store.restore_local_credentials()
    local_path = owner_credential_local_path(download_dir=download_dir, owner_id=owner_id)
    print(
        f"[reply-probe] credential restore: owner={owner_id} restored={restored} "
        f"path={local_path} r2_key={store.r2_key}",
        flush=True,
    )
    return local_path


async def _run(args: argparse.Namespace) -> int:
    owner_id = str(args.owner_id).strip()
    expected_peer_id = str(args.expected_peer_id or "").strip()
    file_path_text = str(args.file_path or "").strip()
    if file_path_text:
        file_path = Path(file_path_text).expanduser().resolve()
        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError(f"reply file not found: {file_path}")
        file_name = str(args.file_name or "").strip() or file_path.name
        file_bytes = file_path.read_bytes()
    else:
        file_name = str(args.file_name or "").strip() or "local-probe.txt"
        file_bytes = str(args.file_text or "").encode("utf-8")
    download_dir = Path(str(args.download_dir)).expanduser().resolve()
    cred_path = await _restore_credentials(owner_id, download_dir)

    try:
        from wechatbot import WeChatBot
    except Exception as exc:
        print(f"[reply-probe] failed to import wechatbot-sdk: {exc}", flush=True)
        return 2

    bot = WeChatBot(cred_path=str(cred_path))
    replied = False

    async def stop_later() -> None:
        while not replied:
            await asyncio.sleep(0.2)
        await asyncio.sleep(1.0)
        bot.stop()

    @bot.on_message
    async def handle(msg) -> None:
        nonlocal replied
        peer_id = str(getattr(msg, "user_id", "") or "").strip()
        text = str(getattr(msg, "text", "") or "").strip()
        context_token = str(getattr(msg, "_context_token", "") or "").strip()
        print(
            f"[reply-probe] incoming peer_id={peer_id} context_token_len={len(context_token)} text={text!r}",
            flush=True,
        )
        if replied:
            return
        if expected_peer_id and peer_id != expected_peer_id:
            print(f"[reply-probe] ignored peer_id={peer_id} expected_peer_id={expected_peer_id}", flush=True)
            return
        replied = True
        try:
            await bot.reply_media(msg, {"file": file_bytes, "file_name": file_name})
            print(f"[reply-probe] reply_media ok peer_id={peer_id} file_name={file_name}", flush=True)
        except Exception as exc:
            print(f"[reply-probe] reply_media failed peer_id={peer_id} error={type(exc).__name__}: {exc}", flush=True)
            with contextlib.suppress(Exception):
                await bot.reply(msg, f"local reply_media failed: {type(exc).__name__}: {exc}")

    try:
        await bot.login(force=False)
        creds = bot.get_credentials()
        print(
            f"[reply-probe] login ok: account_id={getattr(creds, 'account_id', '') or '-'} "
            f"user_id={getattr(creds, 'user_id', '') or '-'} base_url={getattr(creds, 'base_url', '') or '-'}",
            flush=True,
        )
        stopper = asyncio.create_task(stop_later())
        try:
            await bot.start()
        finally:
            stopper.cancel()
            with contextlib.suppress(Exception):
                await stopper
        print(f"[reply-probe] stopped replied={replied}", flush=True)
        return 0 if replied else 1
    finally:
        with contextlib.suppress(Exception):
            bot.stop()


def main() -> int:
    return asyncio.run(_run(_build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
