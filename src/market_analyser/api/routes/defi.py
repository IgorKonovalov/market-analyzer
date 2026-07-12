"""`POST /defi/scan` + `POST /defi/pnl` — the renderer's DeFi routes.

The renderer-side twins of the `scan_wallet` / `compute_wallet_pnl` MCP tools:
each pair reaches the same job (ADR-0015 reconciliation — UI for the
at-a-glance view, agent for narrative deep-dives). Renderer-bearer-gated by the
central middleware in `app.py`; a request carrying the MCP secret is rejected
cross-tenant.

Addresses are validated at the boundary (`EVM_ADDRESS_PATTERN`) by the request
models, so a non-address returns 422 — a typed 4xx, never a 500. A job failure
is mapped to a meaningful status: a missing/rejected key → 400, a rate limit →
429, any other upstream/parse failure → 502. Scan positions are returned live
(not persisted); the P&L path persists its decoded-tx + price-snapshot caches
(Plan 0035 phases 3-4) and streams `defi.scan_*` / `defi.pnl_*` on `/events`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from market_analyser.data.adapters.zerion import ZerionAuthError, ZerionError
from market_analyser.data.errors import RateLimitedError, UpstreamDataError
from market_analyser.data.sources import (
    GaugeResolutionSource,
    HistoricalPriceSource,
    LpPositionDetailSource,
    PnlCrosscheckSource,
    TxHistorySource,
    UnclaimedRewardsSource,
    WalletPositionsSource,
)
from market_analyser.defi.pnl_job import run_wallet_pnl
from market_analyser.defi.scan_job import EVM_ADDRESS_PATTERN, run_wallet_scan
from market_analyser.events import EventBus
from market_analyser.persistence.defi_tx_repository import DefiTxRepository

router = APIRouter(prefix="/defi", tags=["defi"])

# The default wallet-positions source (ADR-0034); the registry seam keeps it
# swappable.
_DEFAULT_SOURCE = "zerion"
# The default LP-detail source (Plan 0034); enriches LP positions when present.
_DEFAULT_LP_DETAIL_SOURCE = "rpc"
# The default gauge-resolution / unclaimed-rewards source (Plan 0084); both read
# over the same RPC path as LP-detail.
_DEFAULT_RPC_SOURCE = "rpc"


class ScanRequest(BaseModel):
    """`POST /defi/scan` body. `address` must be a raw `0x…` EVM address; a
    non-address fails validation (422) at the boundary."""

    model_config = ConfigDict(extra="forbid")

    address: str = Field(pattern=EVM_ADDRESS_PATTERN)


class ScanResponse(BaseModel):
    """The scan result. `wallet` is the masked address; `positions` are the
    decoded positions (JSON-mode dumps); `total_usd_value` sums them."""

    wallet: str
    positions: list[dict[str, Any]]
    chains: list[str]
    position_count: int
    total_usd_value: float


@router.post("/scan", response_model=ScanResponse)
async def post_defi_scan(request: Request, body: ScanRequest) -> ScanResponse:
    sources: Mapping[str, WalletPositionsSource] = request.app.state.wallet_positions_sources
    source = sources.get(_DEFAULT_SOURCE)
    if source is None:
        # The route is only mounted when a source exists, so this is defensive.
        raise HTTPException(status_code=503, detail="no wallet-positions source configured")
    lp_detail_sources: Mapping[str, LpPositionDetailSource] = request.app.state.lp_detail_sources
    lp_detail_source = lp_detail_sources.get(_DEFAULT_LP_DETAIL_SOURCE)
    event_bus: EventBus = request.app.state.event_bus
    try:
        result = await run_wallet_scan(
            source=source,
            address=body.address,
            event_bus=event_bus,
            lp_detail_source=lp_detail_source,
        )
    except ZerionAuthError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except RateLimitedError as err:
        raise HTTPException(status_code=429, detail=str(err)) from err
    except UpstreamDataError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    except (ZerionError, ValidationError) as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return ScanResponse(
        wallet=result.wallet,
        positions=[position.model_dump(mode="json") for position in result.positions],
        chains=result.chains,
        position_count=len(result.positions),
        total_usd_value=result.total_usd_value,
    )


class PnlRequest(BaseModel):
    """`POST /defi/pnl` body. `address` must be a raw `0x…` EVM address (422
    otherwise); `refresh=true` gap-fetches new transactions before replaying —
    the default replays the cached history (the deterministic re-run path)."""

    model_config = ConfigDict(extra="forbid")

    address: str = Field(pattern=EVM_ADDRESS_PATTERN)
    refresh: bool = False


class PnlResponse(BaseModel):
    """The reconstructed P&L. `wallet` is masked; `positions` are per-position
    breakdowns (JSON dumps of `PositionPnl` — `None` figures always travel
    with `incomplete=true` and a naming note); wallet totals are `None`
    whenever any position is incomplete. `crosscheck_zerion_total` is the
    advisory FIFO figure; `crosscheck_warning` flags gross divergence only —
    average-cost vs FIFO makes small differences expected (ADR-0036)."""

    wallet: str
    positions: list[dict[str, Any]]
    position_count: int
    incomplete: bool
    realized_usd: float | None
    unrealized_usd: float | None
    # Labeled current-state on-chain read of owed-but-unclaimed gauge rewards
    # (Plan 0084), wallet roll-up summed by symbol; kept out of realized/unrealized
    # and the determinism guarantee; `None` when nothing is owed.
    unclaimed_rewards: list[dict[str, Any]] | None
    crosscheck_zerion_total: float | None
    crosscheck_warning: bool


@router.post("/pnl", response_model=PnlResponse)
async def post_defi_pnl(request: Request, body: PnlRequest) -> PnlResponse:
    tx_sources: Mapping[str, TxHistorySource] = request.app.state.tx_history_sources
    tx_source = tx_sources.get(_DEFAULT_SOURCE)
    positions_sources: Mapping[str, WalletPositionsSource] = (
        request.app.state.wallet_positions_sources
    )
    positions_source = positions_sources.get(_DEFAULT_SOURCE)
    tx_repository: DefiTxRepository | None = request.app.state.defi_tx_repository
    price_source: HistoricalPriceSource | None = request.app.state.historical_price_source
    if tx_source is None or positions_source is None or tx_repository is None:
        # The route needs the full pipeline: tx source + discovery + the
        # decoded-tx cache. Absent pieces mean no persistence / no key store
        # was wired — a deployment condition, not a client error.
        raise HTTPException(status_code=503, detail="P&L pipeline is not configured")
    if price_source is None:
        raise HTTPException(status_code=503, detail="no historical price source configured")
    crosscheck = tx_source if isinstance(tx_source, PnlCrosscheckSource) else None
    gauge_sources: Mapping[str, GaugeResolutionSource] = request.app.state.gauge_resolution_sources
    unclaimed_sources: Mapping[str, UnclaimedRewardsSource] = (
        request.app.state.unclaimed_rewards_sources
    )
    event_bus: EventBus = request.app.state.event_bus
    try:
        result = await run_wallet_pnl(
            tx_source=tx_source,
            positions_source=positions_source,
            price_source=price_source,
            tx_repository=tx_repository,
            event_bus=event_bus,
            address=body.address,
            refresh=body.refresh,
            crosscheck_source=crosscheck,
            gauge_source=gauge_sources.get(_DEFAULT_RPC_SOURCE),
            unclaimed_source=unclaimed_sources.get(_DEFAULT_RPC_SOURCE),
        )
    except ZerionAuthError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except RateLimitedError as err:
        raise HTTPException(status_code=429, detail=str(err)) from err
    except UpstreamDataError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    except (ZerionError, ValidationError) as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return PnlResponse(
        wallet=result.wallet,
        positions=[position.model_dump(mode="json") for position in result.positions],
        position_count=len(result.positions),
        incomplete=result.incomplete,
        realized_usd=result.realized_usd,
        unrealized_usd=result.unrealized_usd,
        unclaimed_rewards=(
            [reward.model_dump(mode="json") for reward in result.unclaimed_rewards]
            if result.unclaimed_rewards is not None
            else None
        ),
        crosscheck_zerion_total=result.crosscheck_zerion_total,
        crosscheck_warning=result.crosscheck_warning,
    )
