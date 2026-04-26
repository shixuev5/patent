from __future__ import annotations

import json

import pytest

from backend import token_pricing
from backend.storage import SQLiteTaskStorage
from backend.time_utils import utc_now_z


def _build_help_page(content_html: str) -> str:
    payload = {
        "docDetailData": {
            "storeData": {
                "data": {
                    "content": content_html,
                }
            }
        }
    }
    return f"<script>window.__ICE_PAGE_PROPS__={json.dumps(payload, ensure_ascii=False)};\n</script>"


def _fake_pricing_content() -> str:
    return """
    <div lang="zh">
      <section>
        <h3>千问 Plus</h3>
        <section class="tabbed-content-box section">
          <section>
            <h2>中国内地</h2>
            <table>
              <tbody>
                <tr>
                  <td><b>模型名称</b></td>
                  <td><b>单次请求的输入 Token 范围</b></td>
                  <td><b>输入单价（每百万 Token）</b></td>
                  <td><b>输出单价（每百万 Token） 非思考模式</b></td>
                  <td><b>输出单价（每百万 Token） 思考模式</b></td>
                </tr>
                <tr>
                  <td rowspan="2"><p>qwen3.5-plus</p><blockquote>Batch 调用半价</blockquote></td>
                  <td>0&lt;Token≤128K</td>
                  <td>0.8 元</td>
                  <td>4.8 元</td>
                  <td>9.6 元</td>
                </tr>
                <tr>
                  <td>128K&lt;Token≤256K</td>
                  <td>1.2 元</td>
                  <td>6.6 元</td>
                  <td>13.2 元</td>
                </tr>
                <tr>
                  <td><p>qwen3.5-flash</p></td>
                  <td>0&lt;Token≤128K</td>
                  <td>0.2 元</td>
                  <td>2.0 元</td>
                  <td>4.0 元</td>
                </tr>
              </tbody>
            </table>
          </section>
          <section>
            <h2>国际</h2>
            <table>
              <tbody>
                <tr><td><b>模型名称</b></td><td><b>输入单价（每百万 Token）</b></td><td><b>输出单价（每百万 Token）</b></td></tr>
                <tr><td>qwen3.5-plus</td><td>99 元</td><td>199 元</td></tr>
              </tbody>
            </table>
          </section>
        </section>
      </section>
    </div>
    """


def _seed_pricing_state(storage: SQLiteTaskStorage, *, expires_at: str | None = None) -> None:
    now_iso = utc_now_z(timespec="seconds")
    storage.replace_llm_pricing_entries(
        [
            {
                "model": "qwen3.5-flash",
                "region": token_pricing.TOKEN_PRICING_REGION,
                "billing_mode": token_pricing.TOKEN_PRICING_BILLING_MODE,
                "input_tier_min_tokens": 1,
                "input_tier_max_tokens": 131072,
                "prompt_price_per_million_cny": 0.2,
                "completion_price_per_million_cny": 2.0,
                "source_url": "https://example.com/pricing",
                "source_hash": "seeded",
                "fetched_at": now_iso,
                "expires_at": expires_at or "2099-01-01T00:00:00Z",
                "parse_status": "ok",
                "parse_error": "",
                "updated_at": now_iso,
            }
        ],
        region=token_pricing.TOKEN_PRICING_REGION,
        billing_mode=token_pricing.TOKEN_PRICING_BILLING_MODE,
    )
    storage.upsert_llm_pricing_sync_state(
        {
            "region": token_pricing.TOKEN_PRICING_REGION,
            "billing_mode": token_pricing.TOKEN_PRICING_BILLING_MODE,
            "cache_entry_count": 1,
            "last_success_at": now_iso,
            "last_attempt_at": now_iso,
            "expires_at": expires_at or "2099-01-01T00:00:00Z",
            "source_url": "https://example.com/pricing",
            "source_hash": "seeded",
            "parse_status": "ok",
            "last_error": "",
            "updated_at": now_iso,
        }
    )


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_refresh_pricing_cache_parses_cn_page_and_estimates_by_tier(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "token_pricing_refresh.db")
    token_pricing.configure_pricing_storage(lambda: storage)
    token_pricing.reset_pricing_runtime_cache()
    page_html = _build_help_page(_fake_pricing_content())

    monkeypatch.setattr(
        token_pricing.requests,
        "get",
        lambda url, timeout: _FakeResponse(page_html),
    )

    result = token_pricing.refresh_pricing_cache(force=True)

    assert result["success"] is True
    assert result["refreshed"] is True
    assert result["entryCount"] == 3

    quote = token_pricing.get_price_quote("qwen3.5-plus", 200_000)
    assert quote["missing"] is False
    assert quote["promptPricePerMillionCny"] == pytest.approx(1.2)
    assert quote["completionPricePerMillionCny"] == pytest.approx(6.6)

    cost, missing = token_pricing.estimate_cost_cny(
        model="qwen3.5-plus",
        prompt_tokens=200_000,
        completion_tokens=50_000,
    )
    assert missing is False
    assert cost == pytest.approx((200_000 / 1_000_000) * 1.2 + (50_000 / 1_000_000) * 6.6)


def test_refresh_pricing_cache_failure_keeps_existing_rows(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "token_pricing_refresh_failure.db")
    token_pricing.configure_pricing_storage(lambda: storage)
    token_pricing.reset_pricing_runtime_cache()
    _seed_pricing_state(storage, expires_at="2000-01-01T00:00:00Z")

    def _raise(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(token_pricing.requests, "get", _raise)

    result = token_pricing.refresh_pricing_cache(force=True)
    status = token_pricing.get_pricing_status()
    quote = token_pricing.get_price_quote("qwen3.5-flash", 128)

    assert result["success"] is False
    assert status["hasUsableCache"] is True
    assert status["parseStatus"] == "error"
    assert "network down" in str(status["errorMessage"])
    assert quote["missing"] is False
    assert quote["stale"] is True
