"""Plan 0031 phase 1 — route-level integration test (the deterministic e2e gate).

The bug Plan 0031 fixes was *not* in the route or the provider's coverage math —
it was the Yahoo fetcher collapsing the requested ``[start, end]`` window to a
now-relative ``range=`` string, so a window ending in the past returned only a
handful of now-clustered bars. The adapter/provider unit tests (landed in
ce8067f) prove each layer in isolation; this test proves the whole chain threads
the real absolute window end-to-end:

    GET /ohlcv -> get_ohlcv -> _coverage_gaps -> YahooAdapter.fetch_ohlcv -> fetcher

It builds the real FastAPI app via ``create_app`` wired with a real
``DefaultMarketDataProvider`` over an empty in-memory ``BarRepository`` and a
``YahooAdapter`` whose only seam is the injected fetcher. The fetcher records the
window it was called with and returns synthetic daily rows spanning a window that
ends ~1 year ago. Deterministic and offline — no real network.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.data.adapters.yahoo import YahooAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repository import BarRepository

SECRET = "test-secret"

# A window whose `end` is well in the past (relative to today, 2026-06-02): the
# exact shape Plan 0030's scroll-left backward paging requests, and the case the
# bug produced a ~11-bar now-anchored remnant for.
_WINDOW_START = datetime(2024, 6, 3, tzinfo=UTC)
_WINDOW_END = datetime(2025, 6, 3, tzinfo=UTC)


@pytest.fixture
def repo() -> Iterator[BarRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield BarRepository(make_session_factory(engine))
    engine.dispose()


def _daily_row(ts: datetime) -> dict[str, Any]:
    return {
        "date": ts.strftime("%Y-%m-%d"),
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 1_000_000.0,
    }


def _window_rows(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """One synthetic daily row per calendar day across the full ``[start, end]``
    window — the verbatim payload an absolute period1/period2 fetch returns."""
    days = (end.date() - start.date()).days + 1
    return [_daily_row(start + timedelta(days=i)) for i in range(days)]


def test_past_window_ohlcv_route_returns_full_window_and_threads_absolute_window(
    repo: BarRepository,
) -> None:
    recorded: dict[str, datetime] = {}
    rows = _window_rows(_WINDOW_START, _WINDOW_END)

    def fetcher(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        # Record the absolute window the provider hands the adapter's fetcher.
        # Pre-fix, the now-anchored `range=` collapse meant this never saw the
        # requested past window; post-ce8067f it is the verbatim [start, end].
        recorded["start"] = start
        recorded["end"] = end
        return rows

    provider = DefaultMarketDataProvider(
        yahoo=YahooAdapter(fetcher=fetcher),
        bar_repository=repo,  # empty -> the full window is one gap, forcing a fetch
    )
    client = TestClient(create_app(secret=SECRET, provider=provider))

    response = client.get(
        "/ohlcv",
        params={
            "symbol": "AAPL",
            "timeframe": "1d",
            "start": _WINDOW_START.isoformat(),
            "end": _WINDOW_END.isoformat(),
        },
        headers={"Authorization": f"Bearer {SECRET}"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()

    # (a) The response carries the FULL past window's bars — every in-window row
    # the fetcher returned, not the ~11-bar now-anchored remnant the bug produced.
    assert len(payload) == len(rows)
    assert payload[0]["event_ts"].startswith("2024-06-03")
    assert payload[-1]["event_ts"].startswith("2025-06-03")
    assert all(bar["source"] == "yahoo" for bar in payload)

    # (b) The fetcher was invoked with the ABSOLUTE [start, end] window — the
    # regression guard. An empty cache makes the whole window a single gap, so the
    # gap-fetch window equals the requested window exactly (UTC).
    assert recorded["start"] == _WINDOW_START
    assert recorded["end"] == _WINDOW_END


def test_past_window_ohlcv_route_persists_fetched_bars(repo: BarRepository) -> None:
    """A past-ending window backed by an empty cache drives a gap-fetch whose bars
    are upserted into the cache, so a second identical request is served from the
    cache with no further fetch (proving the gap-fetch actually landed)."""
    rows = _window_rows(_WINDOW_START, _WINDOW_END)
    fetch_count = {"n": 0}

    def fetcher(
        symbol: str, start: datetime, end: datetime, interval: str = "1d"
    ) -> list[dict[str, Any]]:
        fetch_count["n"] += 1
        return rows

    provider = DefaultMarketDataProvider(
        yahoo=YahooAdapter(fetcher=fetcher),
        bar_repository=repo,
    )
    client = TestClient(create_app(secret=SECRET, provider=provider))
    params = {
        "symbol": "AAPL",
        "timeframe": "1d",
        "start": _WINDOW_START.isoformat(),
        "end": _WINDOW_END.isoformat(),
    }
    auth = {"Authorization": f"Bearer {SECRET}"}

    first = client.get("/ohlcv", params=params, headers=auth)
    assert first.status_code == 200, first.text
    assert len(first.json()) == len(rows)
    assert fetch_count["n"] == 1

    second = client.get("/ohlcv", params=params, headers=auth)
    assert second.status_code == 200, second.text
    assert len(second.json()) == len(rows)
    # The fetched bars were upserted: the dense daily cache covers the window with
    # no gap over the fetch threshold, so the second request makes no new fetch.
    assert fetch_count["n"] == 1
