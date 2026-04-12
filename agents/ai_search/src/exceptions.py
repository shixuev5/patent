"""AI search runtime exceptions."""

from __future__ import annotations

from typing import Any, Dict, List


class ExecutionQueueTakeoverRequested(RuntimeError):
    def __init__(self, *, queued_messages: List[Dict[str, Any]], takeover_prompt: str):
        super().__init__("execution_queue_takeover_requested")
        self.queued_messages = queued_messages
        self.takeover_prompt = str(takeover_prompt or "").strip()
