"""
Token pricing helpers.
"""

from __future__ import annotations

import json
import os
import threading
from decimal import Decimal, InvalidOperation
from typing import Dict, Tuple

from loguru import logger


TOKEN_PRICING_ENV_KEY = "TOKEN_PRICING_PER_MILLION_JSON"
TOKEN_PRICING_CURRENCY = "CNY"
_CACHE_LOCK = threading.Lock()
_CACHE_RAW = None
_CACHE_PRICING: Dict[str, Tuple[Decimal, Decimal]] = {}


def _to_decimal(value) -> Decimal:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")
    if number < 0:
        return Decimal("0")
    return number


def _parse_pricing(text: str) -> Dict[str, Tuple[Decimal, Decimal]]:
    if not text.strip():
        return {}

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"{TOKEN_PRICING_ENV_KEY} is not valid JSON; fallback to empty pricing table.")
        return {}

    if not isinstance(parsed, dict):
        logger.warning(f"{TOKEN_PRICING_ENV_KEY} should be a JSON object; fallback to empty pricing table.")
        return {}

    pricing: Dict[str, Tuple[Decimal, Decimal]] = {}
    for model_name, price_conf in parsed.items():
        key = str(model_name or "").strip()
        if not key:
            continue
        if not isinstance(price_conf, dict):
            continue
        prompt_price = _to_decimal(price_conf.get("prompt", 0))
        completion_price = _to_decimal(price_conf.get("completion", 0))
        pricing[key] = (prompt_price, completion_price)
        pricing[key.lower()] = (prompt_price, completion_price)
    return pricing


def get_pricing_table() -> Dict[str, Tuple[Decimal, Decimal]]:
    global _CACHE_RAW, _CACHE_PRICING

    raw = os.getenv(TOKEN_PRICING_ENV_KEY, "")
    with _CACHE_LOCK:
        if raw == _CACHE_RAW:
            return _CACHE_PRICING
        _CACHE_RAW = raw
        _CACHE_PRICING = _parse_pricing(raw)
        return _CACHE_PRICING


def get_model_price(model: str) -> Tuple[Decimal, Decimal, bool]:
    key = str(model or "").strip()
    if not key:
        return Decimal("0"), Decimal("0"), True

    table = get_pricing_table()
    if key in table:
        prompt_price, completion_price = table[key]
        return prompt_price, completion_price, False
    lowered = key.lower()
    if lowered in table:
        prompt_price, completion_price = table[lowered]
        return prompt_price, completion_price, False
    return Decimal("0"), Decimal("0"), True


def estimate_cost_cny(model: str, prompt_tokens: int, completion_tokens: int) -> Tuple[float, bool]:
    prompt_price, completion_price, missing = get_model_price(model)
    prompt_units = Decimal(max(0, int(prompt_tokens or 0))) / Decimal("1000000")
    completion_units = Decimal(max(0, int(completion_tokens or 0))) / Decimal("1000000")
    cost = (prompt_units * prompt_price) + (completion_units * completion_price)
    return float(cost), missing
