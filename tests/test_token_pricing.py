from __future__ import annotations

import pytest

from backend import token_pricing


def test_estimate_cost_cny_with_configured_model(monkeypatch):
    monkeypatch.setenv(
        token_pricing.TOKEN_PRICING_ENV_KEY,
        '{"qwen-plus":{"prompt":0.8,"completion":2.0}}',
    )

    cost, missing = token_pricing.estimate_cost_cny(
        model="qwen-plus",
        prompt_tokens=1_000_000,
        completion_tokens=500_000,
    )

    assert missing is False
    assert cost == pytest.approx(1.8)


def test_estimate_cost_cny_missing_model_returns_zero(monkeypatch):
    monkeypatch.setenv(token_pricing.TOKEN_PRICING_ENV_KEY, "{}")

    cost, missing = token_pricing.estimate_cost_cny(
        model="unknown-model",
        prompt_tokens=12_345,
        completion_tokens=67_890,
    )

    assert missing is True
    assert cost == pytest.approx(0.0)


def test_invalid_pricing_json_fallbacks_to_empty_table(monkeypatch):
    monkeypatch.setenv(token_pricing.TOKEN_PRICING_ENV_KEY, "{invalid-json")

    table = token_pricing.get_pricing_table()
    cost, missing = token_pricing.estimate_cost_cny(
        model="qwen-plus",
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
    )

    assert table == {}
    assert missing is True
    assert cost == pytest.approx(0.0)
