"""Plan 0001 phase 2: YahooAdapter unit tests.

The in-house `_fetch_yahoo_ohlcv` is replaced via the adapter's injectable
``fetcher`` seam. The tests assert the adapter's input validation, the
boundary validation on each `Bar`, and the window-filtering behavior —
covering the done-when items for phase 2.
"""

from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.yahoo import YahooAdapter

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "yahoo"


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


# -- Plan 0009 phase 3: HTTP now routes through ResilientHttpClient -----------


def _client_returning(payload: bytes) -> ResilientHttpClient:
    client = ResilientHttpClient(source_name="yahoo-test")
    client._perform_request = (  # type: ignore[method-assign]
        lambda method, url, body, headers, *, proxy: HttpResponse(
            status_code=200, headers={}, body=payload, elapsed_seconds=0.0
        )
    )
    return client


def test_fixture_round_trip_is_byte_identical() -> None:
    """The retrofitted adapter (HTTP via the shared client) reproduces the
    pre-retrofit Bar output byte-for-byte against the committed fixture."""
    payload = (_FIXTURE_DIR / "aapl_1d.json").read_bytes()
    adapter = YahooAdapter(http_client=_client_returning(payload))

    bars = adapter.fetch_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 10, tzinfo=UTC),
    )

    dumped = [bar.model_dump(mode="json") for bar in bars]
    expected = json.loads((_FIXTURE_DIR / "aapl_1d_expected_bars.json").read_text(encoding="utf-8"))
    assert dumped == expected


def test_transient_failure_retries_via_shared_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient transport failure is now retried by ResilientHttpClient, not
    hand-rolled in the adapter — proven by client.stats().retries incrementing."""
    monkeypatch.setattr(time, "sleep", lambda _s: None)  # skip real backoff sleep
    payload = (_FIXTURE_DIR / "aapl_1d.json").read_bytes()
    client = ResilientHttpClient(source_name="yahoo-test")
    attempts = {"n": 0}

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("transient")
        return HttpResponse(status_code=200, headers={}, body=payload, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = YahooAdapter(http_client=client)

    bars = adapter.fetch_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 10, tzinfo=UTC),
    )

    assert len(bars) == 4
    assert client.stats().retries >= 1
