"""Shared deterministic OHLCV fixtures for the forecast tests.

Not a test module (leading underscore — pytest does not collect it). A closed-form
sinusoid + drift gives a non-degenerate series (bands do not collapse, so feature
rows become defined) that is reproducible without a committed JSON fixture.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from market_analyser.data.types import Bar


def synthetic_bars(n: int) -> list[Bar]:
    """A deterministic OHLCV series of length ``n`` with realistic-enough variation
    to exercise every indicator the feature pipeline reads."""

    bars: list[Bar] = []
    for i in range(n):
        base = 100.0 + 0.15 * i + 8.0 * math.sin(i / 6.0)
        delta = 1.5 * math.sin(i / 3.0 + 1.0)
        open_ = base
        close = base + delta
        spread = 0.5 + 0.4 * abs(math.cos(i / 5.0))
        high = max(open_, close) + spread
        low = min(open_, close) - spread
        volume = 1_000_000.0 + 50_000.0 * math.sin(i / 4.0) + 1_000.0 * i
        bars.append(
            Bar(
                symbol="SYN",
                timeframe="1d",
                event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                source="synthetic",
            )
        )
    return bars


def with_close(bar: Bar, new_close: float) -> Bar:
    """Return a copy of ``bar`` with ``close`` set to ``new_close``, widening
    ``high``/``low`` as needed to keep the OHLC invariant valid."""

    high = max(bar.open, bar.high, new_close)
    low = min(bar.open, bar.low, new_close)
    return bar.model_copy(update={"close": new_close, "high": high, "low": low})
