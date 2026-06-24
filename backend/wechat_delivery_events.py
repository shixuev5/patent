from __future__ import annotations

from threading import Condition


class WeChatEventBroker:
    def __init__(self) -> None:
        self._condition = Condition()
        self._cursor = 0

    def publish(self) -> int:
        with self._condition:
            self._cursor += 1
            self._condition.notify_all()
            return self._cursor

    def current_cursor(self) -> int:
        with self._condition:
            return self._cursor

    def wait_for_event(self, cursor: int, timeout_seconds: float) -> int:
        normalized_cursor = max(0, int(cursor or 0))
        normalized_timeout = max(0.0, float(timeout_seconds or 0.0))
        with self._condition:
            if self._cursor > normalized_cursor:
                return self._cursor
            self._condition.wait(timeout=normalized_timeout)
            return self._cursor


class WeChatDeliveryEventBroker(WeChatEventBroker):
    pass


class WeChatRuntimeEventBroker(WeChatEventBroker):
    pass


delivery_event_broker = WeChatDeliveryEventBroker()
runtime_event_broker = WeChatRuntimeEventBroker()
