"""Plan 0001 phase 3: BarRepository upsert + read tests against in-memory SQLite."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from market_analyser.data.types import Bar
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repository import _UPSERT_CHUNK_ROWS, BarRepository


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


def test_get_bars_filters_by_source_when_given(repo: BarRepository) -> None:
    """Plan 0081 / ADR-0076: the provenance-scoped read. Two same-symbol bars at
    different timestamps recorded under different sources — a `source`-filtered
    read returns only the matching source; the default (no filter) returns both."""
    yahoo_bar = Bar(
        symbol="BTC-USD",
        timeframe="1d",
        event_ts=datetime(2026, 4, 15, tzinfo=UTC),
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=5.0,
        source="yahoo",
    )
    coinbase_bar = yahoo_bar.model_copy(
        update={"event_ts": datetime(2026, 4, 16, tzinfo=UTC), "source": "coinbase"}
    )
    repo.upsert_bars([yahoo_bar, coinbase_bar])
    window = (datetime(2026, 4, 1, tzinfo=UTC), datetime(2026, 5, 1, tzinfo=UTC))

    scoped = repo.get_bars("BTC-USD", "1d", *window, source="coinbase")
    assert [b.source for b in scoped] == ["coinbase"]

    unscoped = repo.get_bars("BTC-USD", "1d", *window)
    assert sorted(b.source for b in unscoped) == ["coinbase", "yahoo"]


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


def _intraday_bars(count: int, *, symbol: str = "BTC-USD") -> list[Bar]:
    """`count` distinct hourly bars (distinct `event_ts` ⇒ no dedup collapse)."""
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Bar(
            symbol=symbol,
            timeframe="1h",
            event_ts=base + timedelta(hours=i),
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=1_000.0 + i,
            source="yahoo",
        )
        for i in range(count)
    ]


def test_upsert_large_payload_chunks_and_reads_all_back(repo: BarRepository) -> None:
    """A payload far larger than the bind-variable budget upserts without a
    `too many SQL variables` error, and every bar reads back."""
    count = 5_000
    assert count > _UPSERT_CHUNK_ROWS  # genuinely spans many chunks
    bars = _intraday_bars(count)

    assert repo.upsert_bars(bars) == count

    read_back = repo.get_bars(
        "BTC-USD",
        "1h",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 12, 31, tzinfo=UTC),
    )
    assert len(read_back) == count
    assert [b.event_ts for b in read_back] == [b.event_ts for b in bars]


class _FailOnNthExecute:
    """Session proxy that raises on its `n`-th `execute`, to simulate a failure
    partway through the chunked upsert. Everything else delegates to the real
    session, so the `with`-block close still rolls the transaction back."""

    def __init__(self, session: Any, *, n: int) -> None:
        self._session = session
        self._n = n
        self._calls = 0

    def __enter__(self) -> _FailOnNthExecute:
        self._session.__enter__()
        return self

    def __exit__(self, *exc: Any) -> Any:
        return self._session.__exit__(*exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        self._calls += 1
        if self._calls == self._n:
            raise RuntimeError("injected mid-batch failure")
        return self._session.execute(*args, **kwargs)


def test_upsert_rolls_back_atomically_on_midbatch_failure() -> None:
    """An injected failure on the second chunk's statement leaves no partial
    write — the whole upsert rolls back."""
    engine = make_engine(":memory:")
    apply_migrations(engine)
    real_factory = make_session_factory(engine)

    def failing_factory() -> _FailOnNthExecute:
        # Fail on the 2nd execute ⇒ the first chunk has already been issued on the
        # same transaction; if chunks committed independently it would survive.
        return _FailOnNthExecute(real_factory(), n=2)

    # The proxy duck-types a Session (delegates everything but the injected execute).
    repo = BarRepository(cast("Callable[[], Session]", failing_factory))
    # More than one chunk's worth so a second `execute` (and thus the failure) fires.
    bars = _intraday_bars(_UPSERT_CHUNK_ROWS * 2 + 5)

    with pytest.raises(RuntimeError, match="injected mid-batch failure"):
        repo.upsert_bars(bars)

    # A clean repo on the same in-memory DB sees nothing — the first chunk rolled back too.
    clean_repo = BarRepository(real_factory)
    assert (
        clean_repo.get_bars(
            "BTC-USD",
            "1h",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 12, 31, tzinfo=UTC),
        )
        == []
    )
    engine.dispose()
