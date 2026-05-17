"""Plan 0001 phase 3: BarRepository upsert + read tests against in-memory SQLite."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.data.types import Bar
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repository import BarRepository


def _bar(symbol: str = "AAPL", *, day: int = 15, close: float = 101.0) -> Bar:
    return Bar(
        symbol=symbol,
        timeframe="1d",
        event_ts=datetime(2026, 4, day, tzinfo=UTC),
        open=100.0,
        high=102.0,
        low=99.0,
        close=close,
        volume=1_000_000.0,
        source="yahoo",
    )


@pytest.fixture
def repo() -> Iterator[BarRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield BarRepository(make_session_factory(engine))
    engine.dispose()


def test_upsert_inserts_and_get_bars_returns_them(repo: BarRepository) -> None:
    assert repo.upsert_bars([_bar(day=15), _bar(day=16, close=102.0)]) == 2
    bars = repo.get_bars(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert len(bars) == 2
    assert bars[0].event_ts < bars[1].event_ts


def test_upsert_deduplicates_on_symbol_timeframe_event_ts(repo: BarRepository) -> None:
    repo.upsert_bars([_bar(day=15, close=100.0)])
    repo.upsert_bars([_bar(day=15, close=101.5)])  # same composite key, updated close
    bars = repo.get_bars(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert len(bars) == 1
    assert bars[0].close == 101.5


def test_get_bars_filters_by_window(repo: BarRepository) -> None:
    repo.upsert_bars([_bar(day=5), _bar(day=15), _bar(day=25)])
    bars = repo.get_bars(
        "AAPL",
        "1d",
        datetime(2026, 4, 10, tzinfo=UTC),
        datetime(2026, 4, 20, tzinfo=UTC),
    )
    assert [b.event_ts.day for b in bars] == [15]


def test_get_bars_filters_by_symbol_and_timeframe(repo: BarRepository) -> None:
    repo.upsert_bars([_bar("AAPL"), _bar("MSFT")])
    bars = repo.get_bars(
        "MSFT",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    )
    assert len(bars) == 1
    assert bars[0].symbol == "MSFT"


def test_as_of_filters_by_ingested_at(repo: BarRepository) -> None:
    """Anti-lookahead: a bar written `now` is invisible to an `as_of` in the past."""
    repo.upsert_bars([_bar(day=15)])
    bars = repo.get_bars(
        "AAPL",
        "1d",
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
        as_of=datetime.now(tz=UTC) - timedelta(hours=1),
    )
    assert bars == []


def test_upsert_rejects_empty_source_bar(repo: BarRepository) -> None:
    with pytest.raises(ValueError):
        Bar(
            symbol="AAPL",
            timeframe="1d",
            event_ts=datetime(2026, 4, 15, tzinfo=UTC),
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=1_000_000.0,
            source="",
        )


def test_get_bars_rejects_naive_datetimes(repo: BarRepository) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.get_bars("AAPL", "1d", datetime(2026, 4, 1), datetime(2026, 5, 1))


def test_get_bars_rejects_empty_symbol(repo: BarRepository) -> None:
    with pytest.raises(ValueError, match="symbol"):
        repo.get_bars(
            "",
            "1d",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
        )


def test_get_bars_rejects_naive_as_of(repo: BarRepository) -> None:
    with pytest.raises(ValueError, match="as_of"):
        repo.get_bars(
            "AAPL",
            "1d",
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
            as_of=datetime(2026, 4, 15),
        )


def test_upsert_no_bars_is_noop(repo: BarRepository) -> None:
    assert repo.upsert_bars([]) == 0
