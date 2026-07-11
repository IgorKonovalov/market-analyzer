"""Plan 0080 phase 3: the scheduled recommendation scorer (ADR-0075, ADR-0056).

The scorer's contract, driven deterministically with a fake provider + a real
in-memory ledger:

- it scores exactly the matured, unscored directional rows, persists the outcome,
  and publishes **exactly one** `recommendation.scored` per newly-scored row,
  strictly after persistence;
- a row whose horizon has not matured is left untouched (still unscored, no
  event);
- one row's scoring blowing up (a malformed ticket) is contained — recorded in
  the heartbeat, the others still score;
- a second tick re-scores nothing (scored rows drop out of the unscored filter).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

from market_analyser.attribution.scoring_job import RecommendationScoringJob
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Bar
from market_analyser.events import Envelope, EventBus
from market_analyser.persistence.advice_ledger_repository import (
    AdviceLedgerEntry,
    AdviceLedgerRepository,
)
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_DAY = timedelta(days=1)
_NOW = _T0 + 10 * _DAY  # well past a 3-bar daily horizon


def _day(n: int) -> datetime:
    return _T0 + n * _DAY


def _bar(n: int, *, high: float, low: float, close: float) -> Bar:
    return Bar(
        symbol="X",
        timeframe="1d",
        event_ts=_day(n),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1_000.0,
        source="test",
    )


# A horizon-3 window where day 1 touches the target (110) before the stop (90).
_TARGET_HIT_BARS = [
    _bar(0, high=100.0, low=100.0, close=100.0),  # the as-of bar (entry = 100)
    _bar(1, high=111.0, low=99.0, close=105.0),
    _bar(2, high=106.0, low=101.0, close=104.0),
    _bar(3, high=109.0, low=103.0, close=108.0),
]


class _FakeProvider:
    def __init__(self, bars: Sequence[Bar]) -> None:
        self._bars = list(bars)

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> list[Bar]:
        return list(self._bars)


def _repo() -> AdviceLedgerRepository:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    return AdviceLedgerRepository(make_session_factory(engine))


def _entry(
    *,
    symbol: str = "DOGE-USD",
    as_of: datetime = _T0,
    stop: float = 90.0,
    horizon_bars: int = 3,
) -> AdviceLedgerEntry:
    return AdviceLedgerEntry(
        symbol=symbol,
        timeframe="1d",
        strategy_id="rsi",
        as_of_bar_ts=as_of,
        horizon_bars=horizon_bars,
        direction="long",
        entry_zone=(99.0, 101.0),
        stop=stop,
        targets=[110.0],
        conviction=0.6,
        forecast_prob=0.62,
        artifact_path=None,
        created_at=as_of,
    )


def _drain(bus: EventBus, thunk: object) -> tuple[int, list[Envelope]]:
    """Subscribe, run the async `thunk`, and drain whatever it published."""

    async def _go() -> tuple[int, list[Envelope]]:
        sub = bus.subscribe()
        try:
            scored = await thunk()  # type: ignore[operator]
            envelopes: list[Envelope] = []
            try:
                while True:
                    envelopes.append(await asyncio.wait_for(sub.next(), timeout=0.2))
            except TimeoutError:
                pass
            return scored, envelopes
        finally:
            sub.close()

    return asyncio.run(_go())


def _job(repo: AdviceLedgerRepository, bus: EventBus) -> RecommendationScoringJob:
    return RecommendationScoringJob(
        ledger_repository=repo,
        # The double serves only get_ohlcv; cast to the Protocol the job's
        # signature wants (the other provider methods are never touched here).
        provider=cast(MarketDataProvider, _FakeProvider(_TARGET_HIT_BARS)),
        event_bus=bus,
        backfill_coordinator=None,
    )


def test_scores_matured_row_persists_outcome_and_publishes_one_event() -> None:
    repo = _repo()
    repo.record(_entry())
    bus = EventBus()
    job = _job(repo, bus)

    scored, envelopes = _drain(bus, lambda: job.tick_once(_NOW))

    assert scored == 1
    # Persisted outcome.
    row = repo.list()[0]
    assert row.outcome_class == "target_hit"
    assert row.realized_r == 1.0
    assert row.directional_correct is True
    assert row.scored_at == _NOW
    # Exactly one event, carrying the scored fact.
    assert len(envelopes) == 1
    envelope = envelopes[0]
    assert envelope.type == "recommendation.scored"
    assert envelope.version == 1
    payload = envelope.payload
    assert payload["symbol"] == "DOGE-USD"
    assert payload["direction"] == "long"
    assert payload["outcome_class"] == "target_hit"
    assert payload["realized_r"] == 1.0
    assert payload["directional_correct"] is True
    # Heartbeat reflects the scored row.
    hb = job.heartbeat()
    assert hb.tick_count == 1
    assert hb.scored_count == 1
    assert hb.row_errors == {}


def test_immature_row_is_left_untouched() -> None:
    repo = _repo()
    # as-of only 1 day before `now`: a 3-day horizon cannot have elapsed.
    repo.record(_entry(as_of=_NOW - 1 * _DAY))
    bus = EventBus()
    job = _job(repo, bus)

    scored, envelopes = _drain(bus, lambda: job.tick_once(_NOW))

    assert scored == 0
    assert envelopes == []
    assert repo.list()[0].outcome_class is None  # still unscored


def test_one_bad_row_is_contained_others_still_score() -> None:
    repo = _repo()
    repo.record(_entry(symbol="GOOD"))
    repo.record(_entry(symbol="BADSTOP", stop=110.0))  # stop on the wrong side for a long
    bus = EventBus()
    job = _job(repo, bus)

    scored, envelopes = _drain(bus, lambda: job.tick_once(_NOW))

    assert scored == 1  # the good row scored; the bad one did not stall it
    assert len(envelopes) == 1
    assert envelopes[0].payload["symbol"] == "GOOD"
    good = repo.get(
        symbol="GOOD", timeframe="1d", strategy_id="rsi", as_of_bar_ts=_T0, horizon_bars=3
    )
    bad = repo.get(
        symbol="BADSTOP", timeframe="1d", strategy_id="rsi", as_of_bar_ts=_T0, horizon_bars=3
    )
    assert good is not None and good.outcome_class == "target_hit"
    assert bad is not None and bad.outcome_class is None  # left unscored
    # The failure is surfaced in the heartbeat, keyed by the bad row.
    errors = job.heartbeat().row_errors
    assert len(errors) == 1
    assert any("BADSTOP" in key for key in errors)
    assert "wrong side of entry" in next(iter(errors.values()))


def test_second_tick_re_scores_nothing() -> None:
    repo = _repo()
    repo.record(_entry())
    bus = EventBus()
    job = _job(repo, bus)

    first, _ = _drain(bus, lambda: job.tick_once(_NOW))
    second, envelopes = _drain(bus, lambda: job.tick_once(_NOW + _DAY))

    assert first == 1
    assert second == 0  # the scored row dropped out of the unscored filter
    assert envelopes == []


def test_flat_rows_are_never_scored() -> None:
    repo = _repo()
    repo.record(
        AdviceLedgerEntry(
            symbol="SPY",
            timeframe="1d",
            strategy_id="rsi",
            as_of_bar_ts=_T0,
            horizon_bars=3,
            direction="flat",
            entry_zone=None,
            stop=None,
            targets=[],
            conviction=0.0,
            forecast_prob=None,
            artifact_path=None,
            created_at=_T0,
        )
    )
    bus = EventBus()
    job = _job(repo, bus)

    scored, envelopes = _drain(bus, lambda: job.tick_once(_NOW))

    assert scored == 0  # a flat call has no ticket to score
    assert envelopes == []
    assert repo.list()[0].outcome_class is None
