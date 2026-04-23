from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import unquote

import httpx
from backend.time_utils import utc_now

ROOT_DIR = Path(__file__).resolve().parent.parent
from dotenv import load_dotenv

from .backend_client import BackendClient
from .credential_store import (
    R2CredentialStore,
    build_owner_credential_store_from_env,
    owner_credential_local_path,
)

load_dotenv(ROOT_DIR / ".env")


DEFAULT_BACKEND_PORT = str(os.getenv("PORT", "7860") or "7860").strip() or "7860"
API_BASE_URL = os.getenv("API_BASE_URL", f"http://127.0.0.1:{DEFAULT_BACKEND_PORT}")
INTERNAL_GATEWAY_TOKEN = os.getenv("INTERNAL_GATEWAY_TOKEN", "").strip()
POLL_INTERVAL_SECONDS = max(2, int(os.getenv("IM_GATEWAY_POLL_INTERVAL_SECONDS", "8")))
DELIVERY_EVENT_WAIT_SECONDS = max(5.0, float(os.getenv("IM_GATEWAY_DELIVERY_EVENT_WAIT_SECONDS", "5") or "5"))
DELIVERY_FALLBACK_POLL_INTERVAL_SECONDS = max(
    POLL_INTERVAL_SECONDS,
    int(os.getenv("IM_GATEWAY_DELIVERY_FALLBACK_POLL_INTERVAL_SECONDS", "30") or "30"),
)
LOGIN_RETRY_SECONDS = max(3, int(os.getenv("IM_GATEWAY_LOGIN_RETRY_SECONDS", "5")))
INBOUND_REPLY_WAIT_SECONDS = max(1.0, float(os.getenv("IM_GATEWAY_INBOUND_REPLY_WAIT_SECONDS", "5") or "5"))
INBOUND_REQUEST_TIMEOUT_SECONDS = max(
    INBOUND_REPLY_WAIT_SECONDS + 1.0,
    float(os.getenv("IM_GATEWAY_INBOUND_REQUEST_TIMEOUT_SECONDS", "180") or "180"),
)
MEDIA_UPLOAD_MAX_ATTEMPTS = max(1, int(os.getenv("IM_GATEWAY_MEDIA_UPLOAD_MAX_ATTEMPTS", "3") or "3"))
MEDIA_UPLOAD_RETRY_DELAY_SECONDS = max(0.0, float(os.getenv("IM_GATEWAY_MEDIA_UPLOAD_RETRY_DELAY_SECONDS", "1") or "1"))
DOWNLOAD_DIR = Path(os.getenv("IM_GATEWAY_DOWNLOAD_DIR", str(ROOT_DIR / "data" / "im_gateway")))


def _owner_token(owner_id: str) -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(owner_id or "").strip())
    token = token.strip("-_")
    return token or "owner"


@dataclass
class OwnerRuntimeTarget:
    owner_id: str
    account_id: Optional[str] = None
    login_session_id: Optional[str] = None


class AccountRuntime:
    def __init__(
        self,
        *,
        owner_id: str,
        backend: BackendClient,
        download_dir: Path,
        background_task_tracker: Callable[[asyncio.Task[Any]], None],
    ) -> None:
        self.owner_id = str(owner_id or "").strip()
        self.backend = backend
        self.download_dir = Path(download_dir) / _owner_token(self.owner_id)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.local_credential_path = owner_credential_local_path(download_dir=download_dir, owner_id=self.owner_id)
        self.credential_store = build_owner_credential_store_from_env(download_dir=download_dir, owner_id=self.owner_id)
        self._track_background_task = background_task_tracker

        self.bot: Any = None
        self.task: Optional[asyncio.Task[Any]] = None
        self.current_account_id: Optional[str] = None
        self.current_wechat_user_id: Optional[str] = None
        self.desired_account_id: Optional[str] = None
        self.desired_login_session_id: Optional[str] = None
        self._attempt_login_session_id: Optional[str] = None
        self._stop_requested = False
        self._login_terminal_state_reported = False
        self._missing_credentials_reported = False

    async def apply_target(self, target: OwnerRuntimeTarget) -> None:
        target_account_id = str(target.account_id or "").strip() or None
        target_login_session_id = str(target.login_session_id or "").strip() or None

        login_session_changed = target_login_session_id != self.desired_login_session_id
        account_changed = target_account_id != self.desired_account_id
        needs_restart = False
        clear_credentials = False

        self.desired_account_id = target_account_id
        self.desired_login_session_id = target_login_session_id

        if target_login_session_id and login_session_changed:
            needs_restart = True
            clear_credentials = True
        elif account_changed and self.current_account_id and target_account_id and target_account_id != self.current_account_id:
            needs_restart = True

        if self.task is None or self.task.done():
            await self.start(clear_credentials=clear_credentials)
            return
        if needs_restart:
            await self.start(clear_credentials=clear_credentials, restart=True)

    async def start(self, *, clear_credentials: bool = False, restart: bool = False) -> None:
        if restart and self.task and not self.task.done():
            await self._cancel_task(self.task)
            self.task = None
        if self.task is not None and not self.task.done():
            return
        if clear_credentials:
            await self._clear_credentials()
        self._stop_requested = False
        self.task = asyncio.create_task(self._run_loop(), name=f"wechat-owner-{_owner_token(self.owner_id)}")
        self._track_background_task(self.task)

    async def stop(self, *, clear_credentials: bool) -> None:
        self._stop_requested = True
        self.desired_login_session_id = None
        self.desired_account_id = None
        self._attempt_login_session_id = None
        self._missing_credentials_reported = False
        if self.task is not None and not self.task.done():
            await self._cancel_task(self.task)
        self.task = None
        self.bot = None
        self.current_account_id = None
        self.current_wechat_user_id = None
        if clear_credentials:
            await self._clear_credentials()

    async def send_delivery_job(self, job: Dict[str, Any]) -> None:
        bot = self.bot
        if bot is None:
            raise RuntimeError(f"owner {self.owner_id} runtime is offline")

        delivery_job_id = str(job.get("deliveryJobId") or "").strip()
        binding = job.get("binding") if isinstance(job.get("binding"), dict) else {}
        account_id = str(binding.get("accountId") or "").strip()
        peer_id = str(binding.get("peerId") or "").strip()
        if not peer_id:
            raise RuntimeError("missing peerId")
        if account_id and self.current_account_id and account_id != self.current_account_id:
            raise RuntimeError(f"runtime account mismatch: expected {account_id}, got {self.current_account_id}")

        task = job.get("task") if isinstance(job.get("task"), dict) else {}
        download_path = str(task.get("downloadPath") or "").strip()
        if download_path:
            try:
                await self._update_delivery_job_progress(
                    delivery_job_id,
                    stage="downloading_artifact",
                    stage_details={"startedAt": utc_now().isoformat(), "downloadPath": download_path},
                )
                await self._send_delivery_artifact(bot=bot, peer_id=peer_id, download_path=download_path, delivery_job_id=delivery_job_id)
            except Exception as exc:
                await self._update_delivery_job_progress(
                    delivery_job_id,
                    stage="retry_waiting" if self._is_retryable_media_error(exc) else "failed",
                    stage_details={"error": str(exc)},
                )
                await bot.send(peer_id, self._file_delivery_failure_text())
        await self._update_delivery_job_progress(
            delivery_job_id,
            stage="sending_summary",
            stage_details={"startedAt": utc_now().isoformat()},
        )

        summary = self._build_delivery_text(job)
        if summary:
            await bot.send(peer_id, summary)

    async def _run_loop(self) -> None:
        while not self._stop_requested:
            self._attempt_login_session_id = self.desired_login_session_id
            self._login_terminal_state_reported = False
            bot = self._build_bot()
            self.bot = bot
            self._register_handlers(bot)

            try:
                force_login = bool(self._attempt_login_session_id)
                if not force_login and not await self._ensure_credentials_for_restore():
                    self._report_missing_credentials_waiting()
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue

                self._missing_credentials_reported = False
                print(f"[im-gateway] owner={self.owner_id} waiting for login")
                creds = await bot.login(force=force_login)
                await self._persist_credentials_to_remote()
                self.current_account_id = self._resolve_account_id(bot, creds=creds)
                self.current_wechat_user_id = str(getattr(creds, "user_id", "") or "").strip() or None

                if self._attempt_login_session_id:
                    await self._report_login_online(
                        login_session_id=self._attempt_login_session_id,
                        account_id=self.current_account_id,
                        wechat_user_id=self.current_wechat_user_id,
                    )
                    self.desired_login_session_id = None
                    self._attempt_login_session_id = None

                print(f"[im-gateway] owner={self.owner_id} online account={self.current_account_id or '-'}")
                await bot.start()
                if not self._stop_requested:
                    print(f"[im-gateway] owner={self.owner_id} bot loop ended, retrying")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self._stop_requested:
                    await self._report_login_failure(exc)
                    print(f"[im-gateway] owner={self.owner_id} runtime failed: {exc}")
            finally:
                await self._close_bot(bot)
                if self.bot is bot:
                    self.bot = None

            if self._stop_requested:
                break
            await asyncio.sleep(LOGIN_RETRY_SECONDS)

    def _build_bot(self) -> Any:
        try:
            from wechatbot import WeChatBot  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on optional sdk
            raise RuntimeError(
                "未安装 wechatbot-sdk，无法启动真实微信网关。请先执行 `pip install wechatbot-sdk`。"
            ) from exc

        return WeChatBot(
            cred_path=str(self.local_credential_path),
            on_qr_url=self._on_qr_url,
            on_scanned=self._on_scanned,
            on_expired=self._on_expired,
            on_error=self._on_error,
        )

    def _register_handlers(self, bot: Any) -> None:
        async def _on_message(msg: Any) -> None:  # pragma: no cover - depends on optional sdk
            try:
                await self._handle_message(bot, msg)
            except Exception as exc:
                error_text = str(exc).strip() or type(exc).__name__
                print(f"[im-gateway] owner={self.owner_id} inbound message failed: {error_text}")

        bot.on_message(_on_message)

    async def _handle_message(self, bot: Any, msg: Any) -> None:  # pragma: no cover - depends on optional sdk
        peer_id = str(getattr(msg, "user_id", "") or getattr(msg, "from_user", "") or "").strip()
        if not peer_id:
            return
        bot_account_id = self._resolve_account_id(bot)
        text = str(getattr(msg, "text", "") or "").strip() or None
        attachments = await self._download_attachments(bot, msg)

        inbound_task = asyncio.create_task(
            self.backend.post_inbound_message(
                bot_account_id=bot_account_id,
                wechat_peer_id=peer_id,
                wechat_peer_name=None,
                text=text,
                attachments=attachments,
                timeout_seconds=INBOUND_REQUEST_TIMEOUT_SECONDS,
            )
        )
        self._track_background_task(inbound_task)
        await self._send_typing_indicator(bot, peer_id)
        try:
            result = await asyncio.wait_for(asyncio.shield(inbound_task), timeout=INBOUND_REPLY_WAIT_SECONDS)
        except asyncio.TimeoutError:
            followup = asyncio.create_task(
                self._deliver_inbound_result_later(
                    bot=bot,
                    inbound_task=inbound_task,
                    peer_id=peer_id,
                    incoming_msg=msg,
                )
            )
            self._track_background_task(followup)
            return
        except Exception:
            if inbound_task.done():
                with contextlib.suppress(Exception):
                    inbound_task.result()
            raise

        await self._send_messages(
            bot=bot,
            peer_id=peer_id,
            incoming_msg=msg,
            messages=result.get("messages") if isinstance(result.get("messages"), list) else [],
        )

    async def _deliver_inbound_result_later(
        self,
        *,
        bot: Any,
        inbound_task: asyncio.Task[Dict[str, Any]],
        peer_id: str,
        incoming_msg: Any,
    ) -> None:
        try:
            result = await inbound_task
        except httpx.ReadTimeout:
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[im-gateway] owner={self.owner_id} async inbound follow-up failed: {exc}")
            await self._send_messages(
                bot=bot,
                peer_id=peer_id,
                incoming_msg=incoming_msg,
                messages=[{"type": "text", "text": self._friendly_inbound_failure_text()}],
            )
            return

        await self._send_messages(
            bot=bot,
            peer_id=peer_id,
            incoming_msg=incoming_msg,
            messages=result.get("messages") if isinstance(result.get("messages"), list) else [],
        )

    async def _download_attachments(self, bot: Any, msg: Any) -> List[Dict[str, Any]]:  # pragma: no cover - depends on optional sdk
        download = getattr(bot, "download", None)
        if not callable(download):
            return []
        try:
            media = await download(msg)
        except Exception:
            return []
        if not media:
            return []

        items = media if isinstance(media, list) else [media]
        attachments: List[Dict[str, Any]] = []
        for index, item in enumerate(items, start=1):
            content = getattr(item, "data", None) or getattr(item, "content", None)
            if content is None:
                continue
            file_name = str(getattr(item, "file_name", "") or getattr(item, "filename", "") or "").strip()
            suffix = Path(file_name).suffix or self._default_download_suffix(str(getattr(item, "type", "") or "file"))
            resolved_name = file_name or f"wechat_upload_{index}{suffix}"
            target = self.download_dir / f"{asyncio.get_running_loop().time():.6f}_{index}_{Path(resolved_name).name}"
            target.write_bytes(content if isinstance(content, (bytes, bytearray)) else bytes(content))
            attachments.append(
                {
                    "filename": Path(resolved_name).name,
                    "storedPath": str(target),
                    "contentType": str(getattr(item, "content_type", "") or getattr(item, "mime_type", "") or "").strip() or None,
                }
            )
        return attachments

    async def _send_messages(
        self,
        *,
        bot: Any,
        peer_id: str,
        incoming_msg: Any,
        messages: List[Dict[str, Any]],
    ) -> None:  # pragma: no cover - depends on optional sdk
        for item in messages:
            message_type = str(item.get("type") or "text").strip()
            if message_type == "file":
                download_path = str(item.get("downloadPath") or "").strip()
                if not download_path:
                    continue
                try:
                    preferred_name = str(item.get("fileName") or "").strip() or None
                    await self._reply_with_media(
                        bot=bot,
                        incoming_msg=incoming_msg,
                        peer_id=peer_id,
                        download_path=download_path,
                        preferred_name=preferred_name,
                    )
                except Exception as exc:
                    await bot.send(peer_id, self._file_delivery_failure_text())
                    print(f"[im-gateway] owner={self.owner_id} inline file delivery failed: {exc}")
                continue

            text = str(item.get("text") or "").strip()
            if not text:
                continue
            reply = getattr(bot, "reply", None)
            if callable(reply):
                await reply(incoming_msg, text)
            else:
                await bot.send(peer_id, text)

    async def _send_delivery_artifact(self, *, bot: Any, peer_id: str, download_path: str, delivery_job_id: str) -> None:
        content, content_type, filename = await self.backend.download_task_artifact(download_path)
        resolved_name = unquote(str(filename or "").strip()) or "result.bin"
        send_media = getattr(bot, "send_media", None)
        if not callable(send_media):
            raise RuntimeError("wechat gateway send_media is unavailable")
        size_bytes = len(content) if isinstance(content, (bytes, bytearray)) else -1
        await self._update_delivery_job_progress(
            delivery_job_id,
            stage="uploading_media",
            stage_details={
                "startedAt": utc_now().isoformat(),
                "fileName": resolved_name,
                "contentType": content_type,
                "sizeBytes": size_bytes,
            },
        )
        await self._send_media_with_retry(
            send_media=send_media,
            peer_id=peer_id,
            payload={"file": content, "file_name": resolved_name},
            file_name=resolved_name,
            content_type=content_type,
            size_bytes=size_bytes,
            log_label="delivery artifact upload failed",
        )
        await self._update_delivery_job_progress(
            delivery_job_id,
            stage="sending_summary",
            stage_details={"artifactUploadedAt": utc_now().isoformat(), "fileName": resolved_name},
        )

    async def _update_delivery_job_progress(
        self,
        delivery_job_id: str,
        *,
        stage: str,
        stage_details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not str(delivery_job_id or "").strip():
            return
        try:
            await self.backend.update_delivery_job_progress(
                delivery_job_id,
                stage=stage,
                stage_details=stage_details or {},
            )
        except Exception as exc:
            print(f"[im-gateway] owner={self.owner_id} delivery progress update failed: job={delivery_job_id} stage={stage} error={exc}")

    async def _reply_with_media(
        self,
        *,
        bot: Any,
        incoming_msg: Any,
        peer_id: str,
        download_path: str,
        preferred_name: Optional[str] = None,
    ) -> None:
        content, content_type, filename = await self.backend.download_task_artifact(download_path)
        resolved_name = unquote(str(preferred_name or filename or "").strip()) or "result.bin"
        reply_media = getattr(bot, "reply_media", None)
        if callable(reply_media):
            try:
                await reply_media(incoming_msg, {"file": content, "file_name": resolved_name})
            except Exception as exc:
                if not self._is_retryable_media_error(exc):
                    raise
                send_media = getattr(bot, "send_media", None)
                if not callable(send_media):
                    raise
                size_bytes = len(content) if isinstance(content, (bytes, bytearray)) else -1
                await self._send_media_with_retry(
                    send_media=send_media,
                    peer_id=peer_id,
                    payload={"file": content, "file_name": resolved_name},
                    file_name=resolved_name,
                    content_type=content_type,
                    size_bytes=size_bytes,
                    log_label="media reply upload failed",
                    initial_exception=exc,
                )
            return
        send_media = getattr(bot, "send_media", None)
        if callable(send_media):
            size_bytes = len(content) if isinstance(content, (bytes, bytearray)) else -1
            await self._send_media_with_retry(
                send_media=send_media,
                peer_id=peer_id,
                payload={"file": content, "file_name": resolved_name},
                file_name=resolved_name,
                content_type=content_type,
                size_bytes=size_bytes,
                log_label="media reply upload failed",
            )
            return
        size_bytes = len(content) if isinstance(content, (bytes, bytearray)) else -1
        print(
            f"[im-gateway] owner={self.owner_id} media reply unsupported: "
            f"peer_id={peer_id} file_name={resolved_name} content_type={content_type or '-'} size_bytes={size_bytes}"
        )
        raise RuntimeError("wechat gateway media send is unavailable")

    async def _send_media_with_retry(
        self,
        *,
        send_media: Callable[[str, Dict[str, Any]], Any],
        peer_id: str,
        payload: Dict[str, Any],
        file_name: str,
        content_type: Optional[str],
        size_bytes: int,
        log_label: str,
        initial_exception: Optional[Exception] = None,
    ) -> None:
        last_exc: Optional[Exception] = initial_exception
        start_attempt = 2 if initial_exception is not None else 1
        if initial_exception is None:
            try:
                await send_media(peer_id, payload)
                return
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable_media_error(exc) or MEDIA_UPLOAD_MAX_ATTEMPTS <= 1:
                    self._log_media_upload_failure(
                        peer_id=peer_id,
                        file_name=file_name,
                        content_type=content_type,
                        size_bytes=size_bytes,
                        error=exc,
                        log_label=log_label,
                    )
                    raise

        for attempt in range(start_attempt, MEDIA_UPLOAD_MAX_ATTEMPTS + 1):
            exc = last_exc if attempt == start_attempt else None
            if exc is not None:
                print(
                    f"[im-gateway] owner={self.owner_id} retrying media upload: "
                    f"peer_id={peer_id} file_name={file_name} attempt={attempt}/{MEDIA_UPLOAD_MAX_ATTEMPTS} error={exc}"
                )
            if MEDIA_UPLOAD_RETRY_DELAY_SECONDS > 0:
                await asyncio.sleep(MEDIA_UPLOAD_RETRY_DELAY_SECONDS)
            try:
                await send_media(peer_id, payload)
                return
            except Exception as retry_exc:
                last_exc = retry_exc
                if not self._is_retryable_media_error(retry_exc):
                    break

        assert last_exc is not None
        self._log_media_upload_failure(
            peer_id=peer_id,
            file_name=file_name,
            content_type=content_type,
            size_bytes=size_bytes,
            error=last_exc,
            log_label=log_label,
        )
        raise last_exc

    @staticmethod
    def _is_retryable_media_error(exc: Exception) -> bool:
        message = str(exc or "").strip().lower()
        return "cdn upload failed" in message and "http 5" in message

    def _log_media_upload_failure(
        self,
        *,
        peer_id: str,
        file_name: str,
        content_type: Optional[str],
        size_bytes: int,
        error: Exception,
        log_label: str,
    ) -> None:
        print(
            f"[im-gateway] owner={self.owner_id} {log_label}: "
            f"peer_id={peer_id} file_name={file_name} content_type={content_type or '-'} "
            f"size_bytes={size_bytes} error={error}"
        )

    async def _send_typing_indicator(self, bot: Any, peer_id: str) -> None:
        if not str(peer_id or "").strip():
            return
        for method_name in ("send_typing", "sendTyping"):
            method = getattr(bot, method_name, None)
            if not callable(method):
                continue
            for args, kwargs in (
                ((peer_id,), {}),
                ((peer_id, True), {}),
                ((peer_id,), {"status": 1}),
                ((peer_id,), {"typing": True}),
            ):
                try:
                    result = method(*args, **kwargs)
                    if asyncio.iscoroutine(result):
                        await result
                    return
                except TypeError:
                    continue
                except Exception:
                    return

    async def _ensure_credentials_for_restore(self) -> bool:
        if self._has_local_credentials():
            return True
        restored = await self._restore_credentials_from_remote()
        return restored or self._has_local_credentials()

    async def _restore_credentials_from_remote(self) -> bool:
        if not self.credential_store:
            return False
        restored = await asyncio.to_thread(self.credential_store.restore_local_credentials)
        if restored:
            print(f"[im-gateway] owner={self.owner_id} restored credentials from R2")
        return restored

    async def _persist_credentials_to_remote(self) -> bool:
        if not self.credential_store:
            return False
        persisted = await asyncio.to_thread(self.credential_store.persist_local_credentials)
        if persisted:
            print(f"[im-gateway] owner={self.owner_id} persisted credentials to R2")
        return persisted

    def _report_missing_credentials_waiting(self) -> None:
        if self._missing_credentials_reported:
            return
        print(f"[im-gateway] owner={self.owner_id} missing credentials, waiting for a new login session")
        self._missing_credentials_reported = True

    async def _clear_credentials(self) -> None:
        local_path = self.local_credential_path
        if self.credential_store is not None:
            await asyncio.to_thread(self.credential_store.clear_local_credentials)
            await asyncio.to_thread(self.credential_store.clear_remote_credentials)
        else:
            local_path.unlink(missing_ok=True)

    def _has_local_credentials(self) -> bool:
        if self.credential_store is not None:
            return self.credential_store.has_local_credentials()
        return self.local_credential_path.exists() and self.local_credential_path.is_file()

    async def _report_login_online(
        self,
        *,
        login_session_id: str,
        account_id: Optional[str],
        wechat_user_id: Optional[str],
    ) -> None:
        self._login_terminal_state_reported = True
        try:
            await self.backend.update_login_session_state(
                login_session_id=login_session_id,
                status="online",
                account_id=account_id,
                wechat_user_id=wechat_user_id,
                wechat_display_name=None,
            )
        except Exception as exc:
            print(f"[im-gateway] owner={self.owner_id} failed to report online state: {exc}")

    async def _report_login_failure(self, exc: Exception) -> None:
        login_session_id = str(self._attempt_login_session_id or "").strip()
        if not login_session_id or self._login_terminal_state_reported:
            return
        self._login_terminal_state_reported = True
        try:
            await self.backend.update_login_session_state(
                login_session_id=login_session_id,
                status="failed",
                error_message=str(exc),
            )
        except Exception as report_exc:
            print(f"[im-gateway] owner={self.owner_id} failed to report login error: {report_exc}")

    def _resolve_account_id(self, bot: Any, *, creds: Any | None = None) -> str:
        resolved_creds = creds
        if resolved_creds is None:
            credentials = getattr(bot, "get_credentials", None)
            if callable(credentials):
                with contextlib.suppress(Exception):
                    resolved_creds = credentials()
        account_id = str(getattr(resolved_creds, "account_id", "") or "").strip()
        if account_id:
            return account_id
        return str(getattr(bot, "self_id", "") or getattr(bot, "wxid", "") or f"owner-{_owner_token(self.owner_id)}").strip()

    def _on_qr_url(self, url: str) -> None:
        login_session_id = str(self._attempt_login_session_id or "").strip()
        print(f"[im-gateway] owner={self.owner_id} scan qr: {url}")
        if not login_session_id or self._login_terminal_state_reported:
            return
        task = asyncio.create_task(
            self.backend.update_login_session_state(
                login_session_id=login_session_id,
                status="qr_ready",
                qr_url=str(url or "").strip() or None,
            )
        )
        self._track_background_task(task)

    def _on_scanned(self) -> None:
        login_session_id = str(self._attempt_login_session_id or "").strip()
        print(f"[im-gateway] owner={self.owner_id} qr scanned")
        if not login_session_id or self._login_terminal_state_reported:
            return
        task = asyncio.create_task(
            self.backend.update_login_session_state(
                login_session_id=login_session_id,
                status="scanned",
            )
        )
        self._track_background_task(task)

    def _on_expired(self) -> None:
        login_session_id = str(self._attempt_login_session_id or "").strip()
        print(f"[im-gateway] owner={self.owner_id} qr expired")
        if not login_session_id:
            return
        self._login_terminal_state_reported = True
        task = asyncio.create_task(
            self.backend.update_login_session_state(
                login_session_id=login_session_id,
                status="expired",
            )
        )
        self._track_background_task(task)

    def _on_error(self, exc: Exception) -> None:
        login_session_id = str(self._attempt_login_session_id or "").strip()
        print(f"[im-gateway] owner={self.owner_id} sdk error: {exc}")
        if not login_session_id:
            return
        self._login_terminal_state_reported = True
        task = asyncio.create_task(
            self.backend.update_login_session_state(
                login_session_id=login_session_id,
                status="failed",
                error_message=str(exc),
            )
        )
        self._track_background_task(task)

    @staticmethod
    async def _cancel_task(task: asyncio.Task[Any]) -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _close_bot(self, bot: Any) -> None:
        if bot is None:
            return
        for method_name in ("stop", "close", "aclose"):
            method = getattr(bot, method_name, None)
            if not callable(method):
                continue
            try:
                result = method()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass
            return

    @staticmethod
    def _default_download_suffix(media_type: str) -> str:
        mapping = {
            "image": ".png",
            "video": ".mp4",
            "voice": ".silk",
            "file": ".bin",
        }
        return mapping.get(str(media_type or "").strip(), ".bin")

    @staticmethod
    def _friendly_inbound_failure_text() -> str:
        return "抱歉，刚才处理这条消息时出了点小问题。你可以稍后再发一次，我会继续帮你接着看。"

    @staticmethod
    def _file_delivery_failure_text() -> str:
        return "结果文件发送失败了，我这边会继续重试。你先不用重复发送。"

    @staticmethod
    def _build_delivery_text(job: Dict[str, Any]) -> str:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        title = str(payload.get("title") or payload.get("taskId") or "任务").strip()
        terminal_status = str(payload.get("terminalStatus") or "").strip()
        pending_action_type = str(payload.get("pendingActionType") or "").strip()
        error_message = str(payload.get("errorMessage") or "").strip()
        if terminal_status == "failed":
            return f"{title} 执行失败。\n{error_message or '请回到网页查看详情。'}"
        if pending_action_type == "question":
            prompt = str(payload.get("prompt") or "").strip()
            return f"{title} 需要补充信息。\n{prompt or '请直接在微信里补充说明，我会继续往下处理。'}"
        if pending_action_type == "plan_confirmation":
            return f"{title} 的检索计划已生成。\n请直接在微信里回复“确认计划”，或告诉我你要修改的地方。"
        if pending_action_type == "human_decision":
            selected_count = int(payload.get("selectedCount") or 0)
            summary = f"当前已筛出 {selected_count} 篇候选文献。" if selected_count > 0 else "当前需要你决定下一步。"
            return f"{title} 需要你的确认。\n{summary} 请直接在微信里回复“继续检索”或“按当前结果完成”。"
        return f"{title} 已完成。"


class WeChatGateway:
    def __init__(self, *, backend: BackendClient) -> None:
        self.backend = backend
        self._runtimes: Dict[str, AccountRuntime] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def _track_background_task(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def run(self) -> None:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        print("[im-gateway] waiting for backend")
        await self.backend.wait_until_ready()
        print("[im-gateway] backend ready")

        event_listener = asyncio.create_task(self._listen_delivery_events(), name="wechat-delivery-events")
        poller = asyncio.create_task(self._poll_delivery_jobs_fallback(), name="wechat-delivery-fallback-poller")
        self._track_background_task(event_listener)
        self._track_background_task(poller)
        await asyncio.sleep(0)
        try:
            while True:
                try:
                    snapshot = await self.backend.fetch_runtime_snapshot()
                    await self._reconcile(snapshot)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"[im-gateway] reconcile failed: {exc}")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        finally:
            event_listener.cancel()
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await event_listener
            with contextlib.suppress(asyncio.CancelledError):
                await poller
            await self._shutdown_runtimes(clear_credentials=False)
            for task in list(self._background_tasks):
                if task.done():
                    continue
                task.cancel()
            for task in list(self._background_tasks):
                if task.done():
                    continue
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            await self.backend.close()

    async def _reconcile(self, snapshot: Dict[str, Any]) -> None:
        desired = self._build_desired_targets(snapshot)

        stale_owner_ids = [owner_id for owner_id in self._runtimes.keys() if owner_id not in desired]
        for owner_id in stale_owner_ids:
            runtime = self._runtimes.pop(owner_id, None)
            if runtime is None:
                continue
            await runtime.stop(clear_credentials=True)

        for owner_id, target in desired.items():
            runtime = self._runtimes.get(owner_id)
            if runtime is None:
                runtime = AccountRuntime(
                    owner_id=owner_id,
                    backend=self.backend,
                    download_dir=DOWNLOAD_DIR,
                    background_task_tracker=self._track_background_task,
                )
                self._runtimes[owner_id] = runtime
            await runtime.apply_target(target)

    def _build_desired_targets(self, snapshot: Dict[str, Any]) -> Dict[str, OwnerRuntimeTarget]:
        desired: Dict[str, OwnerRuntimeTarget] = {}

        active_bindings = snapshot.get("activeBindings") if isinstance(snapshot, dict) else []
        for item in active_bindings if isinstance(active_bindings, list) else []:
            if not isinstance(item, dict):
                continue
            owner_id = str(item.get("ownerId") or "").strip()
            if not owner_id:
                continue
            desired[owner_id] = OwnerRuntimeTarget(
                owner_id=owner_id,
                account_id=str(item.get("accountId") or "").strip() or None,
                login_session_id=None,
            )

        pending_sessions = snapshot.get("pendingLoginSessions") if isinstance(snapshot, dict) else []
        for item in pending_sessions if isinstance(pending_sessions, list) else []:
            if not isinstance(item, dict):
                continue
            owner_id = str(item.get("ownerId") or "").strip()
            login_session_id = str(item.get("loginSessionId") or "").strip()
            if not owner_id or not login_session_id:
                continue
            current = desired.get(owner_id)
            desired[owner_id] = OwnerRuntimeTarget(
                owner_id=owner_id,
                account_id=(current.account_id if current else None),
                login_session_id=login_session_id,
            )

        return desired

    async def _listen_delivery_events(self) -> None:
        cursor = 0
        while True:
            try:
                payload = await self.backend.await_delivery_event(cursor=cursor, timeout_seconds=DELIVERY_EVENT_WAIT_SECONDS)
                cursor = int(payload.get("cursor") or cursor)
                await self._claim_and_deliver_jobs(limit=5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[im-gateway] delivery event wait failed: {exc}")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _poll_delivery_jobs_fallback(self) -> None:
        while True:
            try:
                await self._claim_and_deliver_jobs(limit=5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[im-gateway] fallback poll failed: {exc}")
            await asyncio.sleep(DELIVERY_FALLBACK_POLL_INTERVAL_SECONDS)

    async def _claim_and_deliver_jobs(self, *, limit: int) -> None:
        payload = await self.backend.claim_delivery_jobs(limit=limit)
        items = payload.get("items") if isinstance(payload, dict) else []
        for job in items or []:
            await self._deliver_job(job)

    async def _deliver_job(self, job: Dict[str, Any]) -> None:
        delivery_job_id = str(job.get("deliveryJobId") or "").strip()
        try:
            runtime = self._runtime_for_job(job)
            if runtime is None:
                raise RuntimeError("no active runtime for delivery job")
            await runtime.send_delivery_job(job)
            await self.backend.complete_delivery_job(delivery_job_id)
        except Exception as exc:
            if delivery_job_id:
                await self.backend.fail_delivery_job(
                    delivery_job_id,
                    str(exc),
                    retryable=self._is_retryable_delivery_error(exc),
                    retry_after_seconds=self._retry_after_seconds(exc),
                )

    def _runtime_for_job(self, job: Dict[str, Any]) -> Optional[AccountRuntime]:
        owner_id = str(job.get("ownerId") or "").strip()
        binding = job.get("binding") if isinstance(job.get("binding"), dict) else {}
        account_id = str(binding.get("accountId") or "").strip()

        if owner_id:
            runtime = self._runtimes.get(owner_id)
            if runtime is not None:
                if not account_id or not runtime.current_account_id or runtime.current_account_id == account_id:
                    return runtime

        if account_id:
            for runtime in self._runtimes.values():
                if runtime.current_account_id == account_id:
                    return runtime

        return None

    @staticmethod
    def _is_retryable_delivery_error(exc: Exception) -> bool:
        message = str(exc or "").strip().lower()
        return "cdn upload failed" in message and "http 5" in message

    @classmethod
    def _retry_after_seconds(cls, exc: Exception) -> Optional[int]:
        if not cls._is_retryable_delivery_error(exc):
            return None
        return 5

    async def _shutdown_runtimes(self, *, clear_credentials: bool) -> None:
        for owner_id, runtime in list(self._runtimes.items()):
            await runtime.stop(clear_credentials=clear_credentials)
            self._runtimes.pop(owner_id, None)


async def main() -> None:
    if not INTERNAL_GATEWAY_TOKEN:
        raise RuntimeError("缺少 INTERNAL_GATEWAY_TOKEN，无法启动微信网关。")

    gateway = WeChatGateway(
        backend=BackendClient(
            api_base_url=API_BASE_URL,
            internal_gateway_token=INTERNAL_GATEWAY_TOKEN,
            inbound_request_timeout_seconds=INBOUND_REQUEST_TIMEOUT_SECONDS,
        )
    )
    await gateway.run()


if __name__ == "__main__":
    asyncio.run(main())
