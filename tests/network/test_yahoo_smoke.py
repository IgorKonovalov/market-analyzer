"""Plan 0001 phase 2: live-network smoke test, @pytest.mark.network so it
is skipped in CI by default. Run locally with `uv run pytest -m network`.

Asserts the in-house Yahoo Chart fetcher returns >=5 daily bars for AAPL over
the last 10 days. The window is 10 calendar days rather than 7 (Plan 0072
phase 5): a 7-day window over a holiday week yields fewer than 5 trading days
and the smoke flakes on the calendar, not on the fetcher — 10 days clears >=5
trading days even with a long holiday weekend inside it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.data.adapters.yahoo import YahooAdapter


@pytest.mark.network
def test_yahoo_aapl_returns_at_least_five_bars() -> None:
    adapter = YahooAdapter()
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=10)
    bars = adapter.fetch_ohlcv("AAPL", "1d", start, end)
    assert len(bars) >= 5, f"expected >=5 bars for AAPL 1d in last 10d, got {len(bars)}"
    for bar in bars:
        assert bar.symbol == "AAPL"
        assert bar.source == "yahoo"
        assert bar.event_ts.tzinfo is not None
