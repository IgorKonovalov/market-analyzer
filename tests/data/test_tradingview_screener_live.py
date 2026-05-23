"""Plan 0009 phase 2 — live smoke for the TradingView screener.

Network-marked: skipped in CI (which filters out `-m network`), runnable locally
with `uv run pytest -m network`. Proves the adapter integrates with the real
upstream end-to-end — a CI green against the offline fixture does not.
"""

from __future__ import annotations

import pytest

from market_analyser.data.adapters.tradingview_screener import TradingViewScreenerAdapter


@pytest.mark.network
def test_live_screener_returns_rows() -> None:
    adapter = TradingViewScreenerAdapter()

    rows = adapter.query(
        {"RSI": {"lt": 30}},
        market="america",
        exchange="NASDAQ",
        limit=10,
    )

    assert isinstance(rows, list)
    assert len(rows) >= 1
    assert rows[0].symbol
