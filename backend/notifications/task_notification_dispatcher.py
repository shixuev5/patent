"""Dispatch task terminal notifications to all enabled channels."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .task_email_service import build_task_email_notification_service
from .task_wechat_service import build_task_wechat_notification_service


SystemLogEmitter = Callable[..., None]


class TaskNotificationDispatcher:
    def __init__(
        self,
        *,
        storage: Any,
        system_log_emitter: Optional[SystemLogEmitter] = None,
    ) -> None:
        self.storage = storage
        self.system_log_emitter = system_log_emitter

    def notify_task_terminal_status(
        self,
        task_id: str,
        *,
        terminal_status: str,
        task_type: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        email_result = build_task_email_notification_service(
            storage=self.storage,
            system_log_emitter=self.system_log_emitter,
        ).notify_task_terminal_status(
            task_id,
            terminal_status=terminal_status,
            task_type=task_type,
            error_message=error_message,
        )
        wechat_result = build_task_wechat_notification_service(
            storage=self.storage,
            system_log_emitter=self.system_log_emitter,
        ).notify_task_terminal_status(
            task_id,
            terminal_status=terminal_status,
            task_type=task_type,
            error_message=error_message,
        )
        return {
            "email": email_result,
            "wechat": wechat_result,
        }


def build_task_notification_dispatcher(
    *,
    storage: Any,
    system_log_emitter: Optional[SystemLogEmitter] = None,
) -> TaskNotificationDispatcher:
    return TaskNotificationDispatcher(
        storage=storage,
        system_log_emitter=system_log_emitter,
    )
