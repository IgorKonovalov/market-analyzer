"""Wallet-P&L job: runs the reconstruction as an async job streaming SSE
progress (Plan 0035 phase 7, mirroring `scan_job.py`).

`run_wallet_pnl` wraps the synchronous pipeline (cached-history load →
discovery → taxonomy map → replay engine) in `asyncio.to_thread` and publishes
the `defi.pnl_*` lifecycle on the layer-neutral bus (ADR-0017, ADR-0032:
`defi → events`, never `defi → api`):

    pnl_started → pnl_completed | pnl_failed

On failure it publishes `defi.pnl_failed` with the typed, closed reason and
re-raises the original exception — the awaiting tool/route still sees the
precise typed error, and the result is never a zeroed stand-in ("never
silently zero"). Wallet addresses are masked before any event payload.

**Determinism.** The vs-HODL `as_of` anchor is the newest *cached*
transaction's timestamp — an input-derived value, not the wall clock — so the
same cached history replays byte-identically (ADR-0036 / ADR-0018). The
rolling-window `now` anchor (Plan 0088 / ADR-0082) *is* a wall-clock read,
captured here once and passed into the engine so "last 30 days" tracks calendar
time; the windowed figures are deterministic given a fixed `now` but, like
`usd_value`, deliberately outside the cross-calendar-time byte-identical set.

**Cross-check.** Zerion's FIFO `total_gain` is fetched best-effort through the
`PnlCrosscheckSource` seam: any failure leaves it `None` without failing the
reconstruction (it is advisory, ADR-0036 Alt A). `crosscheck_warning` flips
only on **gross** divergence — both totals at least `$100` in magnitude and
disagreeing by an order of magnitude (ratio ≥ 10) or by sign. Average-cost vs
FIFO makes small differences expected and ignored.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from market_analyser.data.errors import UpstreamDataError
from market_analyser.data.sources import (
    GaugeResolutionSource,
    HistoricalPriceSource,
    PnlCrosscheckSource,
    TxHistorySource,
    UnclaimedRewardsSource,
    WalletPositionsSource,
)
from market_analyser.defi.discovery import DiscoveryService, mask_wallet
from market_analyser.defi.models import Chain, DefiPosition
from market_analyser.defi.pnl import WalletPnl, compute_wallet_pnl
from market_analyser.defi.pnl_events import map_events
from market_analyser.defi.scan_job import _classify_failure
from market_analyser.defi.tx_ingestion import TxHistoryService
from market_analyser.defi.tx_models import DecodedTx
from market_analyser.defi.unclaimed import augment_with_unclaimed
from market_analyser.events import (
    DefiPnlCompletedPayloadV1,
    DefiPnlFailedPayloadV1,
    DefiPnlStartedPayloadV1,
    EventBus,
)
from market_analyser.persistence.defi_tx_repository import DefiTxRepository

_logger = logging.getLogger(__name__)

# The gross-divergence rule: ignore totals under the noise floor, then flag an
# order-of-magnitude ratio or a sign flip. Documented in the module docstring.
_CROSSCHECK_NOISE_FLOOR_USD = 100.0
_CROSSCHECK_RATIO = 10.0


async def run_wallet_pnl(
    *,
    tx_source: TxHistorySource,
    positions_source: WalletPositionsSource,
    price_source: HistoricalPriceSource,
    tx_repository: DefiTxRepository,
    event_bus: EventBus,
    address: str,
    refresh: bool = False,
    crosscheck_source: PnlCrosscheckSource | None = None,
    gauge_source: GaugeResolutionSource | None = None,
    unclaimed_source: UnclaimedRewardsSource | None = None,
) -> WalletPnl:
    """Reconstruct the wallet's P&L, streaming `defi.pnl_*`; return the result.

    `refresh=True` gap-fetches new transactions before replaying; the default
    replays the cached history untouched (zero source calls — the
    deterministic re-run path). `gauge_source`, when supplied, resolves the
    gauge→pool map so Aerodrome emissions attribute to the right position
    (Plan 0084); `unclaimed_source`, when supplied, folds each open position's
    owed-but-unclaimed gauge rewards onto the result as a labeled current-state
    field (best-effort, outside the determinism guarantee). Both absent, the
    pre-0084 behavior is reproduced. Raises the pipeline's typed error (after
    publishing `defi.pnl_failed`) — never returns a zeroed result."""
    masked = mask_wallet(address)
    event_bus.publish("defi.pnl_started", DefiPnlStartedPayloadV1(wallet=masked))
    try:
        result = await asyncio.to_thread(
            _reconstruct,
            tx_source,
            positions_source,
            price_source,
            tx_repository,
            address,
            refresh,
            crosscheck_source,
            gauge_source,
            unclaimed_source,
        )
    except Exception as err:
        event_bus.publish(
            "defi.pnl_failed",
            DefiPnlFailedPayloadV1(
                wallet=masked,
                reason=_classify_failure(err),
                message=str(err),
            ),
        )
        raise
    event_bus.publish(
        "defi.pnl_completed",
        DefiPnlCompletedPayloadV1(
            wallet=masked,
            position_count=len(result.positions),
            incomplete_count=sum(1 for p in result.positions if p.incomplete),
            realized_usd=result.realized_usd,
            unrealized_usd=result.unrealized_usd,
        ),
    )
    return result


def _reconstruct(
    tx_source: TxHistorySource,
    positions_source: WalletPositionsSource,
    price_source: HistoricalPriceSource,
    tx_repository: DefiTxRepository,
    address: str,
    refresh: bool,
    crosscheck_source: PnlCrosscheckSource | None,
    gauge_source: GaugeResolutionSource | None,
    unclaimed_source: UnclaimedRewardsSource | None,
) -> WalletPnl:
    history = TxHistoryService(source=tx_source, repository=tx_repository).load_history(
        address, refresh=refresh
    )
    positions = DiscoveryService(positions_source).discover(address).positions
    gauge_map = _resolve_gauge_map(gauge_source, history, positions)
    events = map_events(history, positions, gauge_map)
    # Input-derived benchmark anchor, never the wall clock (see docstring).
    as_of = history[-1].mined_at if history else datetime.fromtimestamp(0, tz=UTC)
    # Analysis-time anchor for the rolling windows (Plan 0088 / ADR-0082): the one
    # wall-clock read, captured here in the job and passed in, so "last 30 days"
    # is 30 calendar days. The engine never reads the clock; windowed figures are
    # deterministic given this `now`, not across calendar time (like `usd_value`).
    now = datetime.now(tz=UTC)
    pnl = compute_wallet_pnl(
        wallet=address,
        positions=positions,
        events=events,
        price_source=price_source,
        as_of=as_of,
        now=now,
    )
    # Current-state augmentation (Plan 0084), after the pure replay so the
    # deterministic figures are untouched; best-effort, never fails the P&L.
    if unclaimed_source is not None:
        pnl = augment_with_unclaimed(pnl, positions, unclaimed_source, owner=address)
    crosscheck = _fetch_crosscheck(crosscheck_source, address)
    return pnl.model_copy(
        update={
            "crosscheck_zerion_total": crosscheck,
            "crosscheck_warning": _grossly_diverges(pnl, crosscheck),
        }
    )


def _resolve_gauge_map(
    gauge_source: GaugeResolutionSource | None,
    history: list[DecodedTx],
    positions: list[DefiPosition],
) -> dict[str, str]:
    """Build the pure `{gauge_address: pool_address}` map `map_events` consumes,
    doing the on-chain I/O here so the classifier stays pure (Plan 0084 risk
    mitigation). Only contracts that appear in the history but are **not** already
    a known pool are probed as gauge candidates, and only those resolving to one
    of the wallet's own pools are kept — so an unrelated gauge or a random router
    never enters the map. Deterministic: candidates are probed in sorted order and
    the resolver returns an on-chain immutable, so a re-run reproduces the map.

    Absent a resolver, or with no candidates, the map is empty and replay behaves
    exactly as pre-0084. Resolution is **best-effort** (Plan 0084 risk mitigation):
    a resolver failure — an unconfigured RPC URL for a chain, a throttle, an outage
    — is swallowed so those gauges simply go unresolved (the position stays honestly
    incomplete), never crashing the whole reconstruction. A chain whose RPC is
    unconfigured is recorded after the first failure so the rest of its gauges are
    skipped without re-probing."""
    if gauge_source is None:
        return {}
    known_pools = {p.pool_address.lower() for p in positions if p.pool_address is not None}
    candidates: dict[str, Chain] = {}  # gauge address -> the chain it was seen on
    for tx in history:
        for act in tx.acts:
            if act.contract_address is None:
                continue
            addr = act.contract_address.lower()
            if addr not in known_pools:
                candidates.setdefault(addr, tx.chain)
    gauge_map: dict[str, str] = {}
    unavailable: set[Chain] = set()  # chains whose resolver has already failed
    for addr in sorted(candidates):
        chain = candidates[addr]
        if chain in unavailable:
            continue
        try:
            pool = gauge_source.resolve_pool(chain=chain, gauge_address=addr)
        except UpstreamDataError:
            # RPC unconfigured / throttled / down for this chain: degrade to the
            # pre-0084 behavior (gauge txs unattributed) rather than failing the P&L.
            unavailable.add(chain)
            continue
        if pool is not None and pool.lower() in known_pools:
            gauge_map[addr] = pool.lower()
    return gauge_map


def _fetch_crosscheck(source: PnlCrosscheckSource | None, address: str) -> float | None:
    """Best-effort by design: the cross-check is advisory, so any failure is
    logged and swallowed — it must never fail the reconstruction."""
    if source is None:
        return None
    try:
        return source.fetch_pnl_total(address)
    except Exception:
        _logger.warning("zerion pnl cross-check unavailable; continuing without it")
        return None


def _grossly_diverges(pnl: WalletPnl, crosscheck: float | None) -> bool:
    if crosscheck is None or pnl.realized_usd is None or pnl.unrealized_usd is None:
        return False  # no confident pair of numbers to compare
    ours = pnl.realized_usd + pnl.unrealized_usd
    ours_mag, theirs_mag = abs(ours), abs(crosscheck)
    if max(ours_mag, theirs_mag) < _CROSSCHECK_NOISE_FLOOR_USD:
        return False
    if min(ours_mag, theirs_mag) >= _CROSSCHECK_NOISE_FLOOR_USD and (ours > 0) != (crosscheck > 0):
        return True
    smaller = min(ours_mag, theirs_mag)
    if smaller == 0:
        return True
    return max(ours_mag, theirs_mag) / smaller >= _CROSSCHECK_RATIO


__all__ = ["run_wallet_pnl"]
