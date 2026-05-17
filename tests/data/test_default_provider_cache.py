"""Plan 0001 phase 3: cache policy on DefaultMarketDataProvider.

Asserts:
  - cache-miss path fetches from the adapter, upserts, and returns bars
  - cache-hit path serves from the repo with no adapter calls
  - as_of mode with empty cached coverage raises ValueError (no remote fetch)
  - as_of mode with cached coverage returns the cached bars
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from market_analyser.data.adapters.yahoo import YahooAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repository import BarRepository


@pytest.fixture
def repo() -> Iterator[BarRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield BarRepository(make_session_factory(engine))
    engine.dispose()


def _yahoo_with_calls(rows: list[dict[str, Any]]) -> tuple[YahooAdapter, list[str]]:
    calls: list[str] = []

    def fetcher(symbol: str, period: str, interval: str = "1d") -> list[dict[str, Any]]:
        calls.append(symbol)
        return rows

    return YahooAdapter(fetcher=fetcher), calls


def _row(date: str) -> dict[str, Any]:
    return {
        "date": date,
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 1_000_000.0,
    }


def test_cache_miss_fetches_upserts_and_returns(repo: BarRepository) -> None:
    yahoo, calls = _yahoo_with_calls([_row("2026-04-15"), _row("2026-04-16")])
    provider = DefaultMarketDataProvider(yahoo=yahoo, bar_repository=repo)
    bars = provider.get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert len(bars) == 2
    assert calls == ["AAPL"]
    # Second call must come straight from cache.
    bars_again = provider.get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert len(bars_again) == 2
    assert calls == ["AAPL"], "cache hit must not call the adapter again"


def test_as_of_with_empty_cache_raises_no_fetch(repo: BarRepository) -> None:
    yahoo, calls = _yahoo_with_calls([_row("2026-04-15")])
    provider = DefaultMarketDataProvider(yahoo=yahoo, bar_repository=repo)
    with pytest.raises(ValueError, match="anti-lookahead"):
        provider.get_ohlcv(
            "AAPL",
            "1d",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
            as_of=datetime(2026, 5, 1, tzinfo=UTC),
        )
    assert calls == []


def test_as_of_with_cached_data_returns_bars(repo: BarRepository) -> None:
    yahoo, calls = _yahoo_with_calls([_row("2026-04-15")])
    provider = DefaultMarketDataProvider(yahoo=yahoo, bar_repository=repo)
    # Warm the cache with a live-mode call.
    provider.get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert calls == ["AAPL"]

    # as_of in the future of ingested_at returns the cached row, no second fetch.
    bars = provider.get_ohlcv(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
        as_of=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    assert len(bars) == 1
    assert calls == ["AAPL"], "as_of with cached coverage must not refetch"


def test_no_repo_with_as_of_raises() -> None:
    yahoo, _calls = _yahoo_with_calls([_row("2026-04-15")])
    provider = DefaultMarketDataProvider(yahoo=yahoo)
    with pytest.raises(ValueError, match="as_of requires"):
        provider.get_ohlcv(
            "AAPL",
            "1d",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
            as_of=datetime(2026, 5, 1, tzinfo=UTC),
        )
