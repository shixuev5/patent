"""
Task-level LLM usage collector and persistence helpers.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Iterator, Optional

from loguru import logger

from backend.time_utils import utc_now_z
from backend.token_pricing import TOKEN_PRICING_CURRENCY, estimate_cost_cny


@dataclass
class ModelUsageAggregate:
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    llm_call_count: int = 0
    estimated_cost_cny: Decimal = Decimal("0")
    price_missing: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "totalTokens": self.total_tokens,
            "reasoningTokens": self.reasoning_tokens,
            "llmCallCount": self.llm_call_count,
            "estimatedCostCny": float(self.estimated_cost_cny),
            "priceMissing": self.price_missing,
        }


@dataclass
class TaskUsageCollector:
    task_id: str
    owner_id: str
    task_type: str
    task_status: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    llm_call_count: int = 0
    estimated_cost_cny: Decimal = Decimal("0")
    price_missing: bool = False
    model_breakdown: Dict[str, ModelUsageAggregate] = field(default_factory=dict)
    first_usage_at: Optional[str] = None
    last_usage_at: Optional[str] = None
    created_at: str = field(default_factory=lambda: utc_now_z(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: utc_now_z(timespec="seconds"))

    def mark_status(self, status: Optional[str]):
        text = str(status or "").strip().lower()
        self.task_status = text or None

    def record_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        reasoning_tokens: int,
    ):
        model_name = str(model or "").strip() or "unknown"
        prompt = max(0, int(prompt_tokens or 0))
        completion = max(0, int(completion_tokens or 0))
        total = max(0, int(total_tokens or 0))
        if total <= 0:
            total = prompt + completion
        reasoning = max(0, int(reasoning_tokens or 0))

        estimated_cost_float, missing = estimate_cost_cny(model_name, prompt, completion)
        estimated_cost = Decimal(str(estimated_cost_float))

        model_item = self.model_breakdown.get(model_name)
        if model_item is None:
            model_item = ModelUsageAggregate(model=model_name)
            self.model_breakdown[model_name] = model_item

        model_item.prompt_tokens += prompt
        model_item.completion_tokens += completion
        model_item.total_tokens += total
        model_item.reasoning_tokens += reasoning
        model_item.llm_call_count += 1
        model_item.estimated_cost_cny += estimated_cost
        model_item.price_missing = model_item.price_missing or missing

        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        self.reasoning_tokens += reasoning
        self.llm_call_count += 1
        self.estimated_cost_cny += estimated_cost
        self.price_missing = self.price_missing or missing

        now_iso = utc_now_z(timespec="seconds")
        if not self.first_usage_at:
            self.first_usage_at = now_iso
        self.last_usage_at = now_iso
        self.updated_at = now_iso

    def to_record(self) -> Dict[str, Any]:
        if not self.last_usage_at:
            self.last_usage_at = self.updated_at
        return {
            "task_id": self.task_id,
            "owner_id": self.owner_id,
            "task_type": self.task_type,
            "task_status": self.task_status or "",
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "llm_call_count": self.llm_call_count,
            "estimated_cost_cny": float(self.estimated_cost_cny),
            "price_missing": bool(self.price_missing),
            "model_breakdown_json": {name: item.to_dict() for name, item in self.model_breakdown.items()},
            "first_usage_at": self.first_usage_at,
            "last_usage_at": self.last_usage_at,
            "currency": TOKEN_PRICING_CURRENCY,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_CURRENT_COLLECTOR: ContextVar[Optional[TaskUsageCollector]] = ContextVar(
    "CURRENT_TASK_USAGE_COLLECTOR",
    default=None,
)


def create_task_usage_collector(task_id: str, owner_id: str, task_type: str) -> TaskUsageCollector:
    return TaskUsageCollector(
        task_id=str(task_id or "").strip(),
        owner_id=str(owner_id or "").strip(),
        task_type=str(task_type or "").strip(),
    )


@contextmanager
def task_usage_collection(collector: TaskUsageCollector) -> Iterator[None]:
    token = _CURRENT_COLLECTOR.set(collector)
    try:
        yield
    finally:
        _CURRENT_COLLECTOR.reset(token)


def record_llm_usage(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    reasoning_tokens: int,
):
    collector = _CURRENT_COLLECTOR.get()
    if collector is None:
        return
    collector.record_usage(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def get_current_task_usage_context() -> Dict[str, Optional[str]]:
    collector = _CURRENT_COLLECTOR.get()
    if collector is None:
        return {"task_id": None, "owner_id": None, "task_type": None}
    return {
        "task_id": collector.task_id or None,
        "owner_id": collector.owner_id or None,
        "task_type": collector.task_type or None,
    }


def persist_task_usage(storage, collector: Optional[TaskUsageCollector]) -> bool:
    if collector is None:
        return False
    if not collector.task_id or not collector.owner_id:
        return False
    if not hasattr(storage, "upsert_task_llm_usage"):
        return False
    try:
        return bool(storage.upsert_task_llm_usage(collector.to_record()))
    except Exception as exc:
        logger.warning(f"持久化任务 LLM 用量失败：{exc}")
        return False
