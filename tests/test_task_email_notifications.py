from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from typing import Any

from config import settings
from backend.notifications.task_email_service import build_task_email_notification_service
from backend.storage.models import TaskType, User
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage import SQLiteTaskStorage


class _FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def send(self, message: EmailMessage, *, metadata=None):
        self.messages.append(message)
        from backend.notifications.email_sender import EmailSendResult

        return EmailSendResult(provider="fake", provider_message_id="fake-message-id")


class _FakeR2Storage:
    def __init__(self, payload: bytes | None = None) -> None:
        self.enabled = True
        self.payload = payload or b"%PDF-1.4\n%from-r2\n"

    def get_bytes(self, key: str) -> bytes | None:
        return self.payload if key else None


def _get_plain_body(message: EmailMessage) -> str:
    body = message.get_body(preferencelist=("plain",))
    return body.get_content() if body is not None else ""


def _get_html_body(message: EmailMessage) -> str:
    body = message.get_body(preferencelist=("html",))
    return body.get_content() if body is not None else ""


def _configure_email_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "EMAIL_NOTIFICATIONS_ENABLED", True)
    monkeypatch.setattr(settings, "EMAIL_PROVIDER", "brevo")
    monkeypatch.setattr(settings, "BREVO_API_KEY", "brevo-key")
    monkeypatch.setattr(settings, "BREVO_API_BASE_URL", "https://api.brevo.com/v3")
    monkeypatch.setattr(settings, "BREVO_FROM_ADDRESS", "noreply@example.com")
    monkeypatch.setattr(settings, "BREVO_FROM_NAME", "Patent Bot")
    monkeypatch.setattr(settings, "BREVO_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "noreply@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret")
    monkeypatch.setattr(settings, "SMTP_FROM_ADDRESS", "noreply@example.com")
    monkeypatch.setattr(settings, "SMTP_FROM_NAME", "Patent Bot")
    monkeypatch.setattr(settings, "SMTP_USE_TLS", True)
    monkeypatch.setattr(settings, "SMTP_USE_SSL", False)


def _mount_storage(tmp_path: Path) -> tuple[SQLiteTaskStorage, PipelineTaskManager]:
    storage = SQLiteTaskStorage(tmp_path / "task_email_notifications.db")
    manager = PipelineTaskManager(storage=storage)
    return storage, manager


def _create_user(
    storage: SQLiteTaskStorage,
    owner_id: str,
    email: str,
    *,
    notification_email_enabled: bool = True,
    work_notification_email: str | None = None,
    personal_notification_email: str | None = None,
) -> None:
    storage.upsert_authing_user(
        User(
            owner_id=owner_id,
            authing_sub=owner_id.removeprefix("authing:") or owner_id,
            email=email,
            name="tester",
            notification_email_enabled=notification_email_enabled,
            work_notification_email=work_notification_email,
            personal_notification_email=personal_notification_email,
        )
    )


def test_task_email_notification_sends_completed_pdf_and_dedupes(monkeypatch, tmp_path):
    _configure_email_settings(monkeypatch)
    storage, manager = _mount_storage(tmp_path)
    _create_user(
        storage,
        "authing:user-1",
        "user-1@example.com",
        work_notification_email="work-1@example.com",
        personal_notification_email="home-1@example.com",
    )

    pdf_path = tmp_path / "completed.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%completed\n")
    task = manager.create_task(
        owner_id="authing:user-1",
        task_type=TaskType.PATENT_ANALYSIS.value,
        pn="CN123456A",
        title="AI 分析任务 - 测试",
    )
    manager.storage.update_task(task.id, current_step="渲染报告")
    manager.complete_task(task.id, output_files={"pdf": str(pdf_path), "pn": "CN123456A"})

    sender = _FakeEmailSender()
    logs: list[dict[str, Any]] = []
    service = build_task_email_notification_service(
        storage=storage,
        email_sender=sender,
        system_log_emitter=lambda **kwargs: logs.append(kwargs),
    )

    first = service.notify_task_terminal_status(task.id, terminal_status="completed")
    second = service.notify_task_terminal_status(task.id, terminal_status="completed")

    assert first["status"] == "sent"
    assert second["status"] == "duplicate"
    assert len(sender.messages) == 1
    assert sender.messages[0]["To"] == "work-1@example.com, home-1@example.com"
    assert str(sender.messages[0]["Subject"]) == "【结果通知】AI 分析已完成 - CN123456A"
    plain_body = _get_plain_body(sender.messages[0])
    html_body = _get_html_body(sender.messages[0])
    assert "尊敬的用户：" in plain_body
    assert "专利号/公开号：CN123456A" in plain_body
    assert "结果附件：completed.pdf" in plain_body
    assert "任务 ID" not in plain_body
    assert "渲染报告" not in plain_body
    assert "终态状态" not in plain_body
    assert "AI Patents" in html_body
    assert "专利审查助手" in html_body
    assert "CN123456A" in html_body
    assert "前往系统查看" in html_body
    assert "https://aipatents.cn/tasks" in html_body
    assert "任务 ID" not in html_body
    attachments = list(sender.messages[0].iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "completed.pdf"
    latest = storage.get_task(task.id)
    assert latest is not None
    assert latest.metadata["notifications"]["email"]["completed"]["status"] == "sent"
    assert latest.metadata["notifications"]["email"]["completed"]["provider"] == "fake"
    assert latest.metadata["notifications"]["email"]["completed"]["provider_message_id"] == "fake-message-id"
    assert latest.metadata["notifications"]["email"]["completed"]["recipients"] == [
        "work-1@example.com",
        "home-1@example.com",
    ]
    assert logs[-1]["event_name"] == "task_email_sent"


def test_task_email_notification_skips_when_owner_email_missing(monkeypatch, tmp_path):
    _configure_email_settings(monkeypatch)
    storage, manager = _mount_storage(tmp_path)
    task = manager.create_task(
        owner_id="guest-user",
        task_type=TaskType.AI_REPLY.value,
        title="AI 答复任务 - 测试",
    )
    manager.fail_task(task.id, "boom")

    sender = _FakeEmailSender()
    service = build_task_email_notification_service(storage=storage, email_sender=sender)
    result = service.notify_task_terminal_status(task.id, terminal_status="failed")

    latest = storage.get_task(task.id)
    assert result["status"] == "skipped"
    assert len(sender.messages) == 0
    assert latest is not None
    assert latest.metadata["notifications"]["email"]["failed"]["reason"] == "recipient_email_missing"


def test_task_email_notification_skips_when_notification_disabled(monkeypatch, tmp_path):
    _configure_email_settings(monkeypatch)
    storage, manager = _mount_storage(tmp_path)
    _create_user(
        storage,
        "authing:user-disabled",
        "login-disabled@example.com",
        notification_email_enabled=False,
        work_notification_email="work-disabled@example.com",
    )
    task = manager.create_task(
        owner_id="authing:user-disabled",
        task_type=TaskType.AI_REPLY.value,
        title="AI 答复任务 - 禁用",
    )
    manager.fail_task(task.id, "boom")

    sender = _FakeEmailSender()
    service = build_task_email_notification_service(storage=storage, email_sender=sender)
    result = service.notify_task_terminal_status(task.id, terminal_status="failed")

    latest = storage.get_task(task.id)
    assert result["status"] == "skipped"
    assert len(sender.messages) == 0
    assert latest is not None
    assert latest.metadata["notifications"]["email"]["failed"]["reason"] == "recipient_email_missing"


def test_task_email_notification_sends_failed_email_without_attachment(monkeypatch, tmp_path):
    _configure_email_settings(monkeypatch)
    storage, manager = _mount_storage(tmp_path)
    _create_user(
        storage,
        "authing:user-2",
        "user-2@example.com",
        work_notification_email="work-2@example.com",
    )
    task = manager.create_task(
        owner_id="authing:user-2",
        task_type=TaskType.AI_SEARCH.value,
        title="AI 检索会话 - 测试",
    )
    manager.storage.update_task(task.id, current_step="执行专利检索")
    manager.fail_task(task.id, "执行失败")

    sender = _FakeEmailSender()
    service = build_task_email_notification_service(storage=storage, email_sender=sender)
    result = service.notify_task_terminal_status(task.id, terminal_status="failed")

    assert result["status"] == "sent"
    assert len(sender.messages) == 1
    assert sender.messages[0]["To"] == "work-2@example.com"
    assert list(sender.messages[0].iter_attachments()) == []
    plain_body = _get_plain_body(sender.messages[0])
    html_body = _get_html_body(sender.messages[0])
    assert str(sender.messages[0]["Subject"]) == "【结果通知】AI 检索处理未完成 - AI 检索会话 - 测试"
    assert "简要说明：执行失败" in plain_body
    assert "执行专利检索" not in plain_body
    assert "failed" not in plain_body
    assert "执行失败" in html_body
    assert "前往系统查看" in html_body
    assert "https://aipatents.cn/tasks" in html_body


def test_task_email_notification_reads_pdf_attachment_from_r2(monkeypatch, tmp_path):
    _configure_email_settings(monkeypatch)
    storage, manager = _mount_storage(tmp_path)
    _create_user(
        storage,
        "authing:user-3",
        "user-3@example.com",
        work_notification_email="work-3@example.com",
    )
    task = manager.create_task(
        owner_id="authing:user-3",
        task_type=TaskType.PATENT_ANALYSIS.value,
        pn="CN999999A",
        title="AI 分析任务 - R2 复用",
    )
    manager.complete_task(
        task.id,
        output_files={
            "pn": "CN999999A",
            "r2_key": "patent/CN999999A/ai_analysis.pdf",
            "analysis_r2_key": "patent/CN999999A/ai_analysis.json",
        },
    )

    sender = _FakeEmailSender()
    service = build_task_email_notification_service(
        storage=storage,
        email_sender=sender,
        r2_storage_factory=lambda: _FakeR2Storage(),
    )
    result = service.notify_task_terminal_status(task.id, terminal_status="completed")

    assert result["status"] == "sent"
    attachments = list(sender.messages[0].iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "ai_analysis.pdf"


def test_task_email_notification_records_failure_when_brevo_config_invalid(monkeypatch, tmp_path):
    _configure_email_settings(monkeypatch)
    monkeypatch.setattr(settings, "BREVO_API_KEY", "")
    storage, manager = _mount_storage(tmp_path)
    _create_user(
        storage,
        "authing:user-4",
        "user-4@example.com",
        work_notification_email="work-4@example.com",
    )

    pdf_path = tmp_path / "completed.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%completed\n")
    task = manager.create_task(
        owner_id="authing:user-4",
        task_type=TaskType.AI_REPLY.value,
        title="AI 答复任务 - SMTP",
    )
    manager.complete_task(task.id, output_files={"pdf": str(pdf_path)})

    service = build_task_email_notification_service(storage=storage)
    result = service.notify_task_terminal_status(task.id, terminal_status="completed")

    latest = storage.get_task(task.id)
    assert result["status"] == "failed"
    assert latest is not None
    assert latest.metadata["notifications"]["email"]["completed"]["status"] == "failed"
    assert latest.metadata["notifications"]["email"]["completed"]["reason"] == "BREVO_API_KEY 未配置。"


def test_task_email_notification_uses_brevo_api_payload(monkeypatch, tmp_path):
    _configure_email_settings(monkeypatch)
    storage, manager = _mount_storage(tmp_path)
    _create_user(
        storage,
        "authing:user-5",
        "user-5@example.com",
        work_notification_email="work-5@example.com",
        personal_notification_email="home-5@example.com",
    )

    pdf_path = tmp_path / "completed.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%completed\n")
    task = manager.create_task(
        owner_id="authing:user-5",
        task_type=TaskType.AI_SEARCH.value,
        title="AI 检索会话 - Brevo",
    )
    manager.complete_task(task.id, output_files={"pdf": str(pdf_path)})

    captured: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 201
        content = b'{"messageId":"brevo-message-id"}'

        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, str]:
            return {"messageId": "brevo-message-id"}

    def _fake_post(url: str, *, headers: dict[str, str], json: dict[str, Any], timeout: int):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse()

    import backend.notifications.email_sender as email_sender_module

    monkeypatch.setattr(email_sender_module.requests, "post", _fake_post)

    service = build_task_email_notification_service(storage=storage)
    result = service.notify_task_terminal_status(task.id, terminal_status="completed")
    latest = storage.get_task(task.id)

    assert result["status"] == "sent"
    assert captured["url"] == "https://api.brevo.com/v3/smtp/email"
    assert captured["headers"]["api-key"] == "brevo-key"
    assert captured["json"]["sender"]["email"] == "noreply@example.com"
    assert captured["json"]["to"] == [{"email": "work-5@example.com"}, {"email": "home-5@example.com"}]
    assert "htmlContent" in captured["json"]
    assert "AI Patents" in captured["json"]["htmlContent"]
    assert "https://aipatents.cn/tasks" in captured["json"]["htmlContent"]
    assert "textContent" in captured["json"]
    assert "尊敬的用户：" in captured["json"]["textContent"]
    assert captured["json"]["attachment"][0]["name"] == "completed.pdf"
    assert f"task_id:{task.id}" in captured["json"]["tags"]
    assert latest is not None
    assert latest.metadata["notifications"]["email"]["completed"]["recipients"] == [
        "work-5@example.com",
        "home-5@example.com",
    ]


def test_task_email_notification_dedupes_same_work_and_personal_address(monkeypatch, tmp_path):
    _configure_email_settings(monkeypatch)
    storage, manager = _mount_storage(tmp_path)
    _create_user(
        storage,
        "authing:user-6",
        "user-6@example.com",
        work_notification_email="same@example.com",
        personal_notification_email="same@example.com",
    )

    pdf_path = tmp_path / "completed.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%completed\n")
    task = manager.create_task(
        owner_id="authing:user-6",
        task_type=TaskType.AI_SEARCH.value,
        title="AI 检索会话 - 去重",
    )
    manager.complete_task(task.id, output_files={"pdf": str(pdf_path)})

    sender = _FakeEmailSender()
    service = build_task_email_notification_service(storage=storage, email_sender=sender)
    result = service.notify_task_terminal_status(task.id, terminal_status="completed")

    latest = storage.get_task(task.id)
    assert result["status"] == "sent"
    assert len(sender.messages) == 1
    assert sender.messages[0]["To"] == "same@example.com"
    assert latest is not None
    assert latest.metadata["notifications"]["email"]["completed"]["recipients"] == ["same@example.com"]


def test_task_email_notification_filters_auto_title_and_sanitizes_failure_reason(monkeypatch, tmp_path):
    _configure_email_settings(monkeypatch)
    storage, manager = _mount_storage(tmp_path)
    _create_user(
        storage,
        "authing:user-7",
        "user-7@example.com",
        work_notification_email="work-7@example.com",
    )
    task = manager.create_task(
        owner_id="authing:user-7",
        task_type=TaskType.AI_REPLY.value,
    )
    manager.fail_task(
        task.id,
        "Traceback (most recent call last): File \"/Users/demo/app.py\", line 10, in <module> RuntimeError: boom",
    )

    sender = _FakeEmailSender()
    service = build_task_email_notification_service(storage=storage, email_sender=sender)
    result = service.notify_task_terminal_status(task.id, terminal_status="failed")

    assert result["status"] == "sent"
    assert str(sender.messages[0]["Subject"]) == "【结果通知】AI 答复处理未完成"
    plain_body = _get_plain_body(sender.messages[0])
    html_body = _get_html_body(sender.messages[0])
    assert "任务名称" not in plain_body
    assert task.id not in plain_body
    assert "处理过程中出现异常，请前往系统查看详情。" in plain_body
    assert "Traceback" not in plain_body
    assert "app.py" not in plain_body
    assert "处理过程中出现异常，请前往系统查看详情。" in html_body
    assert "Traceback" not in html_body
