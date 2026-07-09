"""Plan 0060 phase 3 — the watch scheduler (ADR-0055).

Done-when claims pinned here:
(a) with a fake clock + seeded bars, creating a watch and advancing the bars
    across the threshold produces exactly ONE `alert.triggered v1` envelope
    (edge-triggered: the condition staying true on later ticks stays silent),
    with a condition-only payload — the schema test asserts no
    recommendation-shaped field exists;
(c) the heartbeat reflects the last tick, and a deliberately-raised evaluator
    exception is contained: the scheduler keeps evaluating other watches and
    the error surfaces in the heartbeat.

Plus the phase's rate-limit mitigation: per-(symbol, timeframe) fetch
coalescing — one fetch serves all watches on the same series per tick.

Each test drives `tick_once(now)` with an injected clock inside a single
`asyncio.run` scenario (the house pattern for async cores — no event-loop
plugin), so every tick is deterministic and no real time passes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import pytest
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.alerts.scheduler import WatchScheduler
from market_analyser.alerts.types import Watch
from market_analyser.ui_events.buffer import UIEventBuffer
from market_analyser.data.timeframes import bar_duration
from market_analyser.data.types import (
    Bar,
    MacroContext,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)
from market_analyser.events import AlertTriggeredPayloadV1, EventBus
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.watches import (
    AlertsRepository,
    WatchesRepository,
)

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
_CREATED_AT = datetime(2026, 6, 1, tzinfo=UTC)
_DAY = bar_duration("1d")


def _bars(closes: Sequence[float], *, symbol: str = "TEST") -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=_EPOCH + _DAY * i,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=100.0,
            source="fixture",
        )
        for i, price in enumerate(closes)
    ]


class _SeededProvider:
    """Provider double: serves pre-seeded bars per (symbol, timeframe) and
    counts `get_ohlcv` calls (the fetch-coalescing assertion reads it). Can be
    armed to raise, for the fetch-containment case."""

    def __init__(self) -> None:
        self.bars: dict[tuple[str, str], list[Bar]] = {}
        self.calls: list[tuple[str, str]] = []
        self.raise_for: set[tuple[str, str]] = set()

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        self.calls.append((symbol, timeframe))
        if (symbol, timeframe) in self.raise_for:
            raise RuntimeError("upstream unavailable")
        return list(self.bars.get((symbol, timeframe), []))

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, Any],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self,
        symbol: str,
        window: str,
        source: Literal["rss-vader", "stocktwits"] = "rss-vader",
        as_of: datetime | None = None,
    ) -> SentimentSample:
        raise NotImplementedError

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError

    def get_market_sentiment(
        self,
        market: Literal["crypto"],
        window: str = "current",
        as_of: datetime | None = None,
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_macro_context(
        self,
        market: Literal["crypto"] = "crypto",
        as_of: datetime | None = None,
    ) -> MacroContext:
        raise NotImplementedError


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def watches(session_factory: sessionmaker[Session]) -> WatchesRepository:
    return WatchesRepository(session_factory)


@pytest.fixture
def alerts(session_factory: sessionmaker[Session]) -> AlertsRepository:
    return AlertsRepository(session_factory)


@pytest.fixture
def provider() -> _SeededProvider:
    return _SeededProvider()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def ui_buffer() -> UIEventBuffer:
    return UIEventBuffer()


@pytest.fixture
def scheduler(
    watches: WatchesRepository,
    alerts: AlertsRepository,
    provider: _SeededProvider,
    bus: EventBus,
    ui_buffer: UIEventBuffer,
) -> WatchScheduler:
    return WatchScheduler(
        watches_repository=watches,
        alerts_repository=alerts,
        provider=provider,
        event_bus=bus,
        ui_event_buffer=ui_buffer,
    )


def _close_below_watch(
    watches: WatchesRepository,
    *,
    symbol: str = "TEST",
    level: float = 100.0,
    enabled: bool = True,
) -> Watch:
    return watches.create(
        symbol=symbol,
        timeframe="1d",
        kind="indicator_threshold",
        params={"indicator": "close", "operator": "<", "level": level},
        interval_seconds=int(_DAY.total_seconds()),
        enabled=enabled,
        created_at=_CREATED_AT,
    )


class TestEdgeTriggeredDelivery:
    def test_threshold_cross_fires_exactly_one_envelope(
        self,
        scheduler: WatchScheduler,
        watches: WatchesRepository,
        alerts: AlertsRepository,
        provider: _SeededProvider,
        bus: EventBus,
        ui_buffer: UIEventBuffer,
    ) -> None:
        """Seeded cache, fake clock: create a watch, tick with the condition
        false (arms), advance bars across the threshold, tick (fires ONCE),
        tick again with the condition still true (silent)."""
        watch = _close_below_watch(watches, level=100.0)
        subscription = bus.subscribe()

        async def _scenario() -> None:
            provider.bars[("TEST", "1d")] = _bars([105.0, 104.0, 103.0])
            t1 = _EPOCH + _DAY * 4
            assert await scheduler.tick_once(t1) == 0
            loaded = watches.get(watch.id)
            assert loaded is not None and loaded.last_state is False  # armed

            provider.bars[("TEST", "1d")] = _bars([105.0, 104.0, 103.0, 95.0])
            t2 = t1 + _DAY
            assert await scheduler.tick_once(t2) == 1

            provider.bars[("TEST", "1d")] = _bars([105.0, 104.0, 103.0, 95.0, 94.0])
            t3 = t2 + _DAY
            assert await scheduler.tick_once(t3) == 0  # true -> true: silent

            # Exactly one envelope reached the bus subscriber.
            assert subscription.queue.qsize() == 1
            envelope = await subscription.next()
            assert envelope.type == "alert.triggered"
            assert envelope.version == 1
            assert envelope.payload["watch_id"] == watch.id
            assert envelope.payload["symbol"] == "TEST"
            assert envelope.payload["timeframe"] == "1d"
            assert envelope.payload["kind"] == "indicator_threshold"
            assert envelope.payload["condition"] == "close 95 < 100"
            assert envelope.payload["values"] == {"close": 95.0, "level": 100.0}

            # The durable record and the agent-pollable leg both carry the fire.
            history, total = alerts.list(watch_id=watch.id, limit=10)
            assert total == 1
            assert history[0].payload == envelope.payload
            pending = ui_buffer.snapshot()
            assert [e.type for e in pending] == ["alert.triggered"]
            assert pending[0].payload == envelope.payload

            # State re-arms on true -> false, so the next cross fires again.
            provider.bars[("TEST", "1d")] = _bars([105.0] * 6)
            assert await scheduler.tick_once(t3 + _DAY) == 0
            provider.bars[("TEST", "1d")] = _bars([*([105.0] * 6), 90.0])
            assert await scheduler.tick_once(t3 + _DAY * 2) == 1
            assert subscription.queue.qsize() == 1  # the second, distinct fire

        asyncio.run(_scenario())

    def test_interval_gates_reevaluation(
        self,
        scheduler: WatchScheduler,
        watches: WatchesRepository,
        provider: _SeededProvider,
    ) -> None:
        """A watch is not re-evaluated before its interval elapses — a tick an
        hour later does not fetch again for a 1d-interval watch."""
        _close_below_watch(watches)
        provider.bars[("TEST", "1d")] = _bars([105.0])

        async def _scenario() -> None:
            t1 = _EPOCH + _DAY * 2
            await scheduler.tick_once(t1)
            assert provider.calls == [("TEST", "1d")]

            await scheduler.tick_once(t1 + timedelta(hours=1))
            assert provider.calls == [("TEST", "1d")]  # not due yet

            await scheduler.tick_once(t1 + _DAY)
            assert provider.calls == [("TEST", "1d"), ("TEST", "1d")]

        asyncio.run(_scenario())

    def test_disabled_watch_is_not_evaluated(
        self,
        scheduler: WatchScheduler,
        watches: WatchesRepository,
        provider: _SeededProvider,
    ) -> None:
        _close_below_watch(watches, enabled=False)
        provider.bars[("TEST", "1d")] = _bars([95.0, 94.0])
        assert asyncio.run(scheduler.tick_once(_EPOCH + _DAY * 4)) == 0
        assert provider.calls == []


class TestConditionOnlyPayload:
    def test_payload_schema_has_no_recommendation_shaped_fields(self) -> None:
        """The ADR-0029 boundary, pinned at the schema: the payload carries
        identity, timing, and the condition fact — and none of the fields a
        recommendation would need."""
        fields = set(AlertTriggeredPayloadV1.model_fields)
        assert fields == {
            "watch_id",
            "symbol",
            "timeframe",
            "kind",
            "fired_at",
            "condition",
            "values",
        }
        recommendation_shaped = {
            "direction",
            "action",
            "side",
            "conviction",
            "confidence",
            "size",
            "stop",
            "target",
            "recommendation",
        }
        assert fields & recommendation_shaped == set()
        # And unknown fields cannot be smuggled in at publish time.
        assert AlertTriggeredPayloadV1.model_config.get("extra") == "forbid"


class TestContainmentAndHeartbeat:
    def test_evaluator_exception_is_contained_and_surfaced(
        self,
        scheduler: WatchScheduler,
        watches: WatchesRepository,
        provider: _SeededProvider,
        bus: EventBus,
    ) -> None:
        """One watch whose evaluation raises (a strategy_signal naming a
        strategy the registry does not know — the repository stores it
        structurally, only the tool boundary resolves strategies) does not
        stop the tick: the healthy watch on the same series still evaluates
        and later fires, the error lands in the heartbeat keyed by watch id,
        and the scheduler keeps ticking."""
        bad = watches.create(
            symbol="TEST",
            timeframe="1d",
            kind="strategy_signal",
            params={"strategy_id": "gone_strategy", "params": {}},
            interval_seconds=int(_DAY.total_seconds()),
            created_at=_CREATED_AT,
        )
        good = _close_below_watch(watches, level=100.0)
        subscription = bus.subscribe()

        async def _scenario() -> None:
            provider.bars[("TEST", "1d")] = _bars([105.0, 106.0])
            t1 = _EPOCH + _DAY * 3
            await scheduler.tick_once(t1)  # good watch arms; bad watch errors

            provider.bars[("TEST", "1d")] = _bars([105.0, 106.0, 95.0])
            t2 = t1 + _DAY
            fired = await scheduler.tick_once(t2)

            heartbeat = scheduler.heartbeat()
            assert heartbeat.last_tick_at == t2
            assert heartbeat.tick_count == 2
            assert bad.id in heartbeat.watch_errors
            assert "gone_strategy" in heartbeat.watch_errors[bad.id]
            assert good.id not in heartbeat.watch_errors

            # The healthy watch fired despite its neighbour's error.
            assert fired == 1
            assert subscription.queue.qsize() == 1
            loaded = watches.get(good.id)
            assert loaded is not None and loaded.last_state is True

        asyncio.run(_scenario())

    def test_fetch_failure_is_contained_per_series(
        self,
        scheduler: WatchScheduler,
        watches: WatchesRepository,
        provider: _SeededProvider,
    ) -> None:
        """A series whose fetch blows up marks its watches errored; watches on
        other series still evaluate in the same tick."""
        broken = _close_below_watch(watches, symbol="BROKEN")
        healthy = _close_below_watch(watches, symbol="TEST")
        provider.raise_for.add(("BROKEN", "1d"))
        provider.bars[("TEST", "1d")] = _bars([105.0])

        now = _EPOCH + _DAY * 2
        asyncio.run(scheduler.tick_once(now))

        heartbeat = scheduler.heartbeat()
        assert heartbeat.last_tick_at == now
        assert broken.id in heartbeat.watch_errors
        assert "fetch failed" in heartbeat.watch_errors[broken.id]
        assert healthy.id not in heartbeat.watch_errors
        loaded = watches.get(healthy.id)
        assert loaded is not None and loaded.last_state is False  # evaluated

    def test_heartbeat_reflects_ticks(self, scheduler: WatchScheduler) -> None:
        initial = scheduler.heartbeat()
        assert initial.last_tick_at is None
        assert initial.tick_count == 0
        assert initial.running is False

        now = _EPOCH + _DAY
        asyncio.run(scheduler.tick_once(now))
        after = scheduler.heartbeat()
        assert after.last_tick_at == now
        assert after.tick_count == 1
        assert after.watch_errors == {}
        assert after.last_tick_error is None


class TestFetchCoalescing:
    def test_one_fetch_serves_all_watches_on_a_series(
        self,
        scheduler: WatchScheduler,
        watches: WatchesRepository,
        provider: _SeededProvider,
    ) -> None:
        _close_below_watch(watches, level=100.0)
        _close_below_watch(watches, level=50.0)
        _close_below_watch(watches, symbol="OTHER", level=100.0)
        provider.bars[("TEST", "1d")] = _bars([105.0])
        provider.bars[("OTHER", "1d")] = _bars([105.0], symbol="OTHER")

        asyncio.run(scheduler.tick_once(_EPOCH + _DAY * 2))

        # Two distinct series -> exactly two fetches, though three watches ran.
        assert sorted(provider.calls) == [("OTHER", "1d"), ("TEST", "1d")]
