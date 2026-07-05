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
same cached history replays byte-identically (ADR-0036 / ADR-0018).

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

from market_analyser.data.sources import (
    HistoricalPriceSource,
    PnlCrosscheckSource,
    TxHistorySource,
    WalletPositionsSource,
)
from market_analyser.defi.discovery import DiscoveryService, mask_wallet
from market_analyser.defi.pnl import WalletPnl, compute_wallet_pnl
from market_analyser.defi.pnl_events import map_events
from market_analyser.defi.scan_job import _classify_failure
from market_analyser.defi.tx_ingestion import TxHistoryService
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
) -> WalletPnl:
    """Reconstruct the wallet's P&L, streaming `defi.pnl_*`; return the result.

    `refresh=True` gap-fetches new transactions before replaying; the default
    replays the cached history untouched (zero source calls — the
    deterministic re-run path). Raises the pipeline's typed error (after
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
) -> WalletPnl:
    history = TxHistoryService(source=tx_source, repository=tx_repository).load_history(
        address, refresh=refresh
    )
    positions = DiscoveryService(positions_source).discover(address).positions
    events = map_events(history, positions)
    # Input-derived benchmark anchor, never the wall clock (see docstring).
    as_of = history[-1].mined_at if history else datetime.fromtimestamp(0, tz=UTC)
    pnl = compute_wallet_pnl(
        wallet=address,
        positions=positions,
        events=events,
        price_source=price_source,
        as_of=as_of,
    )
    crosscheck = _fetch_crosscheck(crosscheck_source, address)
    return pnl.model_copy(
        update={
            "crosscheck_zerion_total": crosscheck,
            "crosscheck_warning": _grossly_diverges(pnl, crosscheck),
        }
    )


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
