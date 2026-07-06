"""`portfolio_summary` MCP tool (Plan 0041 phase 3; ADR-0042).

The agent's one-call cross-venue holdings read: the Binance account leg (spot
+ USDⓈ-M futures via the read-only key), the DeFi leg (wallet discovery when a
wallet address is given, with average-cost basis joined from the ADR-0036
replay over the *cached* transaction history), and the manual positions file —
aggregated by the pure `aggregate_portfolio` into unified holdings + basis +
unrealized P&L + exposure, each leg stamped with its own as-of time.

**Per-leg containment.** A failing leg never fails the call (the
`multi_timeframe_analysis` lesson): it lands in `leg_errors` with a typed
reason while the other legs still aggregate. Absent wiring and coverage gaps
surface in `notes` — skipped basis, unpriced holdings, partial P&L coverage —
never silently.

**Pricing.** Spot balances and manual rows are priced through the provider's
live quote path (`<ASSET>-USD` for Binance assets, the row's own symbol for
manual rows), each `PricePoint` carrying the quote's own upstream timestamp
and source name; futures ride the venue's mark, DeFi rides the discovery
figures. A symbol the provider can't quote stays honestly unpriced.

**Basis.** The ADR-0036 join reuses the engine verbatim: cached history
(`TxHistoryService`, `refresh=False` — a warm re-run makes zero history
fetches; the first call for a wallet ingests once) → `map_events` →
`compute_wallet_pnl` over the same discovery positions the holdings leg
already fetched, keeping the two views consistent by construction. Incomplete
positions (the engine's loud paths) simply contribute no basis and are
counted in a note.

Facts only — holdings, basis, P&L, exposure; no recommendation of any kind is
computed, carried, or phrased (ADR-0029: that crossing is the advisor's; a
test pins this output free of action language).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from market_analyser.data.adapters.binance_account import (
    BinanceAccountAuthError,
    BinanceAccountError,
)
from market_analyser.data.adapters.zerion import ZerionAuthError
from market_analyser.data.errors import (
    GeoRestrictedError,
    UpstreamDataError,
    failure_reason,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.sources import (
    AccountHoldingsSource,
    HistoricalPriceSource,
    TxHistorySource,
    WalletPositionsSource,
)
from market_analyser.data.types import AccountHoldings
from market_analyser.defi.discovery import DiscoveryService
from market_analyser.defi.models import DefiPosition
from market_analyser.defi.pnl import compute_wallet_pnl
from market_analyser.defi.pnl_events import map_events
from market_analyser.defi.scan_job import EVM_ADDRESS_PATTERN
from market_analyser.defi.tx_ingestion import TxHistoryService
from market_analyser.persistence.defi_tx_repository import DefiTxRepository
from market_analyser.portfolio.aggregate import (
    PricePoint,
    aggregate_portfolio,
    unrealized_contributor_count,
)
from market_analyser.portfolio.models import Holding
from market_analyser.portfolio.sources import ManualPositionsError, ManualPositionsSource

_DEFAULT_ACCOUNT_SOURCE = "binance"
_DEFAULT_DEFI_SOURCE = "zerion"

PORTFOLIO_SUMMARY_DESCRIPTION = (
    "Aggregate cross-venue holdings into one read-only view (facts only, no "
    "recommendation of any kind): the Binance account leg (spot balances + "
    "USDS-M futures positions, read via the read-only API key), the DeFi leg "
    "(wallet discovery across Ethereum/Base/Arbitrum/Optimism when a 0x wallet "
    "address is given, with average-cost basis joined from the reconstructed "
    "on-chain history), and the manual positions file (positions/portfolio.json). "
    "Returns {summary: {holdings: [{symbol, venue, quantity, avg_cost, as_of, "
    "usd_value, pricing_source, kind}], unrealized_pnl_usd, exposure_by_asset, "
    "exposure_by_venue, legs_as_of, queried_at}, leg_errors, notes, error, "
    "message}. Every leg carries its own as_of - freshness is never blended; "
    "every valuation names its pricing_source (venue mark for futures, live "
    "quotes for spot/manual rows, discovery figures for DeFi) - no single "
    "implied oracle. unrealized_pnl_usd = usd_value - avg_cost x quantity "
    "summed over holdings carrying both a price and a basis; None when none "
    "does; notes flag partial coverage, unpriced holdings, and skipped or "
    "incomplete basis. A failing leg never fails the call: it lands in "
    "leg_errors with a typed reason ('auth' = venue credential missing or "
    "rejected - set binance_read_api_key + binance_read_api_secret, or "
    "zerion_api_key, via the Settings secret endpoint) while the other legs "
    "still aggregate. wallet is optional; include_defi_basis=false skips the "
    "history replay. First basis call for a wallet ingests its history (slow); "
    "re-runs read the immutable SQLite cache."
)


class PortfolioSummaryInput(BaseModel):
    """MCP-boundary input. Unknown keys rejected; `wallet` (optional) must be
    a raw `0x…` EVM address and switches the DeFi leg on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    wallet: str | None = Field(default=None, pattern=EVM_ADDRESS_PATTERN)
    include_defi_basis: bool = True


def register_portfolio_summary(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    account_holdings_sources: Mapping[str, AccountHoldingsSource],
    manual_positions_path: Path,
    wallet_positions_sources: Mapping[str, WalletPositionsSource] | None = None,
    tx_history_sources: Mapping[str, TxHistorySource] | None = None,
    defi_tx_repository: DefiTxRepository | None = None,
    historical_price_source: HistoricalPriceSource | None = None,
) -> None:
    """Bind the `portfolio_summary` tool to `server`. Dependencies are captured
    by closure so the tool body keeps its single declared parameter (FastMCP
    introspects it for the input schema). The DeFi dependencies are optional:
    absent ones degrade to `leg_errors`/`notes`, never a crash."""
    account_source = account_holdings_sources[_DEFAULT_ACCOUNT_SOURCE]
    positions_source = (
        wallet_positions_sources.get(_DEFAULT_DEFI_SOURCE) if wallet_positions_sources else None
    )
    tx_source = tx_history_sources.get(_DEFAULT_DEFI_SOURCE) if tx_history_sources else None

    @server.tool(description=PORTFOLIO_SUMMARY_DESCRIPTION)
    async def portfolio_summary(params: PortfolioSummaryInput) -> dict[str, Any]:
        queried_at = datetime.now(tz=UTC)
        leg_errors: dict[str, str] = {}
        notes: list[str] = []

        binance_account: AccountHoldings | None = None
        try:
            binance_account = await asyncio.to_thread(account_source.fetch_account_holdings)
        except UpstreamDataError as err:
            leg_errors["binance"] = f"{_binance_reason(err)}: {err}"
        except (BinanceAccountError, ValidationError) as err:
            leg_errors["binance"] = f"malformed_response: {err}"

        defi_positions: list[DefiPosition] | None = None
        defi_as_of: datetime | None = None
        defi_basis: dict[str, float] | None = None
        if params.wallet is not None:
            defi_positions, defi_as_of = await _read_defi_leg(
                positions_source, params.wallet, leg_errors
            )
        if defi_positions is not None and params.wallet is not None:
            if not params.include_defi_basis:
                notes.append("defi cost basis skipped (include_defi_basis=false)")
            elif tx_source is None or defi_tx_repository is None or historical_price_source is None:
                notes.append("defi cost basis unavailable: the P&L pipeline is not wired")
            else:
                defi_basis = await _read_defi_basis(
                    tx_source,
                    defi_tx_repository,
                    historical_price_source,
                    params.wallet,
                    defi_positions,
                    notes,
                )

        manual_holdings: list[Holding] = []
        try:
            manual_holdings = ManualPositionsSource(manual_positions_path).load_holdings()
        except ManualPositionsError as err:
            leg_errors["manual"] = f"malformed_file: {err}"
        else:
            if not manual_holdings:
                notes.append(
                    f"manual leg empty: no rows in {manual_positions_path.name} "
                    "(or the file is absent)",
                )

        prices: dict[tuple[str, str], PricePoint] = {}
        if binance_account is not None:
            for balance in binance_account.spot:
                await _quote_into(
                    prices, ("binance", balance.asset), f"{balance.asset}-USD", provider, notes
                )
        for holding in manual_holdings:
            await _quote_into(prices, ("manual", holding.symbol), holding.symbol, provider, notes)

        summary = aggregate_portfolio(
            binance=binance_account,
            defi_positions=defi_positions,
            defi_as_of=defi_as_of,
            defi_basis=defi_basis,
            manual=manual_holdings,
            prices=prices,
            queried_at=queried_at,
        )
        contributors = unrealized_contributor_count(summary)
        if summary.unrealized_pnl_usd is not None and contributors < len(summary.holdings):
            notes.append(
                f"unrealized_pnl_usd covers {contributors} of {len(summary.holdings)} "
                "holdings (the rest lack a price or a basis)",
            )
        return {
            "summary": summary.model_dump(mode="json"),
            "leg_errors": leg_errors,
            "notes": notes,
            "error": None,
            "message": None,
        }


async def _read_defi_leg(
    positions_source: WalletPositionsSource | None,
    wallet: str,
    leg_errors: dict[str, str],
) -> tuple[list[DefiPosition] | None, datetime | None]:
    """Discover the wallet's positions, containing every typed failure into
    `leg_errors["defi"]`. The leg's as-of is the scan instant."""
    if positions_source is None:
        leg_errors["defi"] = "not_configured: no wallet-positions source is wired"
        return None, None
    as_of = datetime.now(tz=UTC)
    try:
        result = await asyncio.to_thread(DiscoveryService(positions_source).discover, wallet)
    except ZerionAuthError as err:
        leg_errors["defi"] = f"auth: {err}"
        return None, None
    except UpstreamDataError as err:
        leg_errors["defi"] = f"{failure_reason(err)}: {err}"
        return None, None
    except (ValidationError, ValueError) as err:
        leg_errors["defi"] = f"malformed_response: {err}"
        return None, None
    return list(result.positions), as_of


async def _read_defi_basis(
    tx_source: TxHistorySource,
    defi_tx_repository: DefiTxRepository,
    historical_price_source: HistoricalPriceSource,
    wallet: str,
    positions: list[DefiPosition],
    notes: list[str],
) -> dict[str, float] | None:
    """Join the ADR-0036 replay's remaining basis by position id; a failure is
    a note (the holdings leg stands on its own), incomplete positions simply
    contribute no basis and are counted."""
    try:
        basis, incomplete = await asyncio.to_thread(
            _replay_basis,
            tx_source,
            defi_tx_repository,
            historical_price_source,
            wallet,
            positions,
        )
    except UpstreamDataError as err:
        notes.append(f"defi cost basis unavailable ({failure_reason(err)}: {err})")
        return None
    except (ValidationError, ValueError) as err:
        notes.append(f"defi cost basis unavailable (malformed history: {err})")
        return None
    if incomplete:
        notes.append(
            f"defi basis incomplete for {incomplete} of {len(positions)} positions "
            "(replay's loud paths; their avg_cost stays null)",
        )
    return basis


def _replay_basis(
    tx_source: TxHistorySource,
    repository: DefiTxRepository,
    price_source: HistoricalPriceSource,
    wallet: str,
    positions: list[DefiPosition],
) -> tuple[dict[str, float], int]:
    """The ADR-0036 engine, verbatim, over the cached history and the same
    discovery positions the holdings leg fetched. The HODL anchor derives from
    the cached inputs (never the wall clock) — the `pnl_job` precedent."""
    history = TxHistoryService(source=tx_source, repository=repository).load_history(
        wallet, refresh=False
    )
    events = map_events(history, positions)
    as_of = history[-1].mined_at if history else datetime.fromtimestamp(0, tz=UTC)
    pnl = compute_wallet_pnl(
        wallet=wallet,
        positions=positions,
        events=events,
        price_source=price_source,
        as_of=as_of,
    )
    basis = {p.position_id: p.cost_basis_usd for p in pnl.positions if p.cost_basis_usd is not None}
    return basis, sum(1 for p in pnl.positions if p.incomplete)


async def _quote_into(
    prices: dict[tuple[str, str], PricePoint],
    key: tuple[str, str],
    quote_symbol: str,
    provider: MarketDataProvider,
    notes: list[str],
) -> None:
    """Resolve one live quote into the price map; an unquotable symbol leaves
    the holding honestly unpriced with a note, never a zero."""
    if key in prices:
        return
    try:
        quote = await asyncio.to_thread(provider.get_quote, quote_symbol)
        prices[key] = PricePoint(
            price=quote.price,
            source=f"{quote.source}:{quote_symbol}",
            as_of=quote.as_of,
        )
    except UpstreamDataError as err:
        notes.append(f"unpriced {key[0]}:{key[1]} ({failure_reason(err)})")
    except (ValidationError, ValueError) as err:
        notes.append(f"unpriced {key[0]}:{key[1]} (malformed quote: {err})")


def _binance_reason(err: UpstreamDataError) -> str:
    if isinstance(err, BinanceAccountAuthError):
        return "auth"
    if isinstance(err, GeoRestrictedError):
        return "geo_restricted"
    return failure_reason(err)


__all__ = [
    "PORTFOLIO_SUMMARY_DESCRIPTION",
    "PortfolioSummaryInput",
    "register_portfolio_summary",
]
