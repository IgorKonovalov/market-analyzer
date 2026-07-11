"""Scheduled recommendation scorer — Plan 0080 phase 3 (ADR-0075, ADR-0056).

The clock for the advisor track record. A lifespan-managed asyncio task (the
ADR-0056 pattern: constructed only when persistence is wired and the flag is on,
tick-first boot, cancelled on shutdown) finds matured, unscored ledger rows,
fetches their realized bars through the same coordinator/provider path the
on-demand tools use, scores each path-dependently via the pure phase-2 engine,
persists the outcome, and publishes one ``recommendation.scored v1`` per
newly-scored row.

Failure containment mirrors the watch scheduler and metric-accrual jobs: one
row's scoring blowing up — a malformed ticket, a fetch failing — is caught,
recorded per row in the heartbeat, and never stalls the others. ``/healthz``
exposes the heartbeat so a wedged scorer degrades loudly.

Only *directional* rows are scored (a flat "no actionable edge" call has no
ticket to simulate); they are read via the repository's `directional=True,
scored=False` filter. A row whose horizon has not truly matured (the authoritative
no-lookahead check lives in the pure engine) scores `pending` and is left for a
later tick — never a partial peek.

`tick_once(now)` is public and clock-injected: tests drive deterministic ticks
against a fake provider + real repository; only `run()`'s loop reads the real
clock. Tick-first on boot (the ADR-0056 inversion): a fresh process with a
backlog of matured-but-unscored calls must start scoring at boot, not one
interval later.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from market_analyser.attribution.scoring import score_recommendation
from market_analyser.data.backfill import BackfillCoordinator
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import timeframe_spec
from market_analyser.data.types import Bar
from market_analyser.events import EventBus, RecommendationScoredPayloadV1
from market_analyser.persistence.advice_ledger_repository import (
    AdviceLedgerEntry,
    AdviceLedgerRepository,
)

# The scorer's default cadence. Recommendations mature over their horizon (bars),
# so there is no value ticking faster than the coarsest useful grain — hourly
# matches the metric-accrual clock and clears any backlog within a few ticks.
DEFAULT_INTERVAL_SECONDS = 3600

_logger = logging.getLogger(__name__)


def _row_key(entry: AdviceLedgerEntry) -> str:
    """A stable per-row key for the heartbeat error map."""
    return "|".join(
        (
            entry.symbol,
            entry.timeframe,
            entry.strategy_id,
            entry.as_of_bar_ts.isoformat(),
            str(entry.horizon_bars),
        )
    )


class ScoringHeartbeat(BaseModel):
    """The scorer's liveness + error surface, served on `/healthz` beside the
    other lifespan jobs (ADR-0056: freshness is observable, not
    discoverable-by-forensics).

    `row_errors` maps a row key to its most recent scoring error (cleared when
    that row later scores cleanly). `last_tick_error` carries a whole-tick
    infrastructure failure; the loop keeps ticking regardless."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    running: bool
    last_tick_at: datetime | None
    tick_count: int
    scored_count: int
    row_errors: dict[str, str]
    last_tick_error: str | None


class RecommendationScoringJob:
    """Scores matured advisory recommendations on a lifespan clock (ADR-0075)."""

    def __init__(
        self,
        *,
        ledger_repository: AdviceLedgerRepository,
        provider: MarketDataProvider,
        event_bus: EventBus,
        backfill_coordinator: BackfillCoordinator | None = None,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._ledger = ledger_repository
        self._provider = provider
        self._bus = event_bus
        self._coordinator = backfill_coordinator
        self._interval_seconds = interval_seconds
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._running = False
        self._last_tick_at: datetime | None = None
        self._tick_count = 0
        self._scored_count = 0
        self._row_errors: dict[str, str] = {}
        self._last_tick_error: str | None = None

    def heartbeat(self) -> ScoringHeartbeat:
        return ScoringHeartbeat(
            running=self._running,
            last_tick_at=self._last_tick_at,
            tick_count=self._tick_count,
            scored_count=self._scored_count,
            row_errors=dict(self._row_errors),
            last_tick_error=self._last_tick_error,
        )

    async def run(self) -> None:
        """The lifespan loop: tick, sleep, repeat — until cancelled.

        Tick-first (the ADR-0056 inversion): a fresh process must start scoring
        any matured backlog at boot, not one interval later. A whole-tick failure
        is recorded in the heartbeat and the loop keeps going; only cancellation
        (app shutdown) stops it.
        """
        self._running = True
        try:
            while True:
                try:
                    await self.tick_once(self._clock())
                except Exception as exc:
                    self._last_tick_error = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(self._interval_seconds)
        finally:
            self._running = False

    async def tick_once(self, now: datetime) -> int:
        """Score every matured, unscored directional row once. Returns the number
        of rows newly scored. Per-row errors are contained and recorded; this
        method raises only for infrastructure failures outside any one row
        (contained in turn by `run()`'s loop)."""
        unscored = await asyncio.to_thread(self._ledger.list, directional=True, scored=False)
        # Coarse wall-clock maturity: skip rows whose horizon cannot have elapsed
        # yet (calendar-time lower bound). The authoritative, gap-aware, closed-bar
        # maturity check is the pure engine's — this only avoids fetching for
        # clearly-too-fresh calls.
        candidates = [entry for entry in unscored if self._maybe_matured(entry, now)]

        by_series: dict[tuple[str, str], list[AdviceLedgerEntry]] = defaultdict(list)
        for entry in candidates:
            by_series[(entry.symbol, entry.timeframe)].append(entry)

        scored = 0
        for (symbol, timeframe), group in by_series.items():
            earliest = min(entry.as_of_bar_ts for entry in group)
            try:
                bars = await self._fetch_bars(symbol, timeframe, earliest, now)
            except Exception as exc:
                message = f"fetch failed: {type(exc).__name__}: {exc}"
                for entry in group:
                    self._row_errors[_row_key(entry)] = message
                continue
            for entry in group:
                scored += await self._score_one(entry, bars, now)

        self._last_tick_at = now
        self._tick_count += 1
        self._scored_count += scored
        self._last_tick_error = None
        return scored

    def _maybe_matured(self, entry: AdviceLedgerEntry, now: datetime) -> bool:
        horizon_end = (
            entry.as_of_bar_ts + entry.horizon_bars * timeframe_spec(entry.timeframe).bar_duration
        )
        return horizon_end <= now

    async def _fetch_bars(
        self, symbol: str, timeframe: str, start: datetime, now: datetime
    ) -> Sequence[Bar]:
        """Fetch bars from the earliest as-of bar in the group through `now` — the
        same cached-bars path the on-demand tools use, offloaded so the blocking
        fetch never stalls the event loop. `start` is the as-of bar itself (its
        close is the notional entry), so no warm-up lookback is needed."""
        if self._coordinator is not None:
            result = await asyncio.to_thread(
                self._coordinator.get_ohlcv_with_status, symbol, timeframe, start, now
            )
            return list(result.bars)
        return list(
            await asyncio.to_thread(self._provider.get_ohlcv, symbol, timeframe, start, now)
        )

    async def _score_one(self, entry: AdviceLedgerEntry, bars: Sequence[Bar], now: datetime) -> int:
        """Score one row against pre-fetched bars, persist the outcome, and — only
        for a newly-scored (non-pending) row — publish exactly one
        `recommendation.scored` strictly after persistence. Returns 1 if scored,
        0 otherwise. Errors are contained and recorded per row."""
        try:
            outcome = score_recommendation(entry, bars, now=now)
            if outcome.outcome_class == "pending":
                # Matured in calendar time but the closed horizon bars are not all
                # present yet — leave it unscored for a later tick. Not an error.
                self._row_errors.pop(_row_key(entry), None)
                return 0

            # Non-pending → every measurement is populated (the engine's contract).
            assert outcome.realized_return is not None
            assert outcome.realized_r is not None
            assert outcome.directional_correct is not None
            assert outcome.scored_at is not None
            direction = entry.direction
            assert direction != "flat"  # list(directional=True) already excludes flats

            await asyncio.to_thread(
                self._ledger.apply_outcome,
                symbol=entry.symbol,
                timeframe=entry.timeframe,
                strategy_id=entry.strategy_id,
                as_of_bar_ts=entry.as_of_bar_ts,
                horizon_bars=entry.horizon_bars,
                outcome_class=outcome.outcome_class,
                realized_return=outcome.realized_return,
                realized_r=outcome.realized_r,
                directional_correct=outcome.directional_correct,
                scored_at=outcome.scored_at,
            )
            # Publish AFTER persistence — the ledger is the durable record; the
            # event is best-effort live fan-out on top of it (exactly one per
            # newly-scored row).
            self._bus.publish(
                "recommendation.scored",
                RecommendationScoredPayloadV1(
                    symbol=entry.symbol,
                    timeframe=entry.timeframe,
                    strategy_id=entry.strategy_id,
                    direction=direction,
                    as_of_bar_ts=entry.as_of_bar_ts,
                    horizon_bars=entry.horizon_bars,
                    conviction=entry.conviction,
                    forecast_prob=entry.forecast_prob,
                    outcome_class=outcome.outcome_class,
                    realized_return=outcome.realized_return,
                    realized_r=outcome.realized_r,
                    directional_correct=outcome.directional_correct,
                    scored_at=outcome.scored_at,
                ),
            )
            self._row_errors.pop(_row_key(entry), None)
            return 1
        except Exception as exc:
            self._row_errors[_row_key(entry)] = f"{type(exc).__name__}: {exc}"
            _logger.warning("recommendation scoring failed for %s", _row_key(entry), exc_info=True)
            return 0


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "RecommendationScoringJob",
    "ScoringHeartbeat",
]
