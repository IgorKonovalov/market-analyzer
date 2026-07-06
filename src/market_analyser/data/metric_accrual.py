"""Metric-store self-warming job — Plan 0061 phase 1 (ADR-0056, ADR-0051, ADR-0055).

The one place the exogenous metric series get a clock. A lifespan-managed
asyncio task (the ADR-0055 watch-scheduler pattern: started after persistence
is up, cancelled on shutdown, absent when persistence is absent) ticks on a
configurable interval (default hourly — the store's bucket size) and tops up
every series the v2 forecast feature set requires. `EXOGENOUS_SERIES_IDS_V2`
is the duty list's source of truth — no second registry (Plan 0061).

Per series, one tick does:

- **F&G / funding / MVRV** (backfillable upstreams): fetch from the series'
  latest stored timestamp forward — the full history when the series is empty
  (`start=None`, the one-time cold-start backfill), an incremental tail
  otherwise — and upsert through the repository. A re-fetched same-value point
  is a repository no-op; one that *changed* upstream raises
  `MetricPointConflictError` (ADR-0051 immutability), surfaced in the
  heartbeat rather than absorbed. This is the `btc_cycle_snapshot`
  `_refresh_mvrv` shape, promoted to a clock.
- **Open interest** (accrue-only upstream): the Plan 0056 mechanism — a
  one-time upstream-anchored ~30-day seed when the series is empty, then one
  hour-truncated snapshot sample per tick (first write in a bucket wins).
- **Dominance** (accrue-only upstream): one macro fetch through the existing
  CoinGecko write-through, which drops the hourly dominance bucket (and its
  total-mcap sibling rides along on the same call — no extra fetch for it).

Failure containment: one series' upstream dying never blocks the others —
each series is accrued in its own try/except, the error recorded per series
in the `MetricAccrualHeartbeat` (cleared on the next clean pass) and logged
with the series id. `/healthz` exposes the heartbeat beside the watch
scheduler's, so "when did the store last actually warm" is one GET away.

Pacing: series are accrued serially within a tick, in `EXOGENOUS_SERIES_IDS_V2`
order (deterministic, never hash iteration), and every wire call goes through
the adapters' own documented pacing (CoinMetrics 10 req/6s, Binance
pagination) — the cold-start burst is bounded and one-time (Plan 0061 risk
note). If a backfill overruns the tick interval the next tick simply resumes
incrementally; first-write-wins makes an overlap with a tool-call write a
no-op, so no further guard is needed.

`tick_once(now)` is public and clock-injected: tests drive deterministic ticks
against fake sources and a real repository; only `run()`'s loop reads the real
clock. Unlike the watch scheduler's poll loop, `run()` ticks **immediately**
on start, then sleeps — a cold store must start warming at boot, not one
interval later (ADR-0056: the accrue-only series lose un-accrued hours
forever).

All writes go through the existing `MetricPointsRepository` semantics
(first-write-wins buckets, upsert-once) — this module adds no new write
paths, only a clock (ADR-0056 Decision).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from market_analyser.data.metric_series import (
    SERIES_BINANCE_FUNDING_RATE_BTCUSDT,
    SERIES_BINANCE_OPEN_INTEREST_BTCUSDT,
    SERIES_COINGECKO_BTC_DOMINANCE,
    SERIES_COINMETRICS_BTC_MVRV,
    SERIES_FNG_VALUE,
)
from market_analyser.data.sources import MetricSeriesSource
from market_analyser.data.types import MacroContext
from market_analyser.forecast.features import EXOGENOUS_SERIES_IDS_V2
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

# The accrual clock's default cadence: hourly, the store's bucket size for the
# accrue-only series (ADR-0056). `AppConfig.metric_accrual_interval_seconds`
# carries the same default; a config override reaches the job via create_app.
DEFAULT_INTERVAL_SECONDS = 3600

_logger = logging.getLogger(__name__)


class OpenInterestRecorder(Protocol):
    """The slice of the Binance derivatives adapter the open-interest duty
    needs: the one-time upstream-anchored seed plus the hour-bucketed snapshot
    accrual (Plan 0056 phase 3). Both write through the adapter's own wired
    metric store with first-write-wins semantics."""

    def seed_open_interest(self, series_id: str) -> int: ...

    def accrue_open_interest(self, series_id: str) -> int: ...


class MacroContextSource(Protocol):
    """The slice of the CoinGecko adapter the dominance duty needs: one macro
    fetch whose write-through drops the hourly dominance bucket (and the
    total-mcap sibling on the same call — Plan 0055 phase 3)."""

    def fetch_macro_context(self) -> MacroContext: ...


@dataclass(frozen=True)
class MetricAccrualSources:
    """The five-series duty's upstream sources, bundled for the composition
    root (create_app default-constructs the real adapters; tests inject
    fakes). `funding` and `open_interest` are the same object in production —
    both faces of `BinanceDerivativesAdapter` — but stay separate fields so a
    test can fail one duty without the other."""

    fng: MetricSeriesSource
    macro: MacroContextSource
    funding: MetricSeriesSource
    open_interest: OpenInterestRecorder
    mvrv: MetricSeriesSource


class MetricAccrualHeartbeat(BaseModel):
    """The accrual job's liveness + per-series health surface, served on
    `/healthz` beside the watch scheduler's heartbeat (ADR-0056: freshness is
    observable, not discoverable-by-forensics).

    `series_last_success_at` always carries all five duty series (None until a
    series' first clean pass). `series_errors` maps a series id to its most
    recent accrual error (cleared on the next clean pass) — a contained
    failure is visible here, not swallowed. `last_tick_error` carries a
    whole-tick infrastructure failure; the loop keeps ticking regardless.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    running: bool
    last_tick_at: datetime | None
    tick_count: int
    series_last_success_at: dict[str, datetime | None]
    series_errors: dict[str, str]
    last_tick_error: str | None


class MetricAccrualJob:
    """Keeps the five v2 exogenous series warm on a lifespan clock (ADR-0056)."""

    def __init__(
        self,
        *,
        metric_store: MetricPointsRepository,
        sources: MetricAccrualSources,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = metric_store
        self._sources = sources
        self._interval_seconds = interval_seconds
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._running = False
        self._last_tick_at: datetime | None = None
        self._tick_count = 0
        self._series_last_success_at: dict[str, datetime | None] = {
            series_id: None for series_id in EXOGENOUS_SERIES_IDS_V2
        }
        self._series_errors: dict[str, str] = {}
        self._last_tick_error: str | None = None

    def heartbeat(self) -> MetricAccrualHeartbeat:
        return MetricAccrualHeartbeat(
            running=self._running,
            last_tick_at=self._last_tick_at,
            tick_count=self._tick_count,
            series_last_success_at=dict(self._series_last_success_at),
            series_errors=dict(self._series_errors),
            last_tick_error=self._last_tick_error,
        )

    async def run(self) -> None:
        """The lifespan loop: tick, sleep, repeat — until cancelled.

        Tick-first (the deliberate inversion of the watch scheduler's
        sleep-first poll): a cold store must start its one-time backfill and
        the accrue-only clocks at boot, not one interval later — every
        un-accrued dominance/OI hour is permanently lost (ADR-0056). A
        whole-tick failure is recorded in the heartbeat and the loop keeps
        going; only cancellation (app shutdown) stops it.
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

    async def tick_once(self, now: datetime) -> None:
        """Accrue every duty series once, serially, in `EXOGENOUS_SERIES_IDS_V2`
        order. Per-series errors are contained and recorded in the heartbeat;
        one failing upstream never blocks the others (Plan 0061 done-when)."""
        for series_id in EXOGENOUS_SERIES_IDS_V2:
            try:
                await asyncio.to_thread(self._accrue_series, series_id, now)
            except Exception as exc:
                self._series_errors[series_id] = f"{type(exc).__name__}: {exc}"
                _logger.warning("metric accrual failed for %s", series_id, exc_info=True)
            else:
                self._series_last_success_at[series_id] = now
                self._series_errors.pop(series_id, None)
        self._last_tick_at = now
        self._tick_count += 1
        self._last_tick_error = None

    def _accrue_series(self, series_id: str, now: datetime) -> None:
        """One series' accrual action (blocking; run off-thread by tick_once)."""
        as_of_ts = int(now.timestamp())
        if series_id == SERIES_FNG_VALUE:
            self._top_up(self._sources.fng, series_id, as_of_ts)
        elif series_id == SERIES_COINGECKO_BTC_DOMINANCE:
            # The write-through drops the hourly dominance bucket (and the
            # total-mcap sibling rides the same call). A storage failure inside
            # the adapter is best-effort by that adapter's own contract; an
            # upstream failure raises here and is contained per series.
            self._sources.macro.fetch_macro_context()
        elif series_id == SERIES_BINANCE_FUNDING_RATE_BTCUSDT:
            self._top_up(self._sources.funding, series_id, as_of_ts)
        elif series_id == SERIES_BINANCE_OPEN_INTEREST_BTCUSDT:
            if self._store.as_of(series_id, as_of_ts) is None:
                # Empty series: one-time ~30-day seed anchored on upstream's
                # own latest timestamp (Plan 0056), then fall through to the
                # first live sample — "a seed+sample" on the cold tick.
                self._sources.open_interest.seed_open_interest(series_id)
            self._sources.open_interest.accrue_open_interest(series_id)
        elif series_id == SERIES_COINMETRICS_BTC_MVRV:
            self._top_up(self._sources.mvrv, series_id, as_of_ts)
        else:
            # The duty list is EXOGENOUS_SERIES_IDS_V2; a series this dispatch
            # doesn't know is a wiring bug to surface, not skip.
            raise ValueError(f"metric accrual has no duty handler for series {series_id!r}")

    def _top_up(self, source: MetricSeriesSource, series_id: str, as_of_ts: int) -> None:
        """Fetch from the latest stored point forward (`start=None` full
        backfill when empty) and upsert — the `_refresh_mvrv` shape. Fetching
        from the latest point *inclusive* re-fetches one known point, which
        upserts as a no-op; a changed historical value raises
        `MetricPointConflictError` (ADR-0051), contained by the caller."""
        latest = self._store.as_of(series_id, as_of_ts)
        fetched = source.fetch_series(series_id, start=latest.ts if latest is not None else None)
        if fetched:
            self._store.upsert_points(list(fetched))


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "MacroContextSource",
    "MetricAccrualHeartbeat",
    "MetricAccrualJob",
    "MetricAccrualSources",
    "OpenInterestRecorder",
]
