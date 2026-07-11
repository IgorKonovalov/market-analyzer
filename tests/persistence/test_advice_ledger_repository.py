"""Plan 0080 phase 1: the append-only `advice_ledger` repository (ADR-0075).

The track record's storage half. The properties under test are the ones that make
the record honest-by-construction:

- a recorded call round-trips (identity, ticket, and null outcome);
- **first-write-wins / append-only**: re-recording the same call at the same bar
  does not duplicate the row, and — critically — does not clobber an outcome the
  scorer already wrote (the anti-cherry-pick guarantee);
- a flat "no actionable edge" call is recorded too, marked non-directional;
- listing filters by symbol, directionality, and maturity (scored / not).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from market_analyser.persistence.advice_ledger_repository import (
    AdviceLedgerEntry,
    AdviceLedgerRepository,
)
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

_AS_OF = datetime(2026, 7, 10, tzinfo=UTC)
_CREATED = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def repo() -> Iterator[AdviceLedgerRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AdviceLedgerRepository(make_session_factory(engine))
    engine.dispose()


def _directional(
    *,
    symbol: str = "DOGE-USD",
    as_of: datetime = _AS_OF,
    horizon_bars: int = 5,
    direction: str = "long",
) -> AdviceLedgerEntry:
    return AdviceLedgerEntry(
        symbol=symbol,
        timeframe="1d",
        strategy_id="rsi",
        as_of_bar_ts=as_of,
        horizon_bars=horizon_bars,
        direction=direction,  # type: ignore[arg-type]
        entry_zone=(0.10, 0.11),
        stop=0.09,
        targets=[0.13, 0.15],
        conviction=0.6,
        forecast_prob=0.62,
        artifact_path="advice/20260710T120000000000Z-DOGE-USD/explanation.json",
        created_at=_CREATED,
    )


def _flat(*, symbol: str = "SPY", as_of: datetime = _AS_OF) -> AdviceLedgerEntry:
    return AdviceLedgerEntry(
        symbol=symbol,
        timeframe="1d",
        strategy_id="rsi",
        as_of_bar_ts=as_of,
        horizon_bars=5,
        direction="flat",
        entry_zone=None,
        stop=None,
        targets=[],
        conviction=0.0,
        forecast_prob=None,
        artifact_path=None,
        created_at=_CREATED,
    )


def _get(repo: AdviceLedgerRepository, entry: AdviceLedgerEntry) -> AdviceLedgerEntry | None:
    return repo.get(
        symbol=entry.symbol,
        timeframe=entry.timeframe,
        strategy_id=entry.strategy_id,
        as_of_bar_ts=entry.as_of_bar_ts,
        horizon_bars=entry.horizon_bars,
    )


def test_record_then_get_round_trips(repo: AdviceLedgerRepository) -> None:
    entry = _directional()
    assert repo.record(entry) is True
    got = _get(repo, entry)
    assert got is not None
    assert got.direction == "long"
    assert got.entry_zone == (0.10, 0.11)
    assert got.stop == 0.09
    assert got.targets == [0.13, 0.15]
    assert got.conviction == 0.6
    assert got.forecast_prob == 0.62
    assert got.as_of_bar_ts == _AS_OF
    assert got.artifact_path == entry.artifact_path
    # Freshly recorded → no outcome yet.
    assert got.outcome_class is None
    assert got.realized_r is None
    assert got.scored_at is None


def test_get_missing_call_returns_none(repo: AdviceLedgerRepository) -> None:
    assert _get(repo, _directional()) is None


def test_flat_call_is_recorded_marked_non_directional(repo: AdviceLedgerRepository) -> None:
    entry = _flat()
    assert repo.record(entry) is True
    got = _get(repo, entry)
    assert got is not None
    assert got.direction == "flat"
    assert got.entry_zone is None and got.stop is None and got.targets == []
    assert got.conviction == 0.0
    assert got.forecast_prob is None


def test_re_record_same_call_does_not_duplicate(repo: AdviceLedgerRepository) -> None:
    entry = _directional()
    assert repo.record(entry) is True
    assert repo.record(entry) is False  # append-only: the second write is a no-op
    assert len(repo.list(symbol="DOGE-USD")) == 1


def test_re_record_does_not_clobber_a_written_outcome(repo: AdviceLedgerRepository) -> None:
    """The anti-cherry-pick guarantee: once the scorer has written an outcome, a
    later re-run of the same recommendation (which arrives with a null outcome)
    must NOT overwrite the scored row back to unscored."""
    scored = _directional().model_copy(
        update={
            "outcome_class": "stopped",
            "realized_return": -0.08,
            "realized_r": -1.0,
            "directional_correct": False,
            "scored_at": datetime(2026, 7, 16, tzinfo=UTC),
        }
    )
    assert repo.record(scored) is True

    fresh_unscored = _directional()  # same identity, outcome None
    assert repo.record(fresh_unscored) is False

    got = _get(repo, scored)
    assert got is not None
    assert got.outcome_class == "stopped"  # the scored outcome survived
    assert got.realized_r == -1.0
    assert got.directional_correct is False


def test_distinct_bars_are_distinct_calls(repo: AdviceLedgerRepository) -> None:
    first = _directional(as_of=_AS_OF)
    later = _directional(as_of=datetime(2026, 7, 11, tzinfo=UTC))
    assert repo.record(first) is True
    assert repo.record(later) is True  # a new bar → a new call, not a dedupe
    assert len(repo.list(symbol="DOGE-USD")) == 2


def test_distinct_horizons_are_distinct_calls(repo: AdviceLedgerRepository) -> None:
    assert repo.record(_directional(horizon_bars=5)) is True
    assert repo.record(_directional(horizon_bars=21)) is True
    assert len(repo.list(symbol="DOGE-USD")) == 2


def test_list_filters_by_symbol(repo: AdviceLedgerRepository) -> None:
    repo.record(_directional(symbol="DOGE-USD"))
    repo.record(_directional(symbol="BTC-USD"))
    doge = repo.list(symbol="DOGE-USD")
    assert len(doge) == 1 and doge[0].symbol == "DOGE-USD"


def test_list_filters_by_directionality(repo: AdviceLedgerRepository) -> None:
    repo.record(_directional(symbol="DOGE-USD"))
    repo.record(_flat(symbol="SPY"))
    directional = repo.list(directional=True)
    flat = repo.list(directional=False)
    assert {e.symbol for e in directional} == {"DOGE-USD"}
    assert {e.symbol for e in flat} == {"SPY"}


def test_list_filters_by_maturity_scored(repo: AdviceLedgerRepository) -> None:
    repo.record(_directional(symbol="DOGE-USD"))  # unscored
    repo.record(
        _directional(symbol="BTC-USD").model_copy(
            update={"outcome_class": "target_hit", "scored_at": _CREATED}
        )
    )
    scored = repo.list(scored=True)
    unscored = repo.list(scored=False)
    assert {e.symbol for e in scored} == {"BTC-USD"}
    assert {e.symbol for e in unscored} == {"DOGE-USD"}


def test_list_rejects_out_of_range_limit(repo: AdviceLedgerRepository) -> None:
    with pytest.raises(ValueError, match="limit must be in"):
        repo.list(limit=0)
    with pytest.raises(ValueError, match="limit must be in"):
        repo.list(limit=10_000)
