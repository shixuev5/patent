"""Task terminal email notification service."""

from __future__ import annotations

import mimetypes
import re
from copy import deepcopy
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
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
DEFAULT_TASKS_URL = "https://aipatents.cn/tasks"
DEFAULT_FAILURE_REASON = "处理过程中出现异常，请前往系统查看详情。"
AUTO_GENERATED_TITLE_PATTERNS = (
    re.compile(r"^AI 分析任务 - [0-9A-Fa-f]{8}$"),
    re.compile(r"^AI 检索会话 - [0-9A-Fa-f]{8}$"),
    re.compile(r"^AI 答复任务 - [0-9A-Fa-f]{8}$"),
    re.compile(r"^AI 审查任务 - [0-9A-Fa-f]{8}$"),
)

TASK_TYPE_LABELS = {
    TaskType.PATENT_ANALYSIS.value: "AI 分析",
    TaskType.AI_REPLY.value: "AI 答复",
    TaskType.AI_REVIEW.value: "AI 审查",
    TaskType.AI_SEARCH.value: "AI 检索",
}


@dataclass
class AttachmentPayload:
    filename: str
    content: bytes
    content_type: str = "application/pdf"


@dataclass
class TaskEmailTemplateContext:
    task_type_label: str
    terminal_status: str
    subject_prefix: str
    subject_suffix: str
    summary_title: str
    opening_sentence: str
    identifier_label: str = ""
    identifier_value: str = ""
    completed_at: str = ""
    attachment_name: str = ""
    failure_reason: str = ""
    cta_url: str = DEFAULT_TASKS_URL
    cta_label: str = "前往系统查看"


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

        recipients = self._resolve_recipients(task)
        if not recipients:
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
                recipients=recipients,
                subject=subject,
            )
            self._emit_delivery_log("task_email_failed", task, normalized_status, record)
            return record

        try:
            sender = self.email_sender or self._build_sender()
            message = self._build_message(
                task=task,
                recipients=recipients,
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
                recipients=recipients,
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
            recipients=recipients,
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

    def _resolve_recipients(self, task: Any) -> list[str]:
        owner_id = str(getattr(task, "owner_id", "") or "").strip()
        if not owner_id or owner_id.startswith("guest"):
            return []
        if not hasattr(self.storage, "get_user_by_owner_id"):
            return []
        user = self.storage.get_user_by_owner_id(owner_id)
        if not user or not bool(getattr(user, "notification_email_enabled", False)):
            return []

        candidates = [
            str(getattr(user, "work_notification_email", "") or "").strip(),
            str(getattr(user, "personal_notification_email", "") or "").strip(),
        ]
        recipients: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            key = item.lower()
            if not item or key in seen:
                continue
            recipients.append(item)
            seen.add(key)
        return recipients

    def _build_subject(self, task: Any, terminal_status: str, *, task_type: Optional[str] = None) -> str:
        context = self._build_template_context(
            task=task,
            terminal_status=terminal_status,
            error_message="",
            attachment=None,
            task_type=task_type,
        )
        subject = f"【结果通知】{context.task_type_label}{context.subject_prefix}"
        if context.subject_suffix:
            subject = f"{subject} - {context.subject_suffix}"
        return subject

    def _build_message(
        self,
        *,
        task: Any,
        recipients: list[str],
        terminal_status: str,
        subject: str,
        error_message: str,
        attachment: Optional[AttachmentPayload],
        task_type: Optional[str] = None,
    ) -> EmailMessage:
        message = EmailMessage()
        context = self._build_template_context(
            task=task,
            terminal_status=terminal_status,
            error_message=error_message,
            attachment=attachment,
            task_type=task_type,
        )
        from_address = str(
            getattr(settings, "BREVO_FROM_ADDRESS", "") or getattr(settings, "SMTP_FROM_ADDRESS", "") or ""
        ).strip()
        from_name = str(
            getattr(settings, "BREVO_FROM_NAME", "") or getattr(settings, "SMTP_FROM_NAME", "") or ""
        ).strip()
        message["Subject"] = subject
        message["From"] = formataddr((from_name, from_address)) if from_name else from_address
        message["To"] = ", ".join(recipients)
        message.set_content(
            self._build_plain_body(context),
            subtype="plain",
            charset="utf-8",
        )
        message.add_alternative(self._build_html_body(context), subtype="html", charset="utf-8")
        if attachment is not None:
            maintype, subtype = attachment.content_type.split("/", 1) if "/" in attachment.content_type else ("application", "octet-stream")
            message.add_attachment(
                attachment.content,
                maintype=maintype,
                subtype=subtype,
                filename=attachment.filename,
            )
        return message

    def _build_template_context(
        self,
        *,
        task: Any,
        terminal_status: str,
        error_message: str,
        attachment: Optional[AttachmentPayload],
        task_type: Optional[str] = None,
    ) -> TaskEmailTemplateContext:
        task_type_value = str(task_type or getattr(task, "task_type", "") or "").strip().lower()
        task_type_label = TASK_TYPE_LABELS.get(task_type_value, task_type_value or "任务")
        completed_at = format_for_admin_local(
            getattr(task, "completed_at", None) or getattr(task, "updated_at", None),
            naive_strategy="utc",
            timespec="seconds",
        ) or ""
        identifier_label, identifier_value = self._resolve_business_identifier(task)
        subject_suffix = identifier_value
        if terminal_status == TERMINAL_COMPLETED:
            return TaskEmailTemplateContext(
                task_type_label=task_type_label,
                terminal_status=terminal_status,
                subject_prefix="已完成",
                subject_suffix=subject_suffix,
                summary_title=f"{task_type_label}已完成",
                opening_sentence=f"您提交的{task_type_label}任务已处理完成，相关结果已生成。",
                identifier_label=identifier_label,
                identifier_value=identifier_value,
                completed_at=completed_at,
                attachment_name=attachment.filename if attachment is not None else "",
            )
        return TaskEmailTemplateContext(
            task_type_label=task_type_label,
            terminal_status=terminal_status,
            subject_prefix="处理未完成",
            subject_suffix=subject_suffix,
            summary_title=f"{task_type_label}处理未完成",
            opening_sentence=f"您提交的{task_type_label}任务暂未完成处理。",
            identifier_label=identifier_label,
            identifier_value=identifier_value,
            completed_at=completed_at,
            failure_reason=self._sanitize_failure_reason(error_message),
        )

    def _resolve_business_identifier(self, task: Any) -> tuple[str, str]:
        pn = str(getattr(task, "pn", "") or "").strip()
        if pn:
            return "专利号/公开号", pn
        title = str(getattr(task, "title", "") or "").strip()
        if title and not self._is_auto_generated_title(title):
            return "任务名称", title
        return "", ""

    def _is_auto_generated_title(self, title: str) -> bool:
        normalized = str(title or "").strip()
        return any(pattern.match(normalized) for pattern in AUTO_GENERATED_TITLE_PATTERNS)

    def _sanitize_failure_reason(self, error_message: str) -> str:
        compact = re.sub(r"\s+", " ", str(error_message or "").strip())
        if not compact:
            return DEFAULT_FAILURE_REASON
        lowered = compact.lower()
        blocked_markers = (
            "traceback",
            "file \"",
            ".py",
            "exception",
            " stack ",
            "sql",
            "requests.",
            "http://",
            "https://",
            "/users/",
            "/home/",
            "\\",
        )
        if any(marker in lowered for marker in blocked_markers):
            return DEFAULT_FAILURE_REASON
        if len(compact) > 80:
            compact = f"{compact[:77].rstrip()}..."
        return compact

    def _build_plain_body(self, context: TaskEmailTemplateContext) -> str:
        lines = [
            "尊敬的用户：",
            "",
            context.opening_sentence,
        ]
        if context.identifier_label and context.identifier_value:
            lines.append(f"{context.identifier_label}：{context.identifier_value}")
        if context.completed_at:
            lines.append(f"完成时间：{context.completed_at}")
        if context.terminal_status == TERMINAL_COMPLETED and context.attachment_name:
            lines.append(f"结果附件：{context.attachment_name}")
            lines.append("相关结果已随邮件附上。")
        if context.terminal_status == TERMINAL_FAILED:
            lines.append(f"简要说明：{context.failure_reason or DEFAULT_FAILURE_REASON}")
        lines.extend(
            [
                "",
                "您可前往系统查看相关详情：",
                context.cta_url,
                "",
                "此致",
                "",
                "AI Patents",
                "专利审查助手",
            ]
        )
        return "\n".join(lines)

    def _build_html_body(self, context: TaskEmailTemplateContext) -> str:
        summary_bg = "#ecfeff" if context.terminal_status == TERMINAL_COMPLETED else "#fff7ed"
        summary_border = "#67e8f9" if context.terminal_status == TERMINAL_COMPLETED else "#fdba74"
        badge_bg = "#0891b2" if context.terminal_status == TERMINAL_COMPLETED else "#c2410c"
        detail_rows = []
        if context.identifier_label and context.identifier_value:
            detail_rows.append(self._build_html_detail_row(context.identifier_label, context.identifier_value))
        if context.completed_at:
            detail_rows.append(self._build_html_detail_row("完成时间", context.completed_at))
        if context.terminal_status == TERMINAL_COMPLETED and context.attachment_name:
            detail_rows.append(self._build_html_detail_row("结果附件", context.attachment_name))
        if context.terminal_status == TERMINAL_FAILED:
            detail_rows.append(
                self._build_html_detail_row("简要说明", context.failure_reason or DEFAULT_FAILURE_REASON)
            )
        details_html = "".join(detail_rows)
        return (
            "<!doctype html>"
            "<html lang=\"zh-CN\">"
            "<body style=\"margin:0;background:#f4f7fb;padding:24px 12px;color:#0f172a;"
            "font-family:'Noto Sans SC','PingFang SC','Microsoft YaHei',sans-serif;\">"
            "<div style=\"max-width:680px;margin:0 auto;\">"
            "<div style=\"background:linear-gradient(135deg,#0f172a 0%,#164e63 100%);"
            "border-radius:20px 20px 0 0;padding:28px 32px;color:#ffffff;\">"
            "<div style=\"font-size:13px;letter-spacing:0.24em;text-transform:uppercase;color:#a5f3fc;\">AI Patents</div>"
            "<div style=\"margin-top:8px;font-size:24px;font-weight:700;\">专利审查助手</div>"
            "<div style=\"margin-top:8px;font-size:14px;line-height:22px;color:#e2e8f0;\">正式任务处理结果通知</div>"
            "</div>"
            "<div style=\"background:#ffffff;border:1px solid #dbeafe;border-top:none;border-radius:0 0 20px 20px;padding:32px;\">"
            f"<div style=\"border:1px solid {summary_border};background:{summary_bg};border-radius:18px;padding:20px 22px;\">"
            f"<div style=\"display:inline-block;background:{badge_bg};color:#ffffff;border-radius:999px;padding:6px 12px;"
            "font-size:12px;font-weight:700;letter-spacing:0.04em;\">结果通知</div>"
            f"<div style=\"margin-top:14px;font-size:28px;line-height:36px;font-weight:700;color:#0f172a;\">{escape(context.summary_title)}</div>"
            f"<div style=\"margin-top:10px;font-size:15px;line-height:24px;color:#334155;\">{escape(context.opening_sentence)}</div>"
            "</div>"
            f"<div style=\"margin-top:22px;border:1px solid #e2e8f0;border-radius:18px;padding:20px 22px;background:#ffffff;\">{details_html}</div>"
            "<div style=\"margin-top:22px;font-size:14px;line-height:24px;color:#475569;\">"
            "如需进一步查看任务结果或处理详情，请进入系统任务列表。"
            "</div>"
            f"<div style=\"margin-top:24px;\"><a href=\"{escape(context.cta_url)}\" "
            "style=\"display:inline-block;background:#0891b2;color:#ffffff;text-decoration:none;"
            "padding:12px 22px;border-radius:999px;font-size:14px;font-weight:700;\">"
            f"{escape(context.cta_label)}</a></div>"
            "<div style=\"margin-top:28px;padding-top:18px;border-top:1px solid #e2e8f0;font-size:12px;"
            "line-height:20px;color:#64748b;\">"
            "本邮件为系统通知邮件，请勿直接回复。<br>"
            "AI Patents | 专利审查助手 | aipatents.cn"
            "</div>"
            "</div>"
            "</div>"
            "</body>"
            "</html>"
        )

    def _build_html_detail_row(self, label: str, value: str) -> str:
        return (
            "<div style=\"padding:10px 0;border-bottom:1px solid #e2e8f0;\">"
            f"<div style=\"font-size:12px;font-weight:700;letter-spacing:0.04em;color:#0891b2;\">{escape(label)}</div>"
            f"<div style=\"margin-top:6px;font-size:15px;line-height:24px;color:#0f172a;\">{escape(value)}</div>"
            "</div>"
        )

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
        recipients: Optional[list[str]] = None,
        subject: str = "",
        attachment: Optional[AttachmentPayload] = None,
        send_result: Optional[EmailSendResult] = None,
    ) -> Dict[str, Any]:
        latest_task = self.storage.get_task(task_id) or task
        metadata = deepcopy(getattr(latest_task, "metadata", {}) if isinstance(getattr(latest_task, "metadata", {}), dict) else {})
        notifications = metadata.get("notifications") if isinstance(metadata.get("notifications"), dict) else {}
        email_meta = notifications.get("email") if isinstance(notifications.get("email"), dict) else {}
        recipient_list = [item for item in (recipients or []) if str(item or "").strip()]
        record = {
            "status": delivery_status,
            "recipients": recipient_list or None,
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
                "recipients": record.get("recipients"),
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
