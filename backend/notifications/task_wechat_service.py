"""WeChat task notification queueing service."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from backend.storage import WeChatDeliveryJob
from backend.time_utils import utc_now, utc_now_z
from backend.wechat_delivery_events import delivery_event_broker
from config import settings


SystemLogEmitter = Callable[..., None]

TERMINAL_COMPLETED = "completed"
TERMINAL_FAILED = "failed"
PENDING_ACTION_EVENT = "ai_search.pending_action"
SUPPORTED_PENDING_ACTION_TYPES = {"question", "plan_confirmation", "human_decision"}


def _noop_emit_system_log(**_kwargs: Any) -> None:
    return


class TaskWeChatNotificationService:
    def __init__(
        self,
        *,
        storage: Any,
        system_log_emitter: Optional[SystemLogEmitter] = None,
    ) -> None:
        self.storage = storage
        self.system_log_emitter = system_log_emitter or _noop_emit_system_log

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

        if not bool(settings.WECHAT_INTEGRATION_ENABLED):
            record = self._save_delivery_record(task_id, normalized_status, task=task, delivery_status="skipped", reason="wechat_integration_disabled")
            self._emit_delivery_log("task_wechat_skipped", task, normalized_status, record)
            return record

        binding = self.storage.get_wechat_binding_by_owner(str(getattr(task, "owner_id", "") or "").strip())
        if not binding or str(binding.status or "").strip() != "active":
            record = self._save_delivery_record(task_id, normalized_status, task=task, delivery_status="skipped", reason="binding_missing")
            self._emit_delivery_log("task_wechat_skipped", task, normalized_status, record)
            return record

        if normalized_status == TERMINAL_COMPLETED and not bool(binding.push_task_completed):
            record = self._save_delivery_record(task_id, normalized_status, task=task, delivery_status="skipped", reason="push_completed_disabled")
            self._emit_delivery_log("task_wechat_skipped", task, normalized_status, record)
            return record
        if normalized_status == TERMINAL_FAILED and not bool(binding.push_task_failed):
            record = self._save_delivery_record(task_id, normalized_status, task=task, delivery_status="skipped", reason="push_failed_disabled")
            self._emit_delivery_log("task_wechat_skipped", task, normalized_status, record)
            return record

        metadata = getattr(task, "metadata", {}) if isinstance(getattr(task, "metadata", {}), dict) else {}
        delivery_meta = metadata.get("wechat_delivery") if isinstance(metadata.get("wechat_delivery"), dict) else {}
        peer_id = str(delivery_meta.get("peer_id") or getattr(binding, "delivery_peer_id", "") or "").strip()
        peer_name = str(delivery_meta.get("peer_name") or getattr(binding, "delivery_peer_name", "") or "").strip() or None
        if not peer_id:
            record = self._save_delivery_record(task_id, normalized_status, task=task, delivery_status="skipped", reason="delivery_peer_missing")
            self._emit_delivery_log("task_wechat_skipped", task, normalized_status, record)
            return record

        payload = {
            "taskId": str(getattr(task, "id", "") or "").strip(),
            "taskType": str(task_type or getattr(task, "task_type", "") or "").strip(),
            "title": str(getattr(task, "title", "") or "").strip() or str(getattr(task, "id", "") or "").strip(),
            "terminalStatus": normalized_status,
            "errorMessage": str(error_message or getattr(task, "error_message", "") or "").strip() or None,
            "outputFiles": getattr(task, "metadata", {}).get("output_files") if isinstance(getattr(task, "metadata", {}), dict) else None,
            "accountId": getattr(binding, "bot_account_id", None),
            "peerId": peer_id,
            "peerName": peer_name,
        }
        job = self.storage.create_wechat_delivery_job(
            WeChatDeliveryJob(
                delivery_job_id=f"wdj-{uuid4().hex[:12]}",
                owner_id=str(getattr(task, "owner_id", "") or "").strip(),
                binding_id=binding.binding_id,
                task_id=str(getattr(task, "id", "") or "").strip(),
                event_type=f"task.{normalized_status}",
                status="pending",
                delivery_stage="queued",
                payload=payload,
                stage_details={"queued_at": utc_now_z()},
                attempt_count=0,
                max_attempts=int(getattr(settings, "WECHAT_DELIVERY_MAX_ATTEMPTS", 3) or 3),
                next_attempt_at=utc_now(),
            )
        )
        delivery_event_broker.publish()
        record = self._save_delivery_record(
            task_id,
            normalized_status,
            task=task,
            delivery_status="queued",
            reason="",
            delivery_job_id=job.delivery_job_id,
            binding_id=binding.binding_id,
        )
        self._emit_delivery_log("task_wechat_queued", task, normalized_status, record)
        return record

    def notify_ai_search_pending_action(
        self,
        task_id: str,
        *,
        pending_action: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        action = pending_action if isinstance(pending_action, dict) else {}
        action_id = str(action.get("actionId") or action.get("action_id") or "").strip()
        action_type = str(action.get("actionType") or action.get("action_type") or "").strip()
        if not action_id or action_type not in SUPPORTED_PENDING_ACTION_TYPES:
            return {"status": "ignored", "reason": "unsupported_pending_action"}

        task = self.storage.get_task(task_id)
        if not task:
            return {"status": "ignored", "reason": "task_not_found"}

        existing = self._existing_pending_action_delivery(task, action_id)
        if existing is not None:
            return {"status": "duplicate", "record": existing}

        if not bool(settings.WECHAT_INTEGRATION_ENABLED):
            record = self._save_pending_action_delivery_record(
                task_id,
                action_id,
                action_type,
                task=task,
                delivery_status="skipped",
                reason="wechat_integration_disabled",
            )
            self._emit_pending_action_delivery_log("ai_search_wechat_pending_action_skipped", task, record)
            return record

        binding = self.storage.get_wechat_binding_by_owner(str(getattr(task, "owner_id", "") or "").strip())
        if not binding or str(binding.status or "").strip() != "active":
            record = self._save_pending_action_delivery_record(
                task_id,
                action_id,
                action_type,
                task=task,
                delivery_status="skipped",
                reason="binding_missing",
            )
            self._emit_pending_action_delivery_log("ai_search_wechat_pending_action_skipped", task, record)
            return record

        if not bool(binding.push_ai_search_pending_action):
            record = self._save_pending_action_delivery_record(
                task_id,
                action_id,
                action_type,
                task=task,
                delivery_status="skipped",
                reason="push_pending_action_disabled",
            )
            self._emit_pending_action_delivery_log("ai_search_wechat_pending_action_skipped", task, record)
            return record

        metadata = getattr(task, "metadata", {}) if isinstance(getattr(task, "metadata", {}), dict) else {}
        delivery_meta = metadata.get("wechat_delivery") if isinstance(metadata.get("wechat_delivery"), dict) else {}
        peer_id = str(delivery_meta.get("peer_id") or getattr(binding, "delivery_peer_id", "") or "").strip()
        peer_name = str(delivery_meta.get("peer_name") or getattr(binding, "delivery_peer_name", "") or "").strip() or None
        if not peer_id:
            record = self._save_pending_action_delivery_record(
                task_id,
                action_id,
                action_type,
                task=task,
                delivery_status="skipped",
                reason="delivery_peer_missing",
            )
            self._emit_pending_action_delivery_log("ai_search_wechat_pending_action_skipped", task, record)
            return record

        payload_body = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        prompt = str(action.get("prompt") or payload_body.get("prompt") or "").strip() or None
        plan_version = int(action.get("planVersion") or action.get("plan_version") or payload_body.get("plan_version") or payload_body.get("planVersion") or 0) or None
        selected_count = int(action.get("selectedCount") or payload_body.get("selected_count") or payload_body.get("selectedCount") or 0) or None
        payload = {
            "taskId": str(getattr(task, "id", "") or "").strip(),
            "taskType": str(getattr(task, "task_type", "") or "").strip(),
            "title": str(getattr(task, "title", "") or "").strip() or str(getattr(task, "id", "") or "").strip(),
            "pendingActionId": action_id,
            "pendingActionType": action_type,
            "prompt": prompt,
            "selectedCount": selected_count,
            "accountId": getattr(binding, "bot_account_id", None),
            "peerId": peer_id,
            "peerName": peer_name,
        }
        if plan_version is not None:
            payload["planVersion"] = plan_version
        job = self.storage.create_wechat_delivery_job(
            WeChatDeliveryJob(
                delivery_job_id=f"wdj-{uuid4().hex[:12]}",
                owner_id=str(getattr(task, "owner_id", "") or "").strip(),
                binding_id=binding.binding_id,
                task_id=str(getattr(task, "id", "") or "").strip(),
                event_type=PENDING_ACTION_EVENT,
                status="pending",
                delivery_stage="queued",
                payload=payload,
                stage_details={"queued_at": utc_now_z()},
                attempt_count=0,
                max_attempts=int(getattr(settings, "WECHAT_DELIVERY_MAX_ATTEMPTS", 3) or 3),
                next_attempt_at=utc_now(),
            )
        )
        delivery_event_broker.publish()
        record = self._save_pending_action_delivery_record(
            task_id,
            action_id,
            action_type,
            task=task,
            delivery_status="queued",
            reason="",
            delivery_job_id=job.delivery_job_id,
            binding_id=binding.binding_id,
        )
        self._emit_pending_action_delivery_log("ai_search_wechat_pending_action_queued", task, record)
        return record

    def _existing_delivery(self, task: Any, terminal_status: str) -> Optional[Dict[str, Any]]:
        metadata = getattr(task, "metadata", {}) if isinstance(getattr(task, "metadata", {}), dict) else {}
        notifications = metadata.get("notifications") if isinstance(metadata.get("notifications"), dict) else {}
        wechat_meta = notifications.get("wechat") if isinstance(notifications.get("wechat"), dict) else {}
        record = wechat_meta.get(terminal_status)
        return deepcopy(record) if isinstance(record, dict) else None

    def _existing_pending_action_delivery(self, task: Any, action_id: str) -> Optional[Dict[str, Any]]:
        metadata = getattr(task, "metadata", {}) if isinstance(getattr(task, "metadata", {}), dict) else {}
        notifications = metadata.get("notifications") if isinstance(metadata.get("notifications"), dict) else {}
        wechat_meta = notifications.get("wechat") if isinstance(notifications.get("wechat"), dict) else {}
        record = wechat_meta.get("pending_action")
        if not isinstance(record, dict):
            return None
        if str(record.get("action_id") or "").strip() != str(action_id or "").strip():
            return None
        return deepcopy(record)

    def _save_delivery_record(
        self,
        task_id: str,
        terminal_status: str,
        *,
        task: Any,
        delivery_status: str,
        reason: str,
        delivery_job_id: Optional[str] = None,
        binding_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        latest_task = self.storage.get_task(task_id) or task
        metadata = deepcopy(getattr(latest_task, "metadata", {}) if isinstance(getattr(latest_task, "metadata", {}), dict) else {})
        notifications = metadata.get("notifications") if isinstance(metadata.get("notifications"), dict) else {}
        wechat_meta = notifications.get("wechat") if isinstance(notifications.get("wechat"), dict) else {}
        record = {
            "status": delivery_status,
            "reason": reason or None,
            "binding_id": binding_id or None,
            "delivery_job_id": delivery_job_id or None,
            "processed_at": utc_now_z(timespec="seconds"),
        }
        wechat_meta[terminal_status] = record
        notifications["wechat"] = wechat_meta
        metadata["notifications"] = notifications
        self.storage.update_task(task_id, metadata=metadata)
        return record

    def _emit_delivery_log(self, event_name: str, task: Any, terminal_status: str, record: Dict[str, Any]) -> None:
        is_failure = event_name == "task_wechat_failed"
        self.system_log_emitter(
            category="task_execution",
            event_name=event_name,
            level="WARNING" if is_failure else "INFO",
            owner_id=str(getattr(task, "owner_id", "") or "").strip() or None,
            task_id=str(getattr(task, "id", "") or "").strip() or None,
            task_type=str(getattr(task, "task_type", "") or "").strip() or None,
            success=not is_failure,
            message=f"任务终态微信通知{record.get('status') or 'unknown'}",
            payload={
                "terminal_status": terminal_status,
                "binding_id": record.get("binding_id"),
                "delivery_job_id": record.get("delivery_job_id"),
                "reason": record.get("reason"),
            },
        )

    def _save_pending_action_delivery_record(
        self,
        task_id: str,
        action_id: str,
        action_type: str,
        *,
        task: Any,
        delivery_status: str,
        reason: str,
        delivery_job_id: Optional[str] = None,
        binding_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        latest_task = self.storage.get_task(task_id) or task
        metadata = deepcopy(getattr(latest_task, "metadata", {}) if isinstance(getattr(latest_task, "metadata", {}), dict) else {})
        notifications = metadata.get("notifications") if isinstance(metadata.get("notifications"), dict) else {}
        wechat_meta = notifications.get("wechat") if isinstance(notifications.get("wechat"), dict) else {}
        record = {
            "action_id": action_id,
            "action_type": action_type,
            "status": delivery_status,
            "reason": reason or None,
            "binding_id": binding_id or None,
            "delivery_job_id": delivery_job_id or None,
            "processed_at": utc_now_z(timespec="seconds"),
        }
        wechat_meta["pending_action"] = record
        notifications["wechat"] = wechat_meta
        metadata["notifications"] = notifications
        self.storage.update_task(task_id, metadata=metadata)
        return record

    def _emit_pending_action_delivery_log(self, event_name: str, task: Any, record: Dict[str, Any]) -> None:
        self.system_log_emitter(
            category="task_execution",
            event_name=event_name,
            owner_id=str(getattr(task, "owner_id", "") or "").strip() or None,
            task_id=str(getattr(task, "id", "") or "").strip() or None,
            task_type=str(getattr(task, "task_type", "") or "").strip() or None,
            success=record.get("status") == "queued",
            message=f"AI 检索待确认微信提醒{record.get('status') or 'unknown'}",
            payload={
                "action_id": record.get("action_id"),
                "action_type": record.get("action_type"),
                "binding_id": record.get("binding_id"),
                "delivery_job_id": record.get("delivery_job_id"),
                "reason": record.get("reason"),
            },
        )


def build_task_wechat_notification_service(
    *,
    storage: Any,
    system_log_emitter: Optional[SystemLogEmitter] = None,
) -> TaskWeChatNotificationService:
    return TaskWeChatNotificationService(
        storage=storage,
        system_log_emitter=system_log_emitter,
    )
