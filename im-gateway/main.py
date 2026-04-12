from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import re
from pathlib import Path
from concurrent.futures import Future
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from dotenv import load_dotenv

from backend_client import BackendClient


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


DEFAULT_BACKEND_PORT = str(os.getenv("PORT", "7860") or "7860").strip() or "7860"
API_BASE_URL = os.getenv("API_BASE_URL", f"http://127.0.0.1:{DEFAULT_BACKEND_PORT}")
INTERNAL_GATEWAY_TOKEN = os.getenv("INTERNAL_GATEWAY_TOKEN", "").strip()
POLL_INTERVAL_SECONDS = max(2, int(os.getenv("IM_GATEWAY_POLL_INTERVAL_SECONDS", "8")))
LOGIN_RETRY_SECONDS = max(3, int(os.getenv("IM_GATEWAY_LOGIN_RETRY_SECONDS", "5")))
DOWNLOAD_DIR = Path(os.getenv("IM_GATEWAY_DOWNLOAD_DIR", str(Path(__file__).resolve().parent / "tmp")))
BIND_CODE_PATTERN = re.compile(r"^[A-Z0-9]{8}$")


class WeChatGateway:
    def __init__(self, *, backend: BackendClient) -> None:
        self.backend = backend
        self.bot: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _build_bot(self) -> Any:
        try:
            from wechatbot import WeChatBot  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on optional sdk
            raise RuntimeError(
                "未安装 wechatbot-sdk，无法启动真实微信网关。请先执行 `pip install wechatbot-sdk`。"
            ) from exc
        return WeChatBot(
            on_qr_url=self._on_qr_url,
            on_scanned=self._on_scanned,
            on_expired=self._on_expired,
            on_error=self._on_error,
        )

    async def run(self) -> None:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self._loop = asyncio.get_running_loop()
        poller = asyncio.create_task(self._poll_delivery_jobs())
        try:
            while True:
                self.bot = self._build_bot()
                self._register_handlers()
                try:
                    print("[im-gateway] waiting for login")
                    self._submit_login_state(status="waiting_for_qr")
                    await self.bot.login()
                    print("[im-gateway] login succeeded")
                    self._submit_login_state(status="online")
                    await self.bot.start()
                    print("[im-gateway] bot session ended, restarting login flow")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._submit_login_state(status="error", error_message=str(exc))
                    print(f"[im-gateway] login loop failed: {exc}. retrying in {LOGIN_RETRY_SECONDS}s")
                finally:
                    await self._close_bot()
                await asyncio.sleep(LOGIN_RETRY_SECONDS)
        finally:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller
            await self.backend.close()

    def _submit_login_state(self, *, status: str, qr_url: Optional[str] = None, error_message: Optional[str] = None) -> Optional[Future]:
        if self._loop is None or self._loop.is_closed():
            return None
        return asyncio.run_coroutine_threadsafe(
            self.backend.update_gateway_login_state(
                status=status,
                qr_url=qr_url,
                error_message=error_message,
            ),
            self._loop,
        )

    def _on_qr_url(self, url: str) -> None:
        print(f"[im-gateway] scan qr: {url}")
        self._submit_login_state(status="qr_ready", qr_url=str(url or "").strip())

    def _on_scanned(self) -> None:
        print("[im-gateway] qr scanned")
        self._submit_login_state(status="scanned")

    def _on_expired(self) -> None:
        print("[im-gateway] qr expired")
        self._submit_login_state(status="expired")

    def _on_error(self, exc: Exception) -> None:
        print(f"[im-gateway] sdk error: {exc}")
        self._submit_login_state(status="error", error_message=str(exc))

    async def _close_bot(self) -> None:
        bot, self.bot = self.bot, None
        if bot is None:
            return
        for method_name in ("stop", "close", "aclose"):
            method = getattr(bot, method_name, None)
            if not callable(method):
                continue
            try:
                result = method()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass
            return

    def _register_handlers(self) -> None:
        async def _on_message(msg: Any) -> None:  # pragma: no cover - depends on optional sdk
            try:
                await self._handle_message(msg)
            except Exception as exc:
                print(f"[im-gateway] inbound message failed: {exc}")
        self.bot.on_message(_on_message)

    def _resolve_bot_account_id(self) -> str:
        credentials = getattr(self.bot, "get_credentials", None)
        if callable(credentials):
            try:
                creds = credentials()
            except Exception:
                creds = None
            account_id = str(getattr(creds, "account_id", "") or "").strip()
            if account_id:
                return account_id
        return str(getattr(self.bot, "self_id", "") or getattr(self.bot, "wxid", "") or "default-bot").strip()

    async def _handle_message(self, msg: Any) -> None:  # pragma: no cover - depends on optional sdk
        peer_id = str(getattr(msg, "user_id", "") or getattr(msg, "from_user", "") or "").strip()
        peer_name = str(getattr(msg, "nickname", "") or getattr(msg, "user_name", "") or "").strip() or None
        bot_account_id = self._resolve_bot_account_id()
        text = str(getattr(msg, "text", "") or "").strip() or None
        attachments = await self._download_attachments(msg)
        if await self._maybe_complete_binding(
            incoming_msg=msg,
            bot_account_id=bot_account_id,
            peer_id=peer_id,
            peer_name=peer_name,
            text=text,
        ):
            return
        result = await self.backend.post_inbound_message(
            bot_account_id=bot_account_id,
            wechat_peer_id=peer_id,
            wechat_peer_name=peer_name,
            text=text,
            attachments=attachments,
        )
        await self._send_messages(
            peer_id=peer_id,
            incoming_msg=msg,
            messages=result.get("messages") if isinstance(result.get("messages"), list) else [],
        )

    async def _maybe_complete_binding(
        self,
        *,
        incoming_msg: Any,
        bot_account_id: str,
        peer_id: str,
        peer_name: Optional[str],
        text: Optional[str],
    ) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False

        bind_session_id = ""
        bind_code = ""
        if normalized.startswith("wechat-bind:"):
            parts = normalized.split(":", 2)
            if len(parts) == 3:
                bind_session_id = parts[1].strip()
                bind_code = parts[2].strip().upper()
        else:
            compact = normalized.replace("绑定", "").replace("bind", "").replace("Bind", "").strip().upper()
            if BIND_CODE_PATTERN.match(compact):
                bind_code = compact

        if not bind_code and not bind_session_id:
            return False

        try:
            if bind_session_id:
                await self.backend.complete_bind_session(
                    bind_session_id=bind_session_id,
                    bot_account_id=bot_account_id,
                    wechat_peer_id=peer_id,
                    wechat_peer_name=peer_name,
                )
            else:
                await self.backend.complete_bind_session_by_code(
                    bind_code=bind_code,
                    bot_account_id=bot_account_id,
                    wechat_peer_id=peer_id,
                    wechat_peer_name=peer_name,
                )
            await self._send_messages(
                peer_id=peer_id,
                incoming_msg=incoming_msg,
                messages=self._build_binding_success_messages(),
            )
            return True
        except Exception as exc:
            await self._send_messages(
                peer_id=peer_id,
                incoming_msg=incoming_msg,
                messages=[{"type": "text", "text": f"绑定失败：{exc}"}],
            )
            return True

    async def _download_attachments(self, msg: Any) -> List[Dict[str, Any]]:  # pragma: no cover - depends on optional sdk
        download = getattr(self.bot, "download", None)
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
            filename = str(getattr(item, "file_name", "") or getattr(item, "filename", "") or f"wechat_upload_{index}").strip()
            content = getattr(item, "data", None) or getattr(item, "content", None)
            if content is None:
                continue
            target = DOWNLOAD_DIR / filename
            target.write_bytes(content if isinstance(content, (bytes, bytearray)) else bytes(content))
            attachments.append(
                {
                    "filename": filename,
                    "storedPath": str(target),
                    "contentType": str(getattr(item, "content_type", "") or getattr(item, "mime_type", "") or "").strip() or None,
                }
            )
        return attachments

    async def _poll_delivery_jobs(self) -> None:
        while True:
            try:
                payload = await self.backend.claim_delivery_jobs(limit=5)
                items = payload.get("items") if isinstance(payload, dict) else []
                for job in items or []:
                    await self._deliver_job(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[im-gateway] poll failed: {exc}")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def _deliver_job(self, job: Dict[str, Any]) -> None:
        delivery_job_id = str(job.get("deliveryJobId") or "").strip()
        binding = job.get("binding") if isinstance(job.get("binding"), dict) else {}
        peer_id = str(binding.get("wechatPeerId") or "").strip()
        try:
            if not peer_id:
                raise RuntimeError("missing wechatPeerId")
            summary = self._build_delivery_text(job)
            if summary:
                await self.bot.send(peer_id, summary)
            task = job.get("task") if isinstance(job.get("task"), dict) else {}
            download_path = str(task.get("downloadPath") or "").strip()
            if download_path:
                try:
                    content, _content_type, filename = await self.backend.download_task_artifact(download_path)
                    resolved_name = unquote(str(filename or "").strip()) or "result.bin"
                    send_media = getattr(self.bot, "send_media", None)
                    if callable(send_media):
                        await send_media(peer_id, {"file": content, "file_name": resolved_name})
                    else:
                        await self.bot.send(peer_id, f"结果文件已生成，但当前网关未启用文件发送能力：{resolved_name}")
                except Exception as file_exc:
                    await self.bot.send(peer_id, f"结果文件发送失败，已回退为文本通知。原因：{file_exc}")
            await self.backend.complete_delivery_job(delivery_job_id)
        except Exception as exc:
            await self.backend.fail_delivery_job(delivery_job_id, str(exc))

    async def _send_messages(self, *, peer_id: str, incoming_msg: Any, messages: List[Dict[str, Any]]) -> None:  # pragma: no cover - depends on optional sdk
        for item in messages:
            message_type = str(item.get("type") or "text").strip()
            if message_type == "file":
                download_path = str(item.get("downloadPath") or "").strip()
                if not download_path:
                    continue
                try:
                    content, _content_type, filename = await self.backend.download_task_artifact(download_path)
                    resolved_name = unquote(str(item.get("fileName") or filename or "").strip()) or "result.bin"
                    reply_media = getattr(self.bot, "reply_media", None)
                    if callable(reply_media):
                        await reply_media(incoming_msg, {"file": content, "file_name": resolved_name})
                    else:
                        await self.bot.send(peer_id, f"文件结果已生成，但当前网关未启用文件发送能力：{resolved_name}")
                except Exception as exc:
                    await self.bot.send(peer_id, f"文件发送失败：{exc}")
                continue
            text = str(item.get("text") or "").strip()
            if text:
                reply = getattr(self.bot, "reply", None)
                if callable(reply):
                    await reply(incoming_msg, text)
                else:
                    await self.bot.send(peer_id, text)

    def _build_delivery_text(self, job: Dict[str, Any]) -> str:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        title = str(payload.get("title") or payload.get("taskId") or "任务").strip()
        terminal_status = str(payload.get("terminalStatus") or "").strip()
        error_message = str(payload.get("errorMessage") or "").strip()
        if terminal_status == "failed":
            return f"{title} 执行失败。\n{error_message or '请回到网页查看详情。'}"
        return f"{title} 已完成。"

    def _build_binding_success_messages(self) -> List[Dict[str, str]]:
        return [
            {
                "type": "text",
                "text": (
                    "微信绑定成功。\n"
                    "现在你可以直接在这里发起任务，即使不打开网页也能继续处理。"
                ),
            },
            {
                "type": "text",
                "text": (
                    "常用用法：\n"
                    "1. 直接发自然语言，例如“帮我检索锂电池负极相关专利”\n"
                    "2. 直接发“分析专利 CN117347385A”或上传 PDF 并说“帮我分析这个专利”\n"
                    "3. 直接发“帮我审查这个专利”\n"
                    "4. 直接发“我要答复审查意见”\n"
                    "5. 也支持 /analysis new、/review new、/reply new"
                ),
            },
            {
                "type": "text",
                "text": (
                    "检索过程中可直接回复：\n"
                    "确认计划\n"
                    "继续检索\n"
                    "按当前结果完成\n"
                    "选择 1 3 5"
                ),
            },
            {
                "type": "text",
                "text": "如果正在收集材料，发送 /cancel 可以取消当前微信任务流程。",
            },
        ]


async def main() -> None:
    if not INTERNAL_GATEWAY_TOKEN:
        raise RuntimeError("缺少 INTERNAL_GATEWAY_TOKEN，无法启动微信网关。")
    gateway = WeChatGateway(
        backend=BackendClient(
            api_base_url=API_BASE_URL,
            internal_gateway_token=INTERNAL_GATEWAY_TOKEN,
        )
    )
    await gateway.run()


if __name__ == "__main__":
    asyncio.run(main())
