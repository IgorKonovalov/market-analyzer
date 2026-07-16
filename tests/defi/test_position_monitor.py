"""Plan 0099 phase 2 — the in-sidecar DeFi position monitor (ADR-0093).

Done-when claims pinned here:
(a) a watch on an out-of-range LP results — after the dwell — in exactly one
    persisted `DefiPositionAlert`, exactly one `defi.position_alert` event
    published, and one pending-UI-events entry, whose payload carries the
    pool address, `tick_lower`/`tick_upper`, `current_tick`, `in_range=False`
    and `hours_out`;
(b) an in-range position fires nothing;
(c) a transient one-tick excursion fires nothing;
(d) an RPC read failure leaves the persisted dwell state unchanged (does not
    reset the excursion clock);
(e) the payload carries no directive/advice field (ADR-0029, asserted on the
    schema), and event payloads carry a masked wallet, never the full
    address;
plus: an unresolvable pool surfaces as a distinct "unreadable" heartbeat
error (never mistaken for in-range), and config-pinned wallets seed
`source="config"` watches idempotently.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.data.adapters.lp_detail import LpDetailConfigError
from market_analyser.defi.models import DefiPosition, LpPositionDetail, PositionToken
from market_analyser.defi.position_monitor import DefiPositionMonitor
from market_analyser.defi.position_watch import DwellState
from market_analyser.events import DefiPositionAlertPayloadV1, Envelope, EventBus
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.defi_position_watches import (
    DefiPositionAlertsRepository,
    DefiPositionWatchesRepository,
)
from market_analyser.ui_events.buffer import UIEventBuffer

# Synthetic placeholder addresses — never a real wallet (public repo).
WALLET = "0x" + "ab" * 20
POOL = "0x" + "cd" * 20
MASKED_WALLET = f"{WALLET[:6]}…{WALLET[-4:]}"

T0 = datetime(2026, 7, 16, 3, 0, tzinfo=UTC)
DWELL_HOURS = 6.0
CREATED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)

OUT_OF_RANGE = LpPositionDetail(
    tick_lower=-100,
    tick_upper=100,
    current_tick=150,
    in_range=False,
    uncollected_fees=[PositionToken(symbol="USDC", address="0x" + "01" * 20, amount=1.25)],
)
IN_RANGE = LpPositionDetail(
    tick_lower=-100,
    tick_upper=100,
    current_tick=0,
    in_range=True,
    uncollected_fees=[],
)


class _ScriptedDetailSource:
    """Yields one scripted result per `fetch_lp_detail` call; an Exception
    entry is raised instead. `None` script entries script the resolver away."""

    def __init__(self, script: list[LpPositionDetail | Exception]) -> None:
        self.script = script
        self.calls = 0
        self.resolve_result: int | None = 42

    def resolve_univ3_token_id(self, *, chain: str, pool_address: str, owner: str) -> int | None:
        return self.resolve_result

    def fetch_lp_detail(
        self, *, chain: str, pool_address: str, token_id: int | None = None
    ) -> LpPositionDetail:
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(step, Exception):
            raise step
        return step


class _FakeWalletSource:
    def __init__(self, positions: list[DefiPosition]) -> None:
        self.positions = positions
        self.calls = 0

    def fetch_positions(self, address: str) -> list[DefiPosition]:
        self.calls += 1
        return self.positions


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def watches(session_factory: sessionmaker[Session]) -> DefiPositionWatchesRepository:
    return DefiPositionWatchesRepository(session_factory)


@pytest.fixture
def alerts(session_factory: sessionmaker[Session]) -> DefiPositionAlertsRepository:
    return DefiPositionAlertsRepository(session_factory)


def _make_monitor(
    watches: DefiPositionWatchesRepository,
    alerts: DefiPositionAlertsRepository,
    source: _ScriptedDetailSource | None,
    *,
    wallet_source: _FakeWalletSource | None = None,
    pinned_wallets: tuple[str, ...] = (),
) -> tuple[DefiPositionMonitor, asyncio.Queue[Envelope], UIEventBuffer]:
    bus = EventBus()
    sub = bus.subscribe()
    buffer = UIEventBuffer()
    monitor = DefiPositionMonitor(
        watches_repository=watches,
        alerts_repository=alerts,
        lp_detail_source=source,
        event_bus=bus,
        ui_event_buffer=buffer,
        wallet_positions_source=wallet_source,
        pinned_wallets=pinned_wallets,
        read_spacing_seconds=0.0,
    )
    return monitor, sub.queue, buffer


def _create_watch(watches: DefiPositionWatchesRepository) -> int:
    watch = watches.create(
        wallet=WALLET,
        chain="base",
        pool_address=POOL,
        nft_token_id=None,
        dwell_hours=DWELL_HOURS,
        interval_seconds=900,
        source="agent",
        created_at=CREATED_AT,
    )
    return watch.id


def _drain(queue: asyncio.Queue[Envelope]) -> list[Envelope]:
    drained: list[Envelope] = []
    while not queue.empty():
        drained.append(queue.get_nowait())
    return drained


def _tick_hours(monitor: DefiPositionMonitor, hours: list[float]) -> list[int]:
    """Drive tick_once at T0 + each offset (hours); returns per-tick fire counts."""

    async def run() -> list[int]:
        return [await monitor.tick_once(T0 + timedelta(hours=h)) for h in hours]

    return asyncio.run(run())


class TestFiresOnceAfterDwell:
    def test_out_of_range_fires_exactly_once_with_full_delivery(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch_id = _create_watch(watches)
        source = _ScriptedDetailSource([OUT_OF_RANGE])
        monitor, queue, buffer = _make_monitor(watches, alerts, source)

        # 15-min cadence from T0 to T0+7h, out of range throughout.
        fires = _tick_hours(monitor, [h / 4 for h in range(0, 29)])
        assert sum(fires) == 1

        # Exactly one persisted alert, carrying the condition facts.
        page, total = alerts.list(limit=10)
        assert total == 1
        alert = page[0]
        assert alert.watch_id == watch_id
        assert alert.pool_address == POOL
        assert alert.tick_lower == -100
        assert alert.tick_upper == 100
        assert alert.current_tick == 150
        assert alert.in_range is False
        assert alert.hours_out == pytest.approx(DWELL_HOURS)
        assert alert.out_since == T0
        assert alert.uncollected_fees is not None

        # Exactly one bus event with the same facts and a MASKED wallet.
        events = _drain(queue)
        assert [e.type for e in events] == ["defi.position_alert"]
        payload = events[0].payload
        assert payload["pool_address"] == POOL
        assert payload["tick_lower"] == -100
        assert payload["tick_upper"] == 100
        assert payload["current_tick"] == 150
        assert payload["in_range"] is False
        assert payload["hours_out"] == pytest.approx(DWELL_HOURS)
        assert payload["wallet"] == MASKED_WALLET
        assert WALLET not in str(payload)

        # Exactly one pending-UI-events entry (the agent-poll leg).
        pending = buffer.snapshot()
        assert [e.type for e in pending] == ["defi.position_alert"]
        assert pending[0].payload == payload

    def test_no_refire_while_still_out_after_alert(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        _create_watch(watches)
        monitor, _queue, _buffer = _make_monitor(
            watches, alerts, _ScriptedDetailSource([OUT_OF_RANGE])
        )
        fires = _tick_hours(monitor, [0.0, DWELL_HOURS, DWELL_HOURS + 5, DWELL_HOURS + 10])
        assert fires == [0, 1, 0, 0]


class TestNoFireCases:
    def test_in_range_position_fires_nothing(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        _create_watch(watches)
        monitor, queue, buffer = _make_monitor(watches, alerts, _ScriptedDetailSource([IN_RANGE]))
        fires = _tick_hours(monitor, [0.0, 6.0, 12.0, 24.0])
        assert sum(fires) == 0
        assert alerts.list(limit=10)[1] == 0
        assert _drain(queue) == []
        assert buffer.snapshot() == []

    def test_transient_one_tick_excursion_fires_nothing(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        _create_watch(watches)
        source = _ScriptedDetailSource([OUT_OF_RANGE, IN_RANGE, IN_RANGE, IN_RANGE])
        monitor, queue, _buffer = _make_monitor(watches, alerts, source)
        fires = _tick_hours(monitor, [0.0, 0.25, 6.5, 12.0])
        assert sum(fires) == 0
        assert _drain(queue) == []


class TestReadFailureContainment:
    def test_rpc_failure_leaves_dwell_state_unchanged(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch_id = _create_watch(watches)
        source = _ScriptedDetailSource([OUT_OF_RANGE, RuntimeError("rpc timeout"), OUT_OF_RANGE])
        monitor, _queue, _buffer = _make_monitor(watches, alerts, source)

        # T0: excursion starts. T0+3h: read fails — state must NOT reset.
        fires = _tick_hours(monitor, [0.0, 3.0])
        assert fires == [0, 0]
        stored = watches.get(watch_id)
        assert stored is not None
        assert stored.dwell_state == DwellState(out_since=T0, fired=False)
        assert "read failed" in monitor.heartbeat().watch_errors[watch_id]

        # T0+6h: read recovers — the dwell measured from the ORIGINAL T0 fires.
        fires = _tick_hours(monitor, [DWELL_HOURS])
        assert fires == [1]

    def test_unreadable_pool_surfaces_distinctly_and_never_evaluates(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch_id = _create_watch(watches)
        source = _ScriptedDetailSource([OUT_OF_RANGE])
        source.resolve_result = None  # no CL position resolves (e.g. a v2 pool)
        monitor, _queue, _buffer = _make_monitor(watches, alerts, source)
        fires = _tick_hours(monitor, [0.0, 24.0])
        assert sum(fires) == 0
        error = monitor.heartbeat().watch_errors[watch_id]
        assert "unreadable" in error
        assert "never evaluated" in error
        stored = watches.get(watch_id)
        assert stored is not None
        assert stored.dwell_state == DwellState()  # untouched, not "in range"

    def test_unconfigured_source_is_a_typed_heartbeat_error(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch_id = _create_watch(watches)
        source = _ScriptedDetailSource([LpDetailConfigError("no RPC URL for base")])
        monitor, _queue, _buffer = _make_monitor(watches, alerts, source)
        assert sum(_tick_hours(monitor, [0.0])) == 0
        assert "unconfigured" in monitor.heartbeat().watch_errors[watch_id]

    def test_heartbeat_ticks_and_clears_watch_error_on_recovery(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch_id = _create_watch(watches)
        source = _ScriptedDetailSource([RuntimeError("boom"), IN_RANGE])
        monitor, _queue, _buffer = _make_monitor(watches, alerts, source)
        _tick_hours(monitor, [0.0])
        assert watch_id in monitor.heartbeat().watch_errors
        _tick_hours(monitor, [0.25])
        heartbeat = monitor.heartbeat()
        assert watch_id not in heartbeat.watch_errors
        assert heartbeat.tick_count == 2
        assert heartbeat.last_tick_at == T0 + timedelta(minutes=15)


class TestPayloadBoundary:
    def test_payload_schema_carries_no_directive_vocabulary(self) -> None:
        # ADR-0029: the wire schema itself must not admit advice-shaped fields.
        fields = set(DefiPositionAlertPayloadV1.model_fields)
        assert fields.isdisjoint(
            {"action", "advice", "direction", "recommendation", "side", "size", "conviction"}
        )

    def test_payload_rejects_extra_fields(self) -> None:
        with pytest.raises(Exception, match=r"[Ee]xtra"):
            DefiPositionAlertPayloadV1(
                watch_id=1,
                wallet=MASKED_WALLET,
                chain="base",
                pool_address=POOL,
                nft_token_id=None,
                fired_at=T0,
                out_since=T0,
                hours_out=6.0,
                tick_lower=-100,
                tick_upper=100,
                current_tick=150,
                in_range=False,
                uncollected_fees=None,
                recommendation="recenter",  # type: ignore[call-arg]
            )


class TestConfigWalletSeeding:
    def _lp_position(self, pool: str) -> DefiPosition:
        return DefiPosition(
            position_id=f"base:aerodrome:{pool}",
            chain="base",
            protocol="aerodrome",
            kind="lp",
            tokens=[PositionToken(symbol="WETH", address="0x" + "02" * 20, amount=1.0)],
            usd_value=1000.0,
            pool_address=pool,
        )

    def test_seed_creates_config_watches_idempotently(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        pool_b = "0x" + "ef" * 20
        wallet_source = _FakeWalletSource([self._lp_position(POOL), self._lp_position(pool_b)])
        monitor, _queue, _buffer = _make_monitor(
            watches, alerts, None, wallet_source=wallet_source, pinned_wallets=(WALLET,)
        )
        created = asyncio.run(monitor.seed_config_watches())
        assert created == 2
        seeded = watches.list()
        assert {w.pool_address for w in seeded} == {POOL, pool_b}
        assert all(w.source == "config" for w in seeded)

        # Second seed (a restart) creates nothing new.
        assert asyncio.run(monitor.seed_config_watches()) == 0
        assert len(watches.list()) == 2

    def test_pinned_wallets_without_wallet_source_is_a_heartbeat_error(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        monitor, _queue, _buffer = _make_monitor(
            watches, alerts, None, wallet_source=None, pinned_wallets=(WALLET,)
        )
        assert asyncio.run(monitor.seed_config_watches()) == 0
        seed_error = monitor.heartbeat().seed_error
        assert seed_error is not None
        assert "not seeded" in seed_error
