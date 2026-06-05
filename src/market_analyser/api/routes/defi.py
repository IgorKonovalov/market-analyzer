"""`POST /defi/scan` — wallet discovery for the renderer (Plan 0032 phase 4).

The renderer-side twin of the `scan_wallet` MCP tool: both reach the same scan
job (ADR-0015 reconciliation — UI for the at-a-glance view, agent for narrative
deep-dives). Renderer-bearer-gated by the central middleware in `app.py`; a
request carrying the MCP secret is rejected cross-tenant.

The address is validated at the boundary (`EVM_ADDRESS_PATTERN`) by the request
model, so a non-address returns 422 — a typed 4xx, never a 500. A scan failure is
mapped to a meaningful status: a missing/rejected key → 400, a rate limit → 429,
any other upstream/parse failure → 502. Positions are returned live (not
persisted); the scan streams `defi.scan_*` progress on `/events`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from market_analyser.data.adapters.zerion import ZerionAuthError, ZerionError
from market_analyser.data.errors import RateLimitedError, UpstreamDataError
from market_analyser.data.sources import LpPositionDetailSource, WalletPositionsSource
from market_analyser.defi.scan_job import EVM_ADDRESS_PATTERN, run_wallet_scan
from market_analyser.events import EventBus

router = APIRouter(prefix="/defi", tags=["defi"])

# The default wallet-positions source (ADR-0034); the registry seam keeps it
# swappable.
_DEFAULT_SOURCE = "zerion"
# The default LP-detail source (Plan 0034); enriches LP positions when present.
_DEFAULT_LP_DETAIL_SOURCE = "rpc"


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
