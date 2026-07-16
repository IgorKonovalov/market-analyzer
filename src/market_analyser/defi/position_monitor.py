"""In-sidecar DeFi LP position monitor (Plan 0099 phase 2, ADR-0093).

The 4th lifespan asyncio loop, sibling to `alerts/scheduler.py`,
`defi/scan_job.py`, and `defi/pnl_job.py` — the one place the position-watch
subsystem reads the wall clock. Each tick it re-reads every due watch's live
on-chain state and drives the pure phase-1 dwell reducer
(`defi/position_watch.py::evaluate_position_dwell`); on a fire it persists
the alert, publishes one `defi.position_alert v1` envelope on the EventBus
(the SSE leg, ADR-0017), and appends the same payload to the UI-event buffer
so the agent's `get_pending_ui_events` poll sees it (ADR-0021).

**The read path.** Evaluation goes through the same `LpPositionDetailSource`
the enrichment step (`enrich_lp_positions`) uses — `resolve_univ3_token_id`
(when the watch pins no NFT id) then `fetch_lp_detail` — the identical
best-effort RPC deep-read behind `scan_wallet`, without re-running wallet
discovery every tick (a Zerion duty cycle is exactly the rate-limit exposure
ADR-0093 scopes to the RPC). Reads within a tick are serialized with the
enrichment module's spacing discipline.

Failure containment (ADR-0093):

- a failed RPC read leaves the watch's persisted dwell state **untouched**
  (never reset) — the error is recorded in the heartbeat's per-watch map;
- a pool the source cannot deep-read (no CL position resolves) is surfaced
  as a distinct "unreadable — never evaluated" error, so a silent non-fire
  is never mistaken for "in range" (plan risk #2);
- one watch's failure never stops the tick for the others, and a whole-tick
  failure is recorded and the loop keeps going.

Watches come from two sources behind the one repository: agent-created (the
MCP tools) and **config-pinned wallets** — at startup `seed_config_watches`
discovers each pinned wallet's LP positions once (via the discovery source)
and creates a `source="config"` watch per LP pool that has none yet.
Seeding is best-effort and idempotent; its failure is a heartbeat field,
never a startup crash.

Alert payloads are condition facts only (ADR-0029 boundary) and carry a
**masked** wallet — full addresses never ride an event payload. The
persisted `DefiPositionAlert` row keeps the full wallet (local DB, the
agent-side `list_position_alerts` needs the real key).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from market_analyser.data.adapters.lp_detail import LpDetailConfigError
from market_analyser.data.sources import LpPositionDetailSource, WalletPositionsSource
from market_analyser.defi.discovery import mask_wallet
from market_analyser.defi.models import LpPositionDetail
from market_analyser.defi.position_watch import (
    DefiPositionWatch,
    evaluate_position_dwell,
    validate_evm_address,
)
from market_analyser.events import DefiPositionAlertPayloadV1, EventBus
from market_analyser.events.payloads import PositionAlertFeeV1
from market_analyser.persistence.repositories.defi_position_watches import (
    DefiPositionAlertsRepository,
    DefiPositionWatchesRepository,
)
from market_analyser.ui_events import UIEventEnvelope
from market_analyser.ui_events.buffer import UIEventBuffer

# The run() loop's sleep granularity: how often the monitor *checks* for due
# watches, not how often watches evaluate (that is per-watch interval_seconds,
# default 15 min — LP ranges move on the timescale of hours; ADR-0093).
DEFAULT_POLL_SECONDS = 5.0

# Spacing between consecutive per-watch RPC reads within one tick — the same
# deliberate cadence `defi/enrichment.py` pins for the free-tier RPC limits.
DEFAULT_READ_SPACING_SECONDS = 1.1


class PositionMonitorHeartbeat(BaseModel):
    """The monitor's liveness + error surface, served on `/healthz`.

    `watch_errors` maps a watch id to its most recent evaluation error
    (cleared on the next clean evaluation); an "unreadable" entry means the
    pool cannot be deep-read and the watch has **never evaluated** — distinct
    from "in range" (plan risk #2). `seed_error` carries a failed
    config-wallet seeding; `last_tick_error` a whole-tick failure. The loop
    keeps ticking regardless.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    running: bool
    last_tick_at: datetime | None
    tick_count: int
    watch_errors: dict[int, str]
    seed_error: str | None
    last_tick_error: str | None


class DefiPositionMonitor:
    """Ticks enabled position watches; fires dwell-qualified, condition-only
    out-of-range alerts (ADR-0093)."""

    def __init__(
        self,
        *,
        watches_repository: DefiPositionWatchesRepository,
        alerts_repository: DefiPositionAlertsRepository,
        lp_detail_source: LpPositionDetailSource | None,
        event_bus: EventBus,
        ui_event_buffer: UIEventBuffer,
        wallet_positions_source: WalletPositionsSource | None = None,
        pinned_wallets: Sequence[str] = (),
        clock: Callable[[], datetime] | None = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        read_spacing_seconds: float = DEFAULT_READ_SPACING_SECONDS,
    ) -> None:
        self._watches = watches_repository
        self._alerts = alerts_repository
        self._lp_detail_source = lp_detail_source
        self._bus = event_bus
        self._ui_events = ui_event_buffer
        self._wallet_source = wallet_positions_source
        self._pinned_wallets = tuple(pinned_wallets)
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._poll_seconds = poll_seconds
        self._read_spacing_seconds = read_spacing_seconds
        self._next_due: dict[int, datetime] = {}
        self._running = False
        self._last_tick_at: datetime | None = None
        self._tick_count = 0
        self._watch_errors: dict[int, str] = {}
        self._seed_error: str | None = None
        self._last_tick_error: str | None = None

    def heartbeat(self) -> PositionMonitorHeartbeat:
        return PositionMonitorHeartbeat(
            running=self._running,
            last_tick_at=self._last_tick_at,
            tick_count=self._tick_count,
            watch_errors=dict(self._watch_errors),
            seed_error=self._seed_error,
            last_tick_error=self._last_tick_error,
        )

    async def run(self) -> None:
        """The lifespan loop: seed the config-pinned wallets once, then
        sleep/tick until cancelled. Failures are heartbeat entries, never a
        loop exit — only cancellation (app shutdown) stops it."""
        self._running = True
        try:
            try:
                await self.seed_config_watches()
            except Exception as exc:
                self._seed_error = f"{type(exc).__name__}: {exc}"
            while True:
                await asyncio.sleep(self._poll_seconds)
                try:
                    await self.tick_once(self._clock())
                except Exception as exc:
                    self._last_tick_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._running = False

    async def seed_config_watches(self) -> int:
        """Create a `source="config"` watch for each LP pool of each pinned
        wallet that has none yet. Idempotent (keyed on wallet+chain+pool);
        best-effort — a failure is recorded in `seed_error`, never raised to
        the lifespan. Returns the number of watches created."""
        if not self._pinned_wallets:
            return 0
        if self._wallet_source is None:
            self._seed_error = (
                "config-pinned wallets set but no wallet-positions source is wired "
                "(zerion_api_key unset?) - pinned wallets not seeded"
            )
            return 0
        existing = await asyncio.to_thread(self._watches.list)
        covered = {
            (w.wallet.lower(), w.chain, w.pool_address.lower())
            for w in existing
            if w.source == "config"
        }

        def _create(wallet: str, chain: str, pool_address: str) -> None:
            self._watches.create(
                wallet=wallet,
                chain=chain,
                pool_address=pool_address,
                nft_token_id=None,
                dwell_hours=6.0,
                interval_seconds=900,
                source="config",
                created_at=self._clock(),
            )

        created = 0
        for wallet in self._pinned_wallets:
            validate_evm_address(wallet, field="pinned wallet")
            positions = await asyncio.to_thread(self._wallet_source.fetch_positions, wallet)
            for position in positions:
                if position.kind != "lp" or not position.pool_address:
                    continue
                key = (wallet.lower(), position.chain, position.pool_address.lower())
                if key in covered:
                    continue
                await asyncio.to_thread(_create, wallet, position.chain, position.pool_address)
                covered.add(key)
                created += 1
        self._seed_error = None
        return created

    async def tick_once(self, now: datetime) -> int:
        """Evaluate every enabled watch that is due at `now`. Returns the
        number of alerts fired. Per-watch errors are contained and recorded;
        this method raises only for infrastructure failures outside any one
        watch (contained in turn by `run()`'s loop)."""
        watches = await asyncio.to_thread(self._watches.list, enabled_only=True)
        due = [w for w in watches if self._next_due.get(w.id, now) <= now]

        fired = 0
        made_a_read = False
        for watch in due:
            if made_a_read and self._read_spacing_seconds > 0:
                await asyncio.sleep(self._read_spacing_seconds)
            made_a_read = True
            fired += await self._evaluate_one(watch, now)
            self._next_due[watch.id] = now + timedelta(seconds=watch.interval_seconds)

        self._last_tick_at = now
        self._tick_count += 1
        self._last_tick_error = None
        return fired

    async def _evaluate_one(self, watch: DefiPositionWatch, now: datetime) -> int:
        """Read one watch's live on-chain state and advance its dwell state;
        fire on the dwell-qualified edge. Returns 1 if an alert fired, 0
        otherwise. A failed or impossible read leaves the persisted dwell
        state untouched (ADR-0093 — never reset on an RPC error)."""
        try:
            detail = await asyncio.to_thread(self._read_detail, watch)
        except LpDetailConfigError as exc:
            self._watch_errors[watch.id] = f"unconfigured: {exc}"
            return 0
        except Exception as exc:
            self._watch_errors[watch.id] = f"read failed: {type(exc).__name__}: {exc}"
            return 0
        if detail is None:
            self._watch_errors[watch.id] = (
                "unreadable: no concentrated-liquidity position resolved for this "
                "pool/wallet - watch has never evaluated (not the same as in range)"
            )
            return 0

        new_state, fire = evaluate_position_dwell(
            watch.dwell_state, in_range=detail.in_range, now=now, dwell=watch.dwell
        )
        await asyncio.to_thread(self._watches.set_dwell_state, watch.id, new_state)
        if not fire:
            self._watch_errors.pop(watch.id, None)
            return 0

        assert new_state.out_since is not None  # a fire happens mid-excursion
        hours_out = (now - new_state.out_since).total_seconds() / 3600.0
        fees = list(detail.uncollected_fees)
        # Persist first: history is the durable record; the two delivery legs
        # below are best-effort live fan-out on top of it (ADR-0055 pattern).
        await asyncio.to_thread(
            lambda: self._alerts.insert(
                watch_id=watch.id,
                wallet=watch.wallet,
                chain=watch.chain,
                pool_address=watch.pool_address,
                nft_token_id=watch.nft_token_id,
                fired_at=now,
                out_since=new_state.out_since,  # type: ignore[arg-type]  # asserted above
                hours_out=hours_out,
                tick_lower=detail.tick_lower,
                tick_upper=detail.tick_upper,
                current_tick=detail.current_tick,
                uncollected_fees=fees or None,
            )
        )
        payload = DefiPositionAlertPayloadV1(
            watch_id=watch.id,
            wallet=mask_wallet(watch.wallet),
            chain=watch.chain,
            pool_address=watch.pool_address,
            nft_token_id=watch.nft_token_id,
            fired_at=now,
            out_since=new_state.out_since,
            hours_out=hours_out,
            tick_lower=detail.tick_lower,
            tick_upper=detail.tick_upper,
            current_tick=detail.current_tick,
            in_range=False,
            uncollected_fees=(
                [PositionAlertFeeV1(symbol=t.symbol, amount=t.amount) for t in fees]
                if fees
                else None
            ),
        )
        envelope = self._bus.publish("defi.position_alert", payload)
        # The agent-pollable pending-events leg (ADR-0021): sidecar-originated,
        # so appended directly like the market-alert scheduler does — "what
        # fired while I was away" must be poll-visible.
        self._ui_events.append(
            UIEventEnvelope(
                event_id=str(uuid.uuid4()),
                type="defi.position_alert",
                version=DefiPositionAlertPayloadV1.VERSION,
                ts=envelope.ts,
                payload=envelope.payload,
            )
        )
        self._watch_errors.pop(watch.id, None)
        return 1

    def _read_detail(self, watch: DefiPositionWatch) -> LpPositionDetail | None:
        """One live deep-read for a watch — the enrichment path's own two-hop
        (resolve the position NFT unless the watch pins one, then fetch the
        tick/fee state). Returns `None` when no CL position resolves (a v2
        pool or a wallet holding nothing there) — the caller surfaces that
        distinctly. Raises the source's typed errors on failure."""
        source = self._lp_detail_source
        if source is None:
            raise LpDetailConfigError(
                "no LP-detail source wired (base_rpc_url / secrets store unset?)"
            )
        token_id = watch.nft_token_id
        if token_id is None:
            token_id = source.resolve_univ3_token_id(
                chain=watch.chain, pool_address=watch.pool_address, owner=watch.wallet
            )
            if token_id is None:
                return None
        return source.fetch_lp_detail(
            chain=watch.chain, pool_address=watch.pool_address, token_id=token_id
        )


__all__ = [
    "DEFAULT_POLL_SECONDS",
    "DEFAULT_READ_SPACING_SECONDS",
    "DefiPositionMonitor",
    "PositionMonitorHeartbeat",
]
