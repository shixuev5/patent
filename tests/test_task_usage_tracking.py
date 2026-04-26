from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend import task_usage_tracking, token_pricing
from backend.storage import SQLiteTaskStorage
from backend.time_utils import to_utc_z, utc_now_z


def _seed_pricing_cache(storage: SQLiteTaskStorage, entries: list[dict]) -> None:
    token_pricing.configure_pricing_storage(lambda: storage)
    token_pricing.reset_pricing_runtime_cache()
    now_iso = utc_now_z(timespec="seconds")
    storage.replace_llm_pricing_entries(
        [
            {
                "region": token_pricing.TOKEN_PRICING_REGION,
                "billing_mode": token_pricing.TOKEN_PRICING_BILLING_MODE,
                "source_url": "https://example.com/pricing",
                "source_hash": "test",
                "fetched_at": now_iso,
                "expires_at": "2099-01-01T00:00:00Z",
                "parse_status": "ok",
                "parse_error": "",
                "updated_at": now_iso,
                **item,
            }
            for item in entries
        ],
        region=token_pricing.TOKEN_PRICING_REGION,
        billing_mode=token_pricing.TOKEN_PRICING_BILLING_MODE,
    )
    storage.upsert_llm_pricing_sync_state(
        {
            "region": token_pricing.TOKEN_PRICING_REGION,
            "billing_mode": token_pricing.TOKEN_PRICING_BILLING_MODE,
            "cache_entry_count": len(entries),
            "last_success_at": now_iso,
            "last_attempt_at": now_iso,
            "expires_at": "2099-01-01T00:00:00Z",
            "source_url": "https://example.com/pricing",
            "source_hash": "test",
            "parse_status": "ok",
            "last_error": "",
            "updated_at": now_iso,
        }
    )


def test_task_usage_collector_aggregates_and_persists(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "task_usage_tracking_test.db")
    _seed_pricing_cache(
        storage,
        [
            {
                "model": "qwen3.5-flash",
                "input_tier_min_tokens": 1,
                "input_tier_max_tokens": 131072,
                "prompt_price_per_million_cny": 0.2,
                "completion_price_per_million_cny": 2.0,
            }
        ],
    )

    collector = task_usage_tracking.create_task_usage_collector(
        task_id="task-001",
        owner_id="authing:user-1",
        task_type="patent_analysis",
    )
    collector.mark_status("completed")

    with task_usage_tracking.task_usage_collection(collector):
        task_usage_tracking.record_llm_usage(
            model="qwen3.5-flash",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            reasoning_tokens=20,
        )
        task_usage_tracking.record_llm_usage(
            model="unknown-model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            reasoning_tokens=0,
        )

    persisted = task_usage_tracking.persist_task_usage(storage, collector)
    assert persisted is True

    start_iso = to_utc_z(datetime.utcnow() - timedelta(days=1), naive_strategy="utc")
    end_iso = to_utc_z(datetime.utcnow() + timedelta(days=1), naive_strategy="utc")
    rows = storage.list_task_llm_usage_by_last_usage_range(start_iso=start_iso, end_iso=end_iso)

    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == "task-001"
    assert row["task_status"] == "completed"
    assert row["prompt_tokens"] == 210
    assert row["completion_tokens"] == 105
    assert row["total_tokens"] == 315
    assert row["reasoning_tokens"] == 20
    assert row["llm_call_count"] == 2
    assert row["estimated_cost_cny"] > 0
    assert row["price_missing"] is True
    assert "qwen3.5-flash" in (row["model_breakdown_json"] or {})
    assert "unknown-model" in (row["model_breakdown_json"] or {})


def test_upsert_task_llm_usage_normalizes_utc_strings(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "task_usage_tracking_normalize_test.db")
    _seed_pricing_cache(
        storage,
        [
            {
                "model": "qwen3.5-flash",
                "input_tier_min_tokens": 1,
                "input_tier_max_tokens": 131072,
                "prompt_price_per_million_cny": 0.2,
                "completion_price_per_million_cny": 2.0,
            }
        ],
    )

    assert storage.upsert_task_llm_usage(
        {
            "task_id": "task-raw-utc",
            "owner_id": "authing:user-1",
            "task_type": "patent_analysis",
            "task_status": "completed",
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "total_tokens": 3,
            "reasoning_tokens": 0,
            "llm_call_count": 1,
            "estimated_cost_cny": 0.01,
            "price_missing": False,
            "model_breakdown_json": {"qwen3.5-flash": {"totalTokens": 3}},
            "first_usage_at": "2026-03-20T15:03:08",
            "last_usage_at": "2026-03-20T15:05:08",
            "created_at": "2026-03-20T15:03:08",
            "updated_at": "2026-03-20T15:05:08",
        }
    )

    with storage._get_connection() as conn:
        row = conn.execute(
            "SELECT first_usage_at, last_usage_at, created_at, updated_at FROM task_llm_usage WHERE task_id = ?",
            ("task-raw-utc",),
        ).fetchone()

    assert row["first_usage_at"] == "2026-03-20T15:03:08.000000Z"
    assert row["last_usage_at"] == "2026-03-20T15:05:08.000000Z"
    assert row["created_at"] == "2026-03-20T15:03:08.000000Z"
    assert row["updated_at"] == "2026-03-20T15:05:08.000000Z"


def test_persist_task_usage_merge_accumulates_existing_rows(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "task_usage_tracking_merge_test.db")
    _seed_pricing_cache(
        storage,
        [
            {
                "model": "qwen3.5-flash",
                "input_tier_min_tokens": 1,
                "input_tier_max_tokens": 131072,
                "prompt_price_per_million_cny": 0.2,
                "completion_price_per_million_cny": 2.0,
            },
            {
                "model": "qwen3.5-plus",
                "input_tier_min_tokens": 1,
                "input_tier_max_tokens": 131072,
                "prompt_price_per_million_cny": 0.8,
                "completion_price_per_million_cny": 4.8,
            },
        ],
    )

    assert storage.upsert_task_llm_usage(
        {
            "task_id": "task-merge-1",
            "owner_id": "authing:user-1",
            "task_type": "ai_search",
            "task_status": "processing",
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "total_tokens": 140,
            "reasoning_tokens": 10,
            "llm_call_count": 1,
            "estimated_cost_cny": 0.272,
            "price_missing": False,
            "model_breakdown_json": {
                "qwen3.5-flash": {
                    "model": "qwen3.5-flash",
                    "promptTokens": 100,
                    "completionTokens": 40,
                    "totalTokens": 140,
                    "reasoningTokens": 10,
                    "llmCallCount": 1,
                    "estimatedCostCny": 0.272,
                    "priceMissing": False,
                }
            },
            "first_usage_at": "2026-04-11T04:00:00Z",
            "last_usage_at": "2026-04-11T04:00:00Z",
            "created_at": "2026-04-11T04:00:00Z",
            "updated_at": "2026-04-11T04:00:00Z",
        }
    )

    collector = task_usage_tracking.create_task_usage_collector(
        task_id="task-merge-1",
        owner_id="authing:user-1",
        task_type="ai_search",
    )
    collector.mark_status("completed")
    with task_usage_tracking.task_usage_collection(collector):
        task_usage_tracking.record_llm_usage(
            model="qwen3.5-plus",
            prompt_tokens=200,
            completion_tokens=50,
            total_tokens=250,
            reasoning_tokens=20,
        )

    persisted = task_usage_tracking.persist_task_usage(storage, collector, merge=True)
    assert persisted is True

    row = storage.get_task_llm_usage("task-merge-1")
    assert row is not None
    assert row["task_status"] == "completed"
    assert row["prompt_tokens"] == 300
    assert row["completion_tokens"] == 90
    assert row["total_tokens"] == 390
    assert row["reasoning_tokens"] == 30
    assert row["llm_call_count"] == 2
    assert row["estimated_cost_cny"] == pytest.approx(0.2724)
    assert row["first_usage_at"] == "2026-04-11T04:00:00.000000Z"
    assert row["last_usage_at"] is not None
    assert set((row["model_breakdown_json"] or {}).keys()) == {"qwen3.5-flash", "qwen3.5-plus"}
    assert row["model_breakdown_json"]["qwen3.5-plus"]["totalTokens"] == 250


def test_persist_task_usage_merge_without_new_usage_keeps_existing_totals(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "task_usage_tracking_merge_status_test.db")
    _seed_pricing_cache(
        storage,
        [
            {
                "model": "qwen3.5-flash",
                "input_tier_min_tokens": 1,
                "input_tier_max_tokens": 131072,
                "prompt_price_per_million_cny": 0.2,
                "completion_price_per_million_cny": 2.0,
            }
        ],
    )

    assert storage.upsert_task_llm_usage(
        {
            "task_id": "task-merge-2",
            "owner_id": "authing:user-1",
            "task_type": "ai_search",
            "task_status": "processing",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "reasoning_tokens": 0,
            "llm_call_count": 1,
            "estimated_cost_cny": 0.012,
            "price_missing": False,
            "model_breakdown_json": {"qwen3.5-flash": {"totalTokens": 15}},
            "first_usage_at": "2026-04-11T04:00:00Z",
            "last_usage_at": "2026-04-11T04:00:00Z",
            "created_at": "2026-04-11T04:00:00Z",
            "updated_at": "2026-04-11T04:00:00Z",
        }
    )

    collector = task_usage_tracking.create_task_usage_collector(
        task_id="task-merge-2",
        owner_id="authing:user-1",
        task_type="ai_search",
    )
    collector.mark_status("completed")

    persisted = task_usage_tracking.persist_task_usage(storage, collector, merge=True)
    assert persisted is True

    row = storage.get_task_llm_usage("task-merge-2")
    assert row is not None
    assert row["task_status"] == "completed"
    assert row["prompt_tokens"] == 10
    assert row["completion_tokens"] == 5
    assert row["total_tokens"] == 15
    assert row["llm_call_count"] == 1
    assert row["first_usage_at"] == "2026-04-11T04:00:00.000000Z"
    assert row["last_usage_at"] == "2026-04-11T04:00:00.000000Z"


def test_persist_task_usage_merge_skips_empty_new_task(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "task_usage_tracking_empty_merge_test.db")
    _seed_pricing_cache(
        storage,
        [
            {
                "model": "qwen3.5-flash",
                "input_tier_min_tokens": 1,
                "input_tier_max_tokens": 131072,
                "prompt_price_per_million_cny": 0.2,
                "completion_price_per_million_cny": 2.0,
            }
        ],
    )

    collector = task_usage_tracking.create_task_usage_collector(
        task_id="task-empty",
        owner_id="authing:user-1",
        task_type="ai_search",
    )
    collector.mark_status("completed")

    persisted = task_usage_tracking.persist_task_usage(storage, collector, merge=True)
    assert persisted is False
    assert storage.get_task_llm_usage("task-empty") is None
