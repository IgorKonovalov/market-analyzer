"""Plan 0001 phase 2: YahooAdapter unit tests.

The vendored `_fetch_ohlcv` is mocked. The tests assert the adapter's input
validation, the boundary validation on each `Bar`, and the window-filtering
behavior — covering the done-when items for phase 2.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import pytest

from market_analyser.data.adapters.yahoo import YahooAdapter


def _make_fetcher(rows: list[dict[str, Any]]) -> Any:
    def fetcher(symbol: str, period: str, interval: str = "1d") -> list[dict[str, Any]]:
        return rows

    return fetcher


def _good_row(
    date: str, *, open_: float = 100.0, close: float = 101.0, volume: float = 1_000_000.0
) -> dict[str, Any]:
    return {
        "date": date,
        "open": open_,
        "high": max(open_, close) + 1.0,
        "low": min(open_, close) - 1.0,
        "close": close,
        "volume": volume,
    }


def test_fetch_ohlcv_returns_validated_bars() -> None:
    adapter = YahooAdapter(
        fetcher=_make_fetcher(
            [
                _good_row("2026-04-15"),
                _good_row("2026-04-16", open_=101.0, close=102.5),
            ]
        )
    )
    bars = adapter.fetch_ohlcv(
        "aapl",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert len(bars) == 2
    assert bars[0].symbol == "AAPL"
    assert bars[0].source == "yahoo"
    assert bars[0].event_ts == datetime(2026, 4, 15, tzinfo=UTC)
    assert bars[0].timeframe == "1d"


def test_fetch_ohlcv_filters_out_of_window_rows() -> None:
    adapter = YahooAdapter(
        fetcher=_make_fetcher(
            [
                _good_row("2026-03-15"),  # before window
                _good_row("2026-04-15"),  # inside window
                _good_row("2026-05-15"),  # after window
            ]
        )
    )
    bars = adapter.fetch_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert len(bars) == 1
    assert bars[0].event_ts == datetime(2026, 4, 15, tzinfo=UTC)


def test_fetch_ohlcv_rejects_nan_close() -> None:
    bad = _good_row("2026-04-15")
    bad["close"] = math.nan
    adapter = YahooAdapter(fetcher=_make_fetcher([bad]))
    with pytest.raises(ValueError):
        adapter.fetch_ohlcv(
            "AAPL",
            "1d",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
        )


def test_fetch_ohlcv_rejects_negative_volume() -> None:
    bad = _good_row("2026-04-15")
    bad["volume"] = -1.0
    adapter = YahooAdapter(fetcher=_make_fetcher([bad]))
    with pytest.raises(ValueError):
        adapter.fetch_ohlcv(
            "AAPL",
            "1d",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
        )


def test_fetch_ohlcv_rejects_malformed_timestamp() -> None:
    bad = _good_row("not-a-date")
    adapter = YahooAdapter(fetcher=_make_fetcher([bad]))
    with pytest.raises(ValueError):
        adapter.fetch_ohlcv(
            "AAPL",
            "1d",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
        )


def test_fetch_ohlcv_rejects_unsupported_timeframe() -> None:
    adapter = YahooAdapter(fetcher=_make_fetcher([]))
    with pytest.raises(ValueError, match="timeframe"):
        adapter.fetch_ohlcv(
            "AAPL",
            "5m",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
        )


def test_fetch_ohlcv_rejects_empty_symbol() -> None:
    adapter = YahooAdapter(fetcher=_make_fetcher([]))
    with pytest.raises(ValueError, match="symbol"):
        adapter.fetch_ohlcv(
            "  ",
            "1d",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
        )


def test_fetch_ohlcv_rejects_naive_datetimes() -> None:
    adapter = YahooAdapter(fetcher=_make_fetcher([]))
    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.fetch_ohlcv("AAPL", "1d", datetime(2026, 4, 1), datetime(2026, 5, 1))


def test_fetch_ohlcv_rejects_start_after_end() -> None:
    adapter = YahooAdapter(fetcher=_make_fetcher([]))
    with pytest.raises(ValueError, match="strictly before"):
        adapter.fetch_ohlcv(
            "AAPL",
            "1d",
            datetime(2026, 5, 1, tzinfo=UTC),
            datetime(2026, 4, 1, tzinfo=UTC),
        )


def test_fetch_ohlcv_rejects_span_over_2y() -> None:
    adapter = YahooAdapter(fetcher=_make_fetcher([]))
    with pytest.raises(ValueError, match="exceeds supported max"):
        adapter.fetch_ohlcv(
            "AAPL",
            "1d",
            datetime(2020, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_fetch_ohlcv_picks_smallest_sufficient_period() -> None:
    captured: dict[str, str] = {}

    def fetcher(symbol: str, period: str, interval: str = "1d") -> list[dict[str, Any]]:
        captured["period"] = period
        return []

    adapter = YahooAdapter(fetcher=fetcher)
    adapter.fetch_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 25, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert captured["period"] == "1mo"
