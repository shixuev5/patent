"""Task terminal email notification service."""

from __future__ import annotations

import mimetypes
from copy import deepcopy
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from config import settings
from backend.storage import TaskType
from backend.time_utils import format_for_admin_local, utc_now_z
from backend.utils import _build_r2_storage

from .email_sender import (
    BrevoEmailSender,
    BrevoSenderConfig,
    EmailSender,
    EmailSendResult,
    SmtpEmailSender,
    SmtpSenderConfig,
)


SystemLogEmitter = Callable[..., None]
R2Factory = Callable[[], Any]

TERMINAL_COMPLETED = "completed"
TERMINAL_FAILED = "failed"

TASK_TYPE_LABELS = {
    TaskType.PATENT_ANALYSIS.value: "AI 分析",
    TaskType.AI_REPLY.value: "AI 答复",
    TaskType.AI_SEARCH.value: "AI 检索",
}


@dataclass
class AttachmentPayload:
    filename: str
    content: bytes
    content_type: str = "application/pdf"


def _noop_emit_system_log(**_kwargs: Any) -> None:
    return


class TaskEmailNotificationService:
    def __init__(
        self,
        *,
        storage: Any,
        email_sender: Optional[EmailSender] = None,
        system_log_emitter: Optional[SystemLogEmitter] = None,
        r2_storage_factory: Optional[R2Factory] = None,
    ) -> None:
        self.storage = storage
        self.email_sender = email_sender
        self.system_log_emitter = system_log_emitter or _noop_emit_system_log
        self.r2_storage_factory = r2_storage_factory or _build_r2_storage

    def notify_task_terminal_status(
        self,
        task_id: str,
        *,
        terminal_status: str,
        task_type: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_status = str(terminal_status or "").strip().lower()
        if normalized_status not in {TERMINAL_COMPLETED, TERMINAL_FAILED}:
            return {"status": "ignored", "reason": "unsupported_terminal_status"}

        task = self.storage.get_task(task_id)
        if not task:
            return {"status": "ignored", "reason": "task_not_found"}

        existing = self._existing_delivery(task, normalized_status)
        if existing is not None:
            return {"status": "duplicate", "record": existing}

        if not bool(settings.EMAIL_NOTIFICATIONS_ENABLED):
            record = self._save_delivery_record(
                task_id,
                normalized_status,
                delivery_status="skipped",
                reason="email_notifications_disabled",
                task=task,
            )
            self._emit_delivery_log("task_email_skipped", task, normalized_status, record)
            return record

        recipient = self._resolve_recipient(task)
        if not recipient:
            record = self._save_delivery_record(
                task_id,
                normalized_status,
                delivery_status="skipped",
                reason="recipient_email_missing",
                task=task,
            )
            self._emit_delivery_log("task_email_skipped", task, normalized_status, record)
            return record

        subject = self._build_subject(task, normalized_status, task_type=task_type)
        resolved_error = str(error_message or getattr(task, "error_message", "") or "").strip()
        attachment = self._resolve_attachment(task, normalized_status)
        if normalized_status == TERMINAL_COMPLETED and attachment is None:
            record = self._save_delivery_record(
                task_id,
                normalized_status,
                delivery_status="failed",
                reason="attachment_missing_for_completed_task",
                task=task,
                recipient=recipient,
                subject=subject,
            )
            self._emit_delivery_log("task_email_failed", task, normalized_status, record)
            return record

        try:
            sender = self.email_sender or self._build_sender()
            message = self._build_message(
                task=task,
                recipient=recipient,
                terminal_status=normalized_status,
                subject=subject,
                error_message=resolved_error,
                attachment=attachment,
                task_type=task_type,
            )
            send_result = sender.send(
                message,
                metadata={
                    "task_id": str(getattr(task, "id", "") or "").strip(),
                    "owner_id": str(getattr(task, "owner_id", "") or "").strip(),
                    "task_type": str(task_type or getattr(task, "task_type", "") or "").strip(),
                    "terminal_status": normalized_status,
                },
            )
        except Exception as exc:
            record = self._save_delivery_record(
                task_id,
                normalized_status,
                delivery_status="failed",
                reason=str(exc),
                task=task,
                recipient=recipient,
                subject=subject,
                attachment=attachment,
            )
            self._emit_delivery_log("task_email_failed", task, normalized_status, record)
            return record

        record = self._save_delivery_record(
            task_id,
            normalized_status,
            delivery_status="sent",
            reason="",
            task=task,
            recipient=recipient,
            subject=subject,
            attachment=attachment,
            send_result=send_result,
        )
        self._emit_delivery_log("task_email_sent", task, normalized_status, record)
        return record

    def _build_sender(self) -> EmailSender:
        provider = str(getattr(settings, "EMAIL_PROVIDER", "brevo") or "brevo").strip().lower()
        if provider in {"", "brevo"}:
            return BrevoEmailSender(
                BrevoSenderConfig(
                    api_key=str(getattr(settings, "BREVO_API_KEY", "") or "").strip(),
                    api_base_url=str(getattr(settings, "BREVO_API_BASE_URL", "https://api.brevo.com/v3") or "").strip(),
                    from_address=str(
                        getattr(settings, "BREVO_FROM_ADDRESS", "") or getattr(settings, "SMTP_FROM_ADDRESS", "") or ""
                    ).strip(),
                    from_name=str(
                        getattr(settings, "BREVO_FROM_NAME", "") or getattr(settings, "SMTP_FROM_NAME", "") or ""
                    ).strip(),
                    timeout_seconds=int(getattr(settings, "BREVO_TIMEOUT_SECONDS", 30) or 30),
                )
            )
        if provider != "smtp":
            raise ValueError(f"EMAIL_PROVIDER 配置无效：{provider}")
        return SmtpEmailSender(
            SmtpSenderConfig(
                host=str(getattr(settings, "SMTP_HOST", "") or "").strip(),
                port=int(getattr(settings, "SMTP_PORT", 0) or 0),
                username=str(getattr(settings, "SMTP_USERNAME", "") or "").strip(),
                password=str(getattr(settings, "SMTP_PASSWORD", "") or ""),
                from_address=str(getattr(settings, "SMTP_FROM_ADDRESS", "") or "").strip(),
                from_name=str(getattr(settings, "SMTP_FROM_NAME", "") or "").strip(),
                use_tls=bool(getattr(settings, "SMTP_USE_TLS", False)),
                use_ssl=bool(getattr(settings, "SMTP_USE_SSL", False)),
            )
        )

    def _resolve_recipient(self, task: Any) -> str:
        owner_id = str(getattr(task, "owner_id", "") or "").strip()
        if not owner_id or owner_id.startswith("guest"):
            return ""
        if not hasattr(self.storage, "get_user_by_owner_id"):
            return ""
        user = self.storage.get_user_by_owner_id(owner_id)
        return str(getattr(user, "email", "") or "").strip()

    def _build_subject(self, task: Any, terminal_status: str, *, task_type: Optional[str] = None) -> str:
        prefix = "任务完成通知" if terminal_status == TERMINAL_COMPLETED else "任务失败通知"
        task_type_value = str(task_type or getattr(task, "task_type", "") or "").strip().lower()
        label = TASK_TYPE_LABELS.get(task_type_value, task_type_value or "任务")
        title = str(getattr(task, "title", "") or "").strip() or str(getattr(task, "id", "") or "").strip()
        return f"【{prefix}】{label} - {title}"

    def _build_message(
        self,
        *,
        task: Any,
        recipient: str,
        terminal_status: str,
        subject: str,
        error_message: str,
        attachment: Optional[AttachmentPayload],
        task_type: Optional[str] = None,
    ) -> EmailMessage:
        message = EmailMessage()
        from_address = str(getattr(settings, "SMTP_FROM_ADDRESS", "") or "").strip()
        from_name = str(getattr(settings, "SMTP_FROM_NAME", "") or "").strip()
        message["Subject"] = subject
        message["From"] = formataddr((from_name, from_address)) if from_name else from_address
        message["To"] = recipient
        message.set_content(
            self._build_body(
                task=task,
                terminal_status=terminal_status,
                error_message=error_message,
                attachment=attachment,
                task_type=task_type,
            ),
            subtype="plain",
            charset="utf-8",
        )
        if attachment is not None:
            maintype, subtype = attachment.content_type.split("/", 1) if "/" in attachment.content_type else ("application", "octet-stream")
            message.add_attachment(
                attachment.content,
                maintype=maintype,
                subtype=subtype,
                filename=attachment.filename,
            )
        return message

    def _build_body(
        self,
        *,
        task: Any,
        terminal_status: str,
        error_message: str,
        attachment: Optional[AttachmentPayload],
        task_type: Optional[str] = None,
    ) -> str:
        task_type_value = str(task_type or getattr(task, "task_type", "") or "").strip().lower()
        task_type_label = TASK_TYPE_LABELS.get(task_type_value, task_type_value or "任务")
        terminal_label = "成功完成" if terminal_status == TERMINAL_COMPLETED else "执行失败"
        completed_at = format_for_admin_local(
            getattr(task, "completed_at", None) or getattr(task, "updated_at", None),
            naive_strategy="utc",
            timespec="seconds",
        ) or ""
        lines = [
            f"{task_type_label}已{terminal_label}。",
            "",
            f"任务标题：{str(getattr(task, 'title', '') or '').strip() or '-'}",
            f"任务 ID：{str(getattr(task, 'id', '') or '').strip() or '-'}",
            f"任务类型：{task_type_label}",
            f"专利号/公开号：{str(getattr(task, 'pn', '') or '').strip() or '-'}",
            f"终态状态：{terminal_status}",
            f"最终步骤：{str(getattr(task, 'current_step', '') or '').strip() or '-'}",
            f"终态时间：{completed_at or '-'}",
        ]
        if terminal_status == TERMINAL_FAILED:
            lines.append(f"失败原因：{error_message or '-'}")
        if attachment is not None:
            lines.append(f"PDF 附件：{attachment.filename}")
        lines.extend(
            [
                "",
                "如需再次查看或下载结果，也可在任务列表中打开对应任务。",
            ]
        )
        return "\n".join(lines)

    def _resolve_attachment(self, task: Any, terminal_status: str) -> Optional[AttachmentPayload]:
        metadata = getattr(task, "metadata", {}) if isinstance(getattr(task, "metadata", {}), dict) else {}
        output_files = metadata.get("output_files") if isinstance(metadata.get("output_files"), dict) else {}
        local_attachment = self._resolve_local_attachment(task, output_files)
        if local_attachment is not None:
            return local_attachment

        r2_key = str(output_files.get("r2_key") or "").strip()
        if not r2_key:
            return None

        r2_storage = self.r2_storage_factory()
        if not getattr(r2_storage, "enabled", False):
            return None
        content = r2_storage.get_bytes(r2_key)
        if not content:
            return None
        filename = Path(r2_key).name or f"{str(getattr(task, 'id', '') or 'task').strip()}.pdf"
        return AttachmentPayload(filename=filename, content=content, content_type="application/pdf")

    def _resolve_local_attachment(self, task: Any, output_files: Dict[str, Any]) -> Optional[AttachmentPayload]:
        for candidate in self._local_pdf_candidates(task, output_files):
            path = Path(candidate)
            if not path.exists() or not path.is_file():
                continue
            try:
                content = path.read_bytes()
            except Exception:
                continue
            if not content:
                continue
            mime_type = mimetypes.guess_type(path.name)[0] or "application/pdf"
            return AttachmentPayload(filename=path.name, content=content, content_type=mime_type)
        return None

    def _local_pdf_candidates(self, task: Any, output_files: Dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        output_pdf = str(output_files.get("pdf") or "").strip()
        if output_pdf:
            candidates.append(output_pdf)

        output_dir = Path(str(getattr(task, "output_dir", "") or "").strip() or ".")
        pn = str(getattr(task, "pn", "") or "").strip()
        task_id = str(getattr(task, "id", "") or "").strip()
        defaults = [
            output_dir / "final_report.pdf",
            output_dir / "ai_search_report.pdf",
            output_dir / "final.pdf",
        ]
        if pn:
            defaults.append(output_dir / f"{pn}.pdf")
        if task_id:
            defaults.append(output_dir / f"{task_id}.pdf")

        seen: set[str] = set()
        deduped: list[str] = []
        for item in [str(path) for path in defaults]:
            normalized = str(item or "").strip()
            if normalized and normalized not in seen:
                deduped.append(normalized)
                seen.add(normalized)
        if output_pdf:
            deduped = [output_pdf] + [item for item in deduped if item != output_pdf]
        return deduped

    def _existing_delivery(self, task: Any, terminal_status: str) -> Optional[Dict[str, Any]]:
        metadata = getattr(task, "metadata", {}) if isinstance(getattr(task, "metadata", {}), dict) else {}
        notifications = metadata.get("notifications") if isinstance(metadata.get("notifications"), dict) else {}
        email_meta = notifications.get("email") if isinstance(notifications.get("email"), dict) else {}
        record = email_meta.get(terminal_status)
        return deepcopy(record) if isinstance(record, dict) else None

    def _save_delivery_record(
        self,
        task_id: str,
        terminal_status: str,
        *,
        delivery_status: str,
        reason: str,
        task: Any,
        recipient: str = "",
        subject: str = "",
        attachment: Optional[AttachmentPayload] = None,
        send_result: Optional[EmailSendResult] = None,
    ) -> Dict[str, Any]:
        latest_task = self.storage.get_task(task_id) or task
        metadata = deepcopy(getattr(latest_task, "metadata", {}) if isinstance(getattr(latest_task, "metadata", {}), dict) else {})
        notifications = metadata.get("notifications") if isinstance(metadata.get("notifications"), dict) else {}
        email_meta = notifications.get("email") if isinstance(notifications.get("email"), dict) else {}
        record = {
            "status": delivery_status,
            "recipient": recipient or None,
            "subject": subject or None,
            "reason": reason or None,
            "attachment_name": attachment.filename if attachment is not None else None,
            "provider": send_result.provider if send_result is not None else None,
            "provider_message_id": send_result.provider_message_id if send_result is not None else None,
            "processed_at": utc_now_z(timespec="seconds"),
        }
        email_meta[terminal_status] = record
        notifications["email"] = email_meta
        metadata["notifications"] = notifications
        self.storage.update_task(task_id, metadata=metadata)
        return record

    def _emit_delivery_log(self, event_name: str, task: Any, terminal_status: str, record: Dict[str, Any]) -> None:
        self.system_log_emitter(
            category="task_execution",
            event_name=event_name,
            owner_id=str(getattr(task, "owner_id", "") or "").strip() or None,
            task_id=str(getattr(task, "id", "") or "").strip() or None,
            task_type=str(getattr(task, "task_type", "") or "").strip() or None,
            success=event_name == "task_email_sent",
            message=f"任务终态邮件{record.get('status') or 'unknown'}",
            payload={
                "terminal_status": terminal_status,
                "recipient": record.get("recipient"),
                "reason": record.get("reason"),
                "attachment_name": record.get("attachment_name"),
                "provider": record.get("provider"),
                "provider_message_id": record.get("provider_message_id"),
            },
        )


def build_task_email_notification_service(
    *,
    storage: Any,
    email_sender: Optional[EmailSender] = None,
    system_log_emitter: Optional[SystemLogEmitter] = None,
    r2_storage_factory: Optional[R2Factory] = None,
) -> TaskEmailNotificationService:
    return TaskEmailNotificationService(
        storage=storage,
        email_sender=email_sender,
        system_log_emitter=system_log_emitter,
        r2_storage_factory=r2_storage_factory,
    )
