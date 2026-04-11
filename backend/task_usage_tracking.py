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

    def has_usage(self) -> bool:
        return any(
            [
                self.prompt_tokens > 0,
                self.completion_tokens > 0,
                self.total_tokens > 0,
                self.reasoning_tokens > 0,
                self.llm_call_count > 0,
                bool(self.model_breakdown),
                bool(self.first_usage_at),
                bool(self.last_usage_at),
            ]
        )

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


def _merge_timestamp_min(*values: Any) -> Optional[str]:
    items = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.lower() in {"none", "null"}:
            continue
        items.append(text)
    return min(items) if items else None


def _merge_timestamp_max(*values: Any) -> Optional[str]:
    items = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.lower() in {"none", "null"}:
            continue
        items.append(text)
    return max(items) if items else None


def _merge_model_breakdown(
    existing: Optional[Dict[str, Any]],
    incoming: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    merged: Dict[str, Dict[str, Any]] = {}

    def _apply(source: Optional[Dict[str, Any]]) -> None:
        if not isinstance(source, dict):
            return
        for model_name, raw_item in source.items():
            name = str(model_name or "").strip()
            if not name:
                continue
            item = raw_item if isinstance(raw_item, dict) else {}
            target = merged.setdefault(
                name,
                {
                    "model": name,
                    "promptTokens": 0,
                    "completionTokens": 0,
                    "totalTokens": 0,
                    "reasoningTokens": 0,
                    "llmCallCount": 0,
                    "estimatedCostCny": 0.0,
                    "priceMissing": False,
                },
            )
            target["promptTokens"] += int(item.get("promptTokens") or 0)
            target["completionTokens"] += int(item.get("completionTokens") or 0)
            target["totalTokens"] += int(item.get("totalTokens") or 0)
            target["reasoningTokens"] += int(item.get("reasoningTokens") or 0)
            target["llmCallCount"] += int(item.get("llmCallCount") or 0)
            target["estimatedCostCny"] += float(item.get("estimatedCostCny") or 0)
            target["priceMissing"] = bool(target["priceMissing"]) or bool(item.get("priceMissing"))

    _apply(existing)
    _apply(incoming)
    return merged


def merge_task_usage_records(
    existing: Optional[Dict[str, Any]],
    incoming: Dict[str, Any],
) -> Dict[str, Any]:
    if not existing:
        return dict(incoming)

    return {
        "task_id": str(incoming.get("task_id") or existing.get("task_id") or "").strip(),
        "owner_id": str(incoming.get("owner_id") or existing.get("owner_id") or "").strip(),
        "task_type": str(incoming.get("task_type") or existing.get("task_type") or "").strip(),
        "task_status": str(incoming.get("task_status") or existing.get("task_status") or "").strip(),
        "prompt_tokens": int(existing.get("prompt_tokens") or 0) + int(incoming.get("prompt_tokens") or 0),
        "completion_tokens": int(existing.get("completion_tokens") or 0) + int(incoming.get("completion_tokens") or 0),
        "total_tokens": int(existing.get("total_tokens") or 0) + int(incoming.get("total_tokens") or 0),
        "reasoning_tokens": int(existing.get("reasoning_tokens") or 0) + int(incoming.get("reasoning_tokens") or 0),
        "llm_call_count": int(existing.get("llm_call_count") or 0) + int(incoming.get("llm_call_count") or 0),
        "estimated_cost_cny": float(existing.get("estimated_cost_cny") or 0) + float(incoming.get("estimated_cost_cny") or 0),
        "price_missing": bool(existing.get("price_missing")) or bool(incoming.get("price_missing")),
        "model_breakdown_json": _merge_model_breakdown(
            existing.get("model_breakdown_json"),
            incoming.get("model_breakdown_json"),
        ),
        "first_usage_at": _merge_timestamp_min(existing.get("first_usage_at"), incoming.get("first_usage_at")),
        "last_usage_at": _merge_timestamp_max(existing.get("last_usage_at"), incoming.get("last_usage_at")),
        "currency": str(incoming.get("currency") or existing.get("currency") or TOKEN_PRICING_CURRENCY).strip() or TOKEN_PRICING_CURRENCY,
        "created_at": str(existing.get("created_at") or incoming.get("created_at") or utc_now_z(timespec="seconds")).strip(),
        "updated_at": str(incoming.get("updated_at") or existing.get("updated_at") or utc_now_z(timespec="seconds")).strip(),
    }


def persist_task_usage(storage, collector: Optional[TaskUsageCollector], *, merge: bool = False) -> bool:
    if collector is None:
        return False
    if not collector.task_id or not collector.owner_id:
        return False
    if not hasattr(storage, "upsert_task_llm_usage"):
        return False
    existing_record = None
    if merge and hasattr(storage, "get_task_llm_usage"):
        try:
            existing_record = storage.get_task_llm_usage(collector.task_id)
        except Exception as exc:
            logger.warning(f"读取任务 LLM 用量失败：{exc}")
            existing_record = None
    has_new_usage = collector.has_usage()
    if merge and not has_new_usage and not existing_record:
        return False
    try:
        payload = collector.to_record()
        if merge and not has_new_usage:
            payload["first_usage_at"] = None
            payload["last_usage_at"] = None
        if merge:
            payload = merge_task_usage_records(existing_record, payload)
        return bool(storage.upsert_task_llm_usage(payload))
    except Exception as exc:
        logger.warning(f"持久化任务 LLM 用量失败：{exc}")
        return False
