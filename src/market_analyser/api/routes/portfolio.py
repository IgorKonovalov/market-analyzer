"""`GET /portfolio` + `POST /portfolio/risk` — the renderer's portfolio surface.

Plan 0043 phase 1 (ADR-0042 aggregation, ADR-0037 conditional-risk framing).

The renderer-facing twins of the `portfolio_summary` / `defi_risk` MCP tools: the
renderer cannot reach MCP tools, so — as with backtests (`get_backtest` + `/backtests`)
and the advisor track record (`get_track_record` + `/track_record`) — each aggregate
gets a REST route the typed fetch client can call. Both bodies are the tools' factored
`_portfolio_summary_response` / `_defi_risk_response`, reused verbatim so the agent and
viewer surfaces can never disagree.

- `GET /portfolio?wallet=0x…&include_defi_basis=true` returns the cross-venue
  `PortfolioSummary` (holdings + average-cost basis + unrealized P&L + exposure), each
  venue leg stamped with its **own** as-of time (freshness is never blended, ADR-0042),
  alongside `leg_errors`/`notes`. `wallet` is optional and switches the DeFi leg on.
- `POST /portfolio/risk` recomputes the DeFi risk panel: `kind="scenario"` for a supplied
  price-shock (→ health factor / liquidation distance / impermanent loss) and
  `kind="conditional"` for a probability under a stated volatility model (the assumption
  travels inline, ADR-0037). It is the viewer's shock-slider + probability path.

Facts only — no rebalance/exit/buy/sell affordance lives here (ADR-0029). Renderer-bearer-
gated by the central middleware in `app.py`; a request carrying the MCP secret is rejected
cross-tenant. The router is mounted only when an account-holdings source is wired (the same
gate the `portfolio_summary` tool uses), so `GET /portfolio`'s account leg always has a
source to read.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from market_analyser.api.mcp_tools.defi_risk import DefiRiskInput, _defi_risk_response
from market_analyser.api.mcp_tools.portfolio import (
    PortfolioSummaryInput,
    PortfolioSurfaceResponse,
    _portfolio_summary_response,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.sources import (
    AaveAccountSource,
    AccountHoldingsSource,
    TxHistorySource,
    WalletPositionsSource,
)
from market_analyser.portfolio.sources import MANUAL_POSITIONS_FILENAME

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

# The default venue/source keys, matching the MCP-tool registration.
_DEFAULT_ACCOUNT_SOURCE = "binance"
_DEFAULT_DEFI_SOURCE = "zerion"
_DEFAULT_AAVE_SOURCE = "rpc"


@router.get("", response_model=PortfolioSurfaceResponse)
async def get_portfolio(
    request: Request,
    wallet: str | None = Query(default=None),
    include_defi_basis: bool = Query(default=True),
) -> PortfolioSurfaceResponse:
    account_sources: Mapping[str, AccountHoldingsSource] = (
        request.app.state.account_holdings_sources
    )
    account_source = account_sources.get(_DEFAULT_ACCOUNT_SOURCE)
    if account_source is None:
        # The route is only mounted when a source exists, so this is defensive.
        raise HTTPException(status_code=503, detail="no account-holdings source configured")
    try:
        params = PortfolioSummaryInput(wallet=wallet, include_defi_basis=include_defi_basis)
    except ValidationError as err:
        # A non-address `wallet` fails the boundary pattern — a typed 422, never a 500.
        raise HTTPException(status_code=422, detail=err.errors()) from err

    wallet_sources: Mapping[str, WalletPositionsSource] = request.app.state.wallet_positions_sources
    tx_sources: Mapping[str, TxHistorySource] = request.app.state.tx_history_sources
    provider: MarketDataProvider = request.app.state.provider
    manual_path: Path = request.app.state.manual_positions_path or (
        Path("positions") / MANUAL_POSITIONS_FILENAME
    )
    return await _portfolio_summary_response(
        provider=provider,
        account_source=account_source,
        positions_source=wallet_sources.get(_DEFAULT_DEFI_SOURCE),
        tx_source=tx_sources.get(_DEFAULT_DEFI_SOURCE),
        defi_tx_repository=request.app.state.defi_tx_repository,
        historical_price_source=request.app.state.historical_price_source,
        manual_positions_path=manual_path,
        params=params,
    )


@router.post("/risk")
async def post_portfolio_risk(request: Request, body: DefiRiskInput) -> dict[str, Any]:
    aave_sources: Mapping[str, AaveAccountSource] = request.app.state.aave_account_sources
    provider: MarketDataProvider = request.app.state.provider
    try:
        return await _defi_risk_response(
            provider=provider,
            aave_source=aave_sources.get(_DEFAULT_AAVE_SOURCE),
            params=body,
        )
    except ValueError as err:
        # A missing leg / a field its `kind` needs — a clear 422, never a 500.
        raise HTTPException(status_code=422, detail=str(err)) from err
