"""
Token pricing sync and cost estimation helpers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import requests
from bs4 import BeautifulSoup
from loguru import logger

from backend.system_logs import suppress_outbound_request_logging
from backend.time_utils import parse_storage_ts, utc_now_z


TOKEN_PRICING_CURRENCY = "CNY"
TOKEN_PRICING_REGION = "cn"
TOKEN_PRICING_BILLING_MODE = "payg"
TOKEN_PRICING_SYNC_TTL_SECONDS = max(3600, int(os.getenv("TOKEN_PRICING_SYNC_TTL_SECONDS", "604800") or 604800))
TOKEN_PRICING_REQUEST_TIMEOUT_SECONDS = max(
    5,
    int(os.getenv("TOKEN_PRICING_REQUEST_TIMEOUT_SECONDS", "30") or 30),
)
TOKEN_PRICING_SOURCE_URLS: Tuple[str, ...] = (
    "https://help.aliyun.com/zh/model-studio/model-pricing",
    "https://help.aliyun.com/zh/model-studio/getting-started/models",
)
_HELP_PAGE_PROPS_PATTERN = re.compile(r"window\.__ICE_PAGE_PROPS__=(.*?);\n", re.S)
_ASCII_MODEL_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9._:-]*")
_NUMERIC_PATTERN = re.compile(r"(\d+(?:\.\d+)?)")
_TOKEN_BOUND_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*([KkMm]?)")
_NON_DIGIT_WHITESPACE_PATTERN = re.compile(r"\s+")

_CACHE_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()
_CACHE_LOADED = False
_CACHE_ROWS: List[Dict[str, Any]] = []
_CACHE_STATE: Dict[str, Any] = {}
_REFRESH_THREAD: Optional[threading.Thread] = None
_STORAGE_PROVIDER: Optional[Callable[[], Any]] = None


def _normalize_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\xa0", " ").replace("\u200b", " ")
    text = text.replace("help-letter-space", " ")
    text = _NON_DIGIT_WHITESPACE_PATTERN.sub(" ", text)
    return text.strip()


def _normalize_model_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _to_decimal(value: Any) -> Decimal:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    if number < 0:
        return Decimal("0")
    return number


def _extract_first_number(value: Any) -> Optional[Decimal]:
    match = _NUMERIC_PATTERN.search(_normalize_text(value))
    if not match:
        return None
    return _to_decimal(match.group(1))


def _parse_price_cny(value: Any) -> Decimal:
    number = _extract_first_number(value)
    return number if number is not None else Decimal("0")


def _parse_token_count(value: str, unit: str) -> Optional[int]:
    number = _to_decimal(value)
    multiplier = Decimal("1")
    if unit.lower() == "k":
        multiplier = Decimal("1024")
    elif unit.lower() == "m":
        multiplier = Decimal("1048576")
    total = int(number * multiplier)
    return max(0, total)


def _parse_token_range(value: Any) -> Tuple[int, Optional[int]]:
    text = _normalize_text(value).replace(" ", "")
    if not text:
        return 1, None

    lower_match = re.search(r"(\d+(?:\.\d+)?)\s*([KkMm]?)<Token", text)
    upper_match = re.search(r"≤(\d+(?:\.\d+)?)\s*([KkMm]?)", text)

    min_tokens = 1
    max_tokens: Optional[int] = None

    if lower_match:
        lower = _parse_token_count(lower_match.group(1), lower_match.group(2))
        if lower is not None:
            min_tokens = max(1, lower + 1)
    if upper_match:
        upper = _parse_token_count(upper_match.group(1), upper_match.group(2))
        if upper is not None:
            max_tokens = max(1, upper)

    if not lower_match and not upper_match:
        matches = _TOKEN_BOUND_PATTERN.findall(text)
        if len(matches) == 1:
            upper = _parse_token_count(matches[0][0], matches[0][1])
            if upper is not None:
                max_tokens = max(1, upper)
        elif len(matches) >= 2:
            lower = _parse_token_count(matches[0][0], matches[0][1])
            upper = _parse_token_count(matches[1][0], matches[1][1])
            if lower is not None:
                min_tokens = max(1, lower + 1)
            if upper is not None:
                max_tokens = max(1, upper)

    if max_tokens is not None and max_tokens < min_tokens:
        max_tokens = min_tokens
    return min_tokens, max_tokens


def _extract_model_name(value: Any) -> str:
    text = _normalize_text(value)
    match = _ASCII_MODEL_PATTERN.search(text)
    return match.group(0).strip() if match else ""


def _extract_doc_content_html(page_html: str) -> str:
    match = _HELP_PAGE_PROPS_PATTERN.search(str(page_html or ""))
    if not match:
        raise ValueError("官方价格页中未找到 __ICE_PAGE_PROPS__ 数据。")
    payload = json.loads(match.group(1))
    content = (
        payload.get("docDetailData", {})
        .get("storeData", {})
        .get("data", {})
        .get("content")
    )
    text = str(content or "").strip()
    if not text:
        raise ValueError("官方价格页内容为空。")
    return text


def _html_table_to_grid(table) -> List[List[str]]:
    grid: List[List[str]] = []
    rowspan_cells: Dict[int, List[Any]] = {}

    for tr in table.find_all("tr"):
        row: List[str] = []
        column = 0

        def fill_rowspans() -> None:
            nonlocal column
            while column in rowspan_cells:
                remaining, text = rowspan_cells[column]
                row.append(text)
                if remaining <= 1:
                    rowspan_cells.pop(column, None)
                else:
                    rowspan_cells[column][0] = remaining - 1
                column += 1

        fill_rowspans()
        for cell in tr.find_all(["th", "td"], recursive=False):
            fill_rowspans()
            text = _normalize_text(cell.get_text(" ", strip=True))
            rowspan = max(1, int(cell.get("rowspan", 1) or 1))
            colspan = max(1, int(cell.get("colspan", 1) or 1))
            for offset in range(colspan):
                row.append(text)
                if rowspan > 1:
                    rowspan_cells[column + offset] = [rowspan - 1, text]
            column += colspan
        fill_rowspans()
        if any(cell for cell in row):
            grid.append(row)

    width = max((len(row) for row in grid), default=0)
    for row in grid:
        if len(row) < width:
            row.extend([""] * (width - len(row)))
    return grid


def _resolve_header_indexes(headers: Sequence[str]) -> Dict[str, Optional[int]]:
    normalized = [_normalize_text(item) for item in headers]

    model_idx = next((idx for idx, item in enumerate(normalized) if "模型名称" in item), None)
    token_range_idx = next(
        (
            idx
            for idx, item in enumerate(normalized)
            if "单次请求" in item and "输入" in item and "token" in item.lower()
        ),
        None,
    )
    prompt_idx = next((idx for idx, item in enumerate(normalized) if "输入单价" in item), None)
    output_candidates = [idx for idx, item in enumerate(normalized) if "输出单价" in item]
    if output_candidates:
        non_thinking = [idx for idx in output_candidates if "非思考" in normalized[idx]]
        without_thinking = [
            idx
            for idx in output_candidates
            if "思考模式" not in normalized[idx] and "思维链" not in normalized[idx]
        ]
        completion_idx = (non_thinking or without_thinking or output_candidates)[0]
    else:
        completion_idx = None
    return {
        "model_idx": model_idx,
        "token_range_idx": token_range_idx,
        "prompt_idx": prompt_idx,
        "completion_idx": completion_idx,
    }


def _parse_pricing_entries_from_table(
    table,
    *,
    source_url: str,
    source_hash: str,
    fetched_at: str,
    expires_at: str,
) -> List[Dict[str, Any]]:
    grid = _html_table_to_grid(table)
    if len(grid) < 2:
        return []

    indexes = _resolve_header_indexes(grid[0])
    model_idx = indexes["model_idx"]
    prompt_idx = indexes["prompt_idx"]
    if model_idx is None or prompt_idx is None:
        return []

    entries: List[Dict[str, Any]] = []
    for row in grid[1:]:
        if len(row) <= model_idx:
            continue
        model_name = _extract_model_name(row[model_idx])
        if not model_name:
            continue

        prompt_price = _parse_price_cny(row[prompt_idx])
        completion_idx = indexes["completion_idx"]
        completion_price = _parse_price_cny(row[completion_idx]) if completion_idx is not None else Decimal("0")
        if prompt_price <= 0 and completion_price <= 0:
            continue

        token_range_idx = indexes["token_range_idx"]
        if token_range_idx is not None and token_range_idx < len(row):
            min_tokens, max_tokens = _parse_token_range(row[token_range_idx])
        else:
            min_tokens, max_tokens = 1, None

        entries.append(
            {
                "model": model_name,
                "region": TOKEN_PRICING_REGION,
                "billing_mode": TOKEN_PRICING_BILLING_MODE,
                "input_tier_min_tokens": int(min_tokens),
                "input_tier_max_tokens": int(max_tokens) if max_tokens is not None else None,
                "prompt_price_per_million_cny": float(prompt_price),
                "completion_price_per_million_cny": float(completion_price),
                "source_url": source_url,
                "source_hash": source_hash,
                "fetched_at": fetched_at,
                "expires_at": expires_at,
                "parse_status": "ok",
                "parse_error": "",
                "updated_at": fetched_at,
            }
        )
    return entries


def _parse_pricing_entries_from_page(page_html: str, *, source_url: str, fetched_at: str, expires_at: str) -> List[Dict[str, Any]]:
    content_html = _extract_doc_content_html(page_html)
    source_hash = hashlib.sha1(content_html.encode("utf-8")).hexdigest()
    soup = BeautifulSoup(content_html, "html.parser")
    entries: List[Dict[str, Any]] = []
    dedupe: set[Tuple[str, int, Optional[int]]] = set()

    for region_heading in soup.find_all("h2"):
        if _normalize_text(region_heading.get_text(" ", strip=True)) != "中国内地":
            continue
        region_section = region_heading.find_parent("section")
        if region_section is None:
            continue
        table = region_section.find("table")
        if table is None:
            continue
        for item in _parse_pricing_entries_from_table(
            table,
            source_url=source_url,
            source_hash=source_hash,
            fetched_at=fetched_at,
            expires_at=expires_at,
        ):
            key = (
                _normalize_model_key(item.get("model")),
                int(item.get("input_tier_min_tokens") or 1),
                item.get("input_tier_max_tokens"),
            )
            if key in dedupe:
                continue
            dedupe.add(key)
            entries.append(item)
    return entries


def _get_storage():
    provider = _STORAGE_PROVIDER
    if provider is not None:
        return provider()
    from backend.storage import get_pipeline_manager

    return get_pipeline_manager().storage


def configure_pricing_storage(provider: Optional[Callable[[], Any]]) -> None:
    global _STORAGE_PROVIDER
    _STORAGE_PROVIDER = provider


def reset_pricing_runtime_cache() -> None:
    global _CACHE_LOADED, _CACHE_ROWS, _CACHE_STATE, _REFRESH_THREAD
    with _CACHE_LOCK:
        _CACHE_LOADED = False
        _CACHE_ROWS = []
        _CACHE_STATE = {}
        _REFRESH_THREAD = None


def _normalize_pricing_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "model": str(row.get("model") or "").strip(),
        "region": str(row.get("region") or TOKEN_PRICING_REGION).strip() or TOKEN_PRICING_REGION,
        "billing_mode": str(row.get("billing_mode") or TOKEN_PRICING_BILLING_MODE).strip() or TOKEN_PRICING_BILLING_MODE,
        "input_tier_min_tokens": int(row.get("input_tier_min_tokens") or 1),
        "input_tier_max_tokens": int(row["input_tier_max_tokens"]) if row.get("input_tier_max_tokens") is not None else None,
        "prompt_price_per_million_cny": float(row.get("prompt_price_per_million_cny") or 0),
        "completion_price_per_million_cny": float(row.get("completion_price_per_million_cny") or 0),
        "source_url": str(row.get("source_url") or "").strip(),
        "source_hash": str(row.get("source_hash") or "").strip(),
        "fetched_at": row.get("fetched_at"),
        "expires_at": row.get("expires_at"),
        "parse_status": str(row.get("parse_status") or "ok").strip() or "ok",
        "parse_error": str(row.get("parse_error") or "").strip(),
        "updated_at": row.get("updated_at"),
    }


def _normalize_sync_state(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    item = row or {}
    return {
        "region": str(item.get("region") or TOKEN_PRICING_REGION).strip() or TOKEN_PRICING_REGION,
        "billing_mode": str(item.get("billing_mode") or TOKEN_PRICING_BILLING_MODE).strip() or TOKEN_PRICING_BILLING_MODE,
        "cache_entry_count": int(item.get("cache_entry_count") or 0),
        "last_success_at": item.get("last_success_at"),
        "last_attempt_at": item.get("last_attempt_at"),
        "expires_at": item.get("expires_at"),
        "source_url": str(item.get("source_url") or "").strip(),
        "source_hash": str(item.get("source_hash") or "").strip(),
        "parse_status": str(item.get("parse_status") or "unknown").strip() or "unknown",
        "last_error": str(item.get("last_error") or "").strip(),
        "updated_at": item.get("updated_at"),
    }


def _load_cache_from_storage() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    storage = _get_storage()
    rows: List[Dict[str, Any]] = []
    state: Dict[str, Any] = {}
    if hasattr(storage, "list_llm_pricing_entries"):
        rows = [
            _normalize_pricing_row(item)
            for item in storage.list_llm_pricing_entries(
                region=TOKEN_PRICING_REGION,
                billing_mode=TOKEN_PRICING_BILLING_MODE,
            )
        ]
    if hasattr(storage, "get_llm_pricing_sync_state"):
        state = _normalize_sync_state(
            storage.get_llm_pricing_sync_state(
                region=TOKEN_PRICING_REGION,
                billing_mode=TOKEN_PRICING_BILLING_MODE,
            )
        )
    with _CACHE_LOCK:
        global _CACHE_LOADED, _CACHE_ROWS, _CACHE_STATE
        _CACHE_LOADED = True
        _CACHE_ROWS = rows
        _CACHE_STATE = state
    return rows, state


def _get_cache_snapshot() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    with _CACHE_LOCK:
        if _CACHE_LOADED:
            return list(_CACHE_ROWS), dict(_CACHE_STATE)
    return _load_cache_from_storage()


def _update_cache(rows: Sequence[Dict[str, Any]], state: Dict[str, Any]) -> None:
    with _CACHE_LOCK:
        global _CACHE_LOADED, _CACHE_ROWS, _CACHE_STATE
        _CACHE_LOADED = True
        _CACHE_ROWS = [_normalize_pricing_row(item) for item in rows]
        _CACHE_STATE = _normalize_sync_state(state)


def _is_expired(state: Dict[str, Any]) -> bool:
    parsed = parse_storage_ts(state.get("expires_at"), naive_strategy="utc")
    if parsed is None:
        return True
    now = parse_storage_ts(utc_now_z(), naive_strategy="utc")
    assert now is not None
    return parsed <= now


def _persist_refresh_failure(storage: Any, *, state: Dict[str, Any], attempt_at: str, error_message: str) -> Dict[str, Any]:
    next_state = {
        **state,
        "region": TOKEN_PRICING_REGION,
        "billing_mode": TOKEN_PRICING_BILLING_MODE,
        "cache_entry_count": int(state.get("cache_entry_count") or 0),
        "last_attempt_at": attempt_at,
        "parse_status": "error",
        "last_error": error_message,
        "updated_at": attempt_at,
    }
    if hasattr(storage, "upsert_llm_pricing_sync_state"):
        storage.upsert_llm_pricing_sync_state(next_state)
    return _normalize_sync_state(next_state)


def refresh_pricing_cache(force: bool = False) -> Dict[str, Any]:
    with _REFRESH_LOCK:
        storage = _get_storage()
        rows, state = _load_cache_from_storage()
        has_rows = bool(rows)
        expired = _is_expired(state)
        if not force and has_rows and not expired:
            return {
                "success": True,
                "refreshed": False,
                "entryCount": len(rows),
                "lastSuccessAt": state.get("last_success_at"),
                "lastAttemptAt": state.get("last_attempt_at"),
                "expiresAt": state.get("expires_at"),
                "sourceUrl": state.get("source_url"),
                "parseStatus": state.get("parse_status") or "ok",
                "errorMessage": state.get("last_error") or None,
            }

        attempt_at = utc_now_z(timespec="seconds")
        fetch_errors: List[str] = []
        selected_url = ""
        selected_entries: List[Dict[str, Any]] = []
        selected_hash = ""

        try:
            for url in TOKEN_PRICING_SOURCE_URLS:
                try:
                    with suppress_outbound_request_logging():
                        response = requests.get(url, timeout=TOKEN_PRICING_REQUEST_TIMEOUT_SECONDS)
                    response.raise_for_status()
                    fetched_at = utc_now_z(timespec="seconds")
                    expires_at = utc_now_z(timespec="seconds")
                    entries = _parse_pricing_entries_from_page(
                        response.text,
                        source_url=url,
                        fetched_at=fetched_at,
                        expires_at=expires_at,
                    )
                except Exception as exc:
                    fetch_errors.append(f"{url}: {exc}")
                    continue
                if entries:
                    selected_url = url
                    selected_entries = entries
                    selected_hash = str(entries[0].get("source_hash") or "").strip()
                    break
                fetch_errors.append(f"{url}: 未解析出任何价格条目")

            if not selected_entries:
                raise ValueError("; ".join(fetch_errors) or "未解析出任何价格条目")

            fetched_at = utc_now_z(timespec="seconds")
            fetched_at_dt = parse_storage_ts(fetched_at, naive_strategy="utc")
            assert fetched_at_dt is not None
            expires_at_text = fetched_at_dt + timedelta(seconds=TOKEN_PRICING_SYNC_TTL_SECONDS)
            expires_at_text = expires_at_text.isoformat(timespec="seconds").replace("+00:00", "Z")
            normalized_entries = []
            for item in selected_entries:
                normalized_entries.append(
                    {
                        **item,
                        "fetched_at": fetched_at,
                        "expires_at": expires_at_text,
                        "parse_status": "ok",
                        "parse_error": "",
                        "updated_at": fetched_at,
                        "source_url": selected_url,
                        "source_hash": selected_hash,
                    }
                )

            if hasattr(storage, "replace_llm_pricing_entries"):
                storage.replace_llm_pricing_entries(
                    normalized_entries,
                    region=TOKEN_PRICING_REGION,
                    billing_mode=TOKEN_PRICING_BILLING_MODE,
                )

            next_state = {
                "region": TOKEN_PRICING_REGION,
                "billing_mode": TOKEN_PRICING_BILLING_MODE,
                "cache_entry_count": len(normalized_entries),
                "last_success_at": fetched_at,
                "last_attempt_at": fetched_at,
                "expires_at": expires_at_text,
                "source_url": selected_url,
                "source_hash": selected_hash,
                "parse_status": "ok",
                "last_error": "",
                "updated_at": fetched_at,
            }
            if hasattr(storage, "upsert_llm_pricing_sync_state"):
                storage.upsert_llm_pricing_sync_state(next_state)
            _update_cache(normalized_entries, next_state)
            return {
                "success": True,
                "refreshed": True,
                "entryCount": len(normalized_entries),
                "lastSuccessAt": fetched_at,
                "lastAttemptAt": fetched_at,
                "expiresAt": expires_at_text,
                "sourceUrl": selected_url,
                "parseStatus": "ok",
                "errorMessage": None,
            }
        except Exception as exc:
            logger.warning("价格同步失败：{}", exc)
            next_state = _persist_refresh_failure(
                storage,
                state=state,
                attempt_at=attempt_at,
                error_message=str(exc),
            )
            _update_cache(rows, next_state)
            return {
                "success": False,
                "refreshed": False,
                "entryCount": len(rows),
                "lastSuccessAt": next_state.get("last_success_at"),
                "lastAttemptAt": next_state.get("last_attempt_at"),
                "expiresAt": next_state.get("expires_at"),
                "sourceUrl": next_state.get("source_url"),
                "parseStatus": next_state.get("parse_status"),
                "errorMessage": next_state.get("last_error") or str(exc),
            }


def _background_refresh_worker(force: bool) -> None:
    try:
        refresh_pricing_cache(force=force)
    except Exception as exc:
        logger.warning("后台价格同步失败：{}", exc)
    finally:
        global _REFRESH_THREAD
        with _CACHE_LOCK:
            _REFRESH_THREAD = None


def schedule_background_refresh(force: bool = False) -> bool:
    global _REFRESH_THREAD
    with _CACHE_LOCK:
        if _REFRESH_THREAD is not None and _REFRESH_THREAD.is_alive():
            return False
        thread = threading.Thread(
            target=_background_refresh_worker,
            args=(force,),
            daemon=True,
            name="token-pricing-sync",
        )
        _REFRESH_THREAD = thread
        thread.start()
        return True


def get_pricing_status() -> Dict[str, Any]:
    rows, state = _get_cache_snapshot()
    cache_entry_count = int(state.get("cache_entry_count") or len(rows))
    has_usable_cache = bool(rows)
    return {
        "region": TOKEN_PRICING_REGION,
        "billingMode": TOKEN_PRICING_BILLING_MODE,
        "currency": TOKEN_PRICING_CURRENCY,
        "entryCount": cache_entry_count,
        "lastSuccessAt": state.get("last_success_at"),
        "lastAttemptAt": state.get("last_attempt_at"),
        "expiresAt": state.get("expires_at"),
        "sourceUrl": state.get("source_url") or None,
        "parseStatus": state.get("parse_status") or ("ok" if has_usable_cache else "unknown"),
        "errorMessage": state.get("last_error") or None,
        "hasUsableCache": has_usable_cache,
        "isExpired": _is_expired(state) if state else True,
    }


def _select_price_row(model: str, prompt_tokens: int, rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    normalized_model = _normalize_model_key(model)
    candidates = [row for row in rows if _normalize_model_key(row.get("model")) == normalized_model]
    if not candidates:
        return None

    prompt_total = max(0, int(prompt_tokens or 0))
    ordered = sorted(
        candidates,
        key=lambda item: (
            int(item.get("input_tier_min_tokens") or 1),
            int(item["input_tier_max_tokens"]) if item.get("input_tier_max_tokens") is not None else 10**18,
        ),
    )
    for row in ordered:
        min_tokens = max(1, int(row.get("input_tier_min_tokens") or 1))
        max_tokens = row.get("input_tier_max_tokens")
        upper = int(max_tokens) if max_tokens is not None else None
        target_prompt = prompt_total if prompt_total > 0 else min_tokens
        if target_prompt < min_tokens:
            continue
        if upper is not None and target_prompt > upper:
            continue
        return row
    return ordered[-1]


def get_price_quote(model: str, prompt_tokens: int) -> Dict[str, Any]:
    rows, state = _get_cache_snapshot()
    if not rows:
        schedule_background_refresh(force=False)
        return {
            "model": str(model or "").strip(),
            "promptPricePerMillionCny": 0.0,
            "completionPricePerMillionCny": 0.0,
            "missing": True,
            "stale": False,
            "sourceUrl": None,
        }

    stale = _is_expired(state)
    if stale:
        schedule_background_refresh(force=False)

    row = _select_price_row(model, prompt_tokens, rows)
    if row is None:
        schedule_background_refresh(force=False)
        return {
            "model": str(model or "").strip(),
            "promptPricePerMillionCny": 0.0,
            "completionPricePerMillionCny": 0.0,
            "missing": True,
            "stale": stale,
            "sourceUrl": state.get("source_url") or None,
        }

    return {
        "model": row.get("model"),
        "promptPricePerMillionCny": float(row.get("prompt_price_per_million_cny") or 0),
        "completionPricePerMillionCny": float(row.get("completion_price_per_million_cny") or 0),
        "missing": False,
        "stale": stale,
        "sourceUrl": row.get("source_url") or state.get("source_url") or None,
    }


def get_model_price(model: str, prompt_tokens: int = 0) -> Tuple[Decimal, Decimal, bool]:
    quote = get_price_quote(model, prompt_tokens)
    return (
        _to_decimal(quote.get("promptPricePerMillionCny")),
        _to_decimal(quote.get("completionPricePerMillionCny")),
        bool(quote.get("missing")),
    )


def estimate_cost_cny(model: str, prompt_tokens: int, completion_tokens: int) -> Tuple[float, bool]:
    prompt_price, completion_price, missing = get_model_price(model, prompt_tokens)
    prompt_units = Decimal(max(0, int(prompt_tokens or 0))) / Decimal("1000000")
    completion_units = Decimal(max(0, int(completion_tokens or 0))) / Decimal("1000000")
    cost = (prompt_units * prompt_price) + (completion_units * completion_price)
    return float(cost), missing
