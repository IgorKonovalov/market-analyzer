"""In-sidecar watch scheduler (Plan 0060 phase 3, ADR-0055).

The one place in the alerting loop where the wall clock lives. A
lifespan-managed asyncio task ticks the enabled watches at their per-watch
intervals; each tick:

1. lists the enabled watches (repository read, off-thread);
2. groups the *due* ones by ``(symbol, timeframe)`` and fetches bars **once
   per group** (per-symbol fetch coalescing — upstream rate-limit exposure
   scales with distinct symbols, not watch count), through the same
   coordinator/provider path the on-demand tools use (a watch evaluation
   never fetches beyond what the existing backfill rules allow);
3. runs the pure phase-2 evaluation per watch, folds the result through the
   edge reducer against the persisted ``last_state``, and persists the new
   state;
4. on a false→true edge: appends the alert row, publishes one
   ``alert.triggered v1`` envelope on the EventBus (the SSE leg, ADR-0017),
   and appends the same payload to the UI-event buffer so the agent's
   ``get_pending_ui_events`` poll sees it (the pending-events leg, ADR-0021).

Failure containment: one watch's evaluation error — bad params for a renamed
strategy, an upstream fetch blowing up, anything — is caught, recorded in the
heartbeat's per-watch error map, and never stops the tick for other watches.
The heartbeat (`SchedulerHeartbeat`) is how a wedged or erroring scheduler
degrades loudly instead of quietly: `/healthz` exposes it (the existing
health surface), so "when did alerting last actually tick" is one GET away.

`tick_once(now)` is public and clock-injected: the integration tests drive
deterministic ticks with a fake clock and seeded bars; only `run()`'s loop
reads the real clock.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from market_analyser.alerts.evaluate import evaluate_watch_detail, should_fire
from market_analyser.alerts.types import Watch
from market_analyser.api.ui_events import UIEventEnvelope
from market_analyser.api.ui_events.buffer import UIEventBuffer
from market_analyser.data.backfill import BackfillCoordinator
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import timeframe_spec
from market_analyser.data.types import Bar
from market_analyser.events import AlertTriggeredPayloadV1, EventBus
from market_analyser.persistence.repositories.watches import (
    AlertsRepository,
    WatchesRepository,
)

# Bars of trailing history fetched per evaluation — enough for every indicator
# the watch vocabulary admits to warm up (the slowest, ADX(14)/RSI(14) with
# smoothing, is comfortably defined by ~100 bars) and for a strategy's
# generate_signals to have context. One constant for all kinds keeps the fetch
# coalescer simple; raising it is a latency/rate-limit trade, not a schema one.
WARMUP_BARS = 200

# The run() loop's sleep granularity: how often the scheduler *checks* for due
# watches, not how often watches evaluate (that is per-watch interval_seconds).
DEFAULT_POLL_SECONDS = 5.0


class SchedulerHeartbeat(BaseModel):
    """The scheduler's liveness + error surface, served on `/healthz`.

    `last_tick_at` is `None` until the first tick completes. `watch_errors`
    maps a watch id to its most recent evaluation error (cleared on the next
    clean evaluation) — a contained failure is visible here, not swallowed.
    `last_tick_error` carries a whole-tick failure (e.g. the repository read
    itself failing); the loop keeps ticking regardless.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    running: bool
    last_tick_at: datetime | None
    tick_count: int
    watch_errors: dict[int, str]
    last_tick_error: str | None


class WatchScheduler:
    """Ticks enabled watches, fires edge-triggered condition-only alerts."""

    def __init__(
        self,
        *,
        watches_repository: WatchesRepository,
        alerts_repository: AlertsRepository,
        provider: MarketDataProvider,
        event_bus: EventBus,
        ui_event_buffer: UIEventBuffer,
        backfill_coordinator: BackfillCoordinator | None = None,
        clock: Callable[[], datetime] | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> None:
        self._watches = watches_repository
        self._alerts = alerts_repository
        self._provider = provider
        self._coordinator = backfill_coordinator
        self._bus = event_bus
        self._ui_events = ui_event_buffer
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._poll_seconds = poll_seconds
        self._next_due: dict[int, datetime] = {}
        self._running = False
        self._last_tick_at: datetime | None = None
        self._tick_count = 0
        self._watch_errors: dict[int, str] = {}
        self._last_tick_error: str | None = None

    def heartbeat(self) -> SchedulerHeartbeat:
        return SchedulerHeartbeat(
            running=self._running,
            last_tick_at=self._last_tick_at,
            tick_count=self._tick_count,
            watch_errors=dict(self._watch_errors),
            last_tick_error=self._last_tick_error,
        )

    async def run(self) -> None:
        """The lifespan loop: sleep, tick, repeat — until cancelled.

        A whole-tick failure is recorded in the heartbeat and the loop keeps
        going; only cancellation (app shutdown) stops it.
        """
        self._running = True
        try:
            while True:
                await asyncio.sleep(self._poll_seconds)
                try:
                    await self.tick_once(self._clock())
                except Exception as exc:
                    self._last_tick_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._running = False

    async def tick_once(self, now: datetime) -> int:
        """Evaluate every enabled watch that is due at `now`. Returns the
        number of alerts fired. Per-watch errors are contained and recorded;
        this method raises only for infrastructure failures outside any one
        watch (contained in turn by `run()`'s loop)."""
        watches = await asyncio.to_thread(self._watches.list, enabled_only=True)
        due = [w for w in watches if self._next_due.get(w.id, now) <= now]

        fired = 0
        by_series: dict[tuple[str, str], list[Watch]] = defaultdict(list)
        for watch in due:
            by_series[(watch.symbol, watch.timeframe)].append(watch)

        for (symbol, timeframe), group in by_series.items():
            try:
                bars = await self._fetch_bars(symbol, timeframe, now)
            except Exception as exc:
                message = f"fetch failed: {type(exc).__name__}: {exc}"
                for watch in group:
                    self._watch_errors[watch.id] = message
                    self._next_due[watch.id] = now + timedelta(seconds=watch.interval_seconds)
                continue
            for watch in group:
                fired += await self._evaluate_one(watch, bars, now)
                self._next_due[watch.id] = now + timedelta(seconds=watch.interval_seconds)

        self._last_tick_at = now
        self._tick_count += 1
        self._last_tick_error = None
        return fired

    async def _fetch_bars(self, symbol: str, timeframe: str, now: datetime) -> Sequence[Bar]:
        """One coalesced fetch per (symbol, timeframe) per tick — the same
        cached-bars path as the on-demand tools, offloaded so the blocking
        fetch never stalls the event loop."""
        range_start = now - timeframe_spec(timeframe).bar_duration * WARMUP_BARS
        if self._coordinator is not None:
            result = await asyncio.to_thread(
                self._coordinator.get_ohlcv_with_status, symbol, timeframe, range_start, now
            )
            return list(result.bars)
        return list(
            await asyncio.to_thread(self._provider.get_ohlcv, symbol, timeframe, range_start, now)
        )

    async def _evaluate_one(self, watch: Watch, bars: Sequence[Bar], now: datetime) -> int:
        """Evaluate one watch against pre-fetched bars; fire on the edge.
        Returns 1 if an alert fired, 0 otherwise. Errors are contained."""
        try:
            detail = evaluate_watch_detail(watch, bars, now=now)
            fire = should_fire(watch.last_state, detail.result)
            await asyncio.to_thread(
                self._watches.set_last_state, watch.id, last_state=detail.result
            )
            if not fire:
                self._watch_errors.pop(watch.id, None)
                return 0
            payload = AlertTriggeredPayloadV1(
                watch_id=watch.id,
                symbol=watch.symbol,
                timeframe=watch.timeframe,
                kind=watch.kind,
                fired_at=now,
                condition=detail.condition,
                values=detail.values,
            )
            # Persist first: history is the durable record; the two delivery
            # legs below are best-effort live fan-out on top of it.
            await asyncio.to_thread(
                self._alerts.insert,
                watch_id=watch.id,
                fired_at=now,
                payload=payload.model_dump(mode="json"),
            )
            envelope = self._bus.publish("alert.triggered", payload)
            # The agent-pollable pending-events leg (ADR-0021): the same
            # payload, wrapped in the buffer's envelope shape. Appended
            # directly (not via `POST /ui_events`' closed ui.* vocabulary, and
            # not gated by agent mode — the toggle guards *UI gesture*
            # visibility; an alert is sidecar-originated, and "what fired
            # while I was away" must survive the toggle being off).
            self._ui_events.append(
                UIEventEnvelope(
                    event_id=str(uuid.uuid4()),
                    type="alert.triggered",
                    version=AlertTriggeredPayloadV1.VERSION,
                    ts=envelope.ts,
                    payload=envelope.payload,
                )
            )
            self._watch_errors.pop(watch.id, None)
            return 1
        except Exception as exc:
            self._watch_errors[watch.id] = f"{type(exc).__name__}: {exc}"
            return 0


__all__ = [
    "DEFAULT_POLL_SECONDS",
    "WARMUP_BARS",
    "SchedulerHeartbeat",
    "WatchScheduler",
]
