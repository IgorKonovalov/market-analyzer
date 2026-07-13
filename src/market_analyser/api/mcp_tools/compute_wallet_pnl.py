"""`compute_wallet_pnl` MCP tool (Plan 0035 phase 7).

The agent's entry point to DeFi profitability: paste a public EVM address, get
back the **reconstructed** per-position and total realized/unrealized P&L —
every number traced to decoded events priced at their own block timestamps
(ADR-0036), never an aggregator's opaque figure. Zerion's FIFO total rides
along only as an advisory cross-check.

The tool runs the async P&L job (which streams `defi.pnl_*` progress on the
SSE stream) and returns the `WalletPnl` dump. Input is boundary-validated
(`extra="forbid"`, `EVM_ADDRESS_PATTERN`); failures return the structured
`{positions: null, error, message}` shape with the same typed reasons as
`scan_wallet` — `auth` / `rate_limited` / `upstream_unavailable` /
`malformed_response`.

Dispatches through the `TxHistorySource` / `WalletPositionsSource` registries
(ADR-0031); concrete adapters are never imported for fetching — only Zerion's
typed error classes, to map them to a precise reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from market_analyser.data.adapters.zerion import ZerionAuthError, ZerionError
from market_analyser.data.errors import UpstreamDataError, failure_reason
from market_analyser.data.sources import (
    GaugeResolutionSource,
    HistoricalPriceSource,
    PnlCrosscheckSource,
    TxHistorySource,
    UnclaimedRewardsSource,
    WalletPositionsSource,
)
from market_analyser.defi.pnl_job import run_wallet_pnl
from market_analyser.defi.scan_job import EVM_ADDRESS_PATTERN
from market_analyser.events import EventBus
from market_analyser.persistence.defi_tx_repository import DefiTxRepository

_DEFAULT_SOURCE = "zerion"

COMPUTE_WALLET_PNL_DESCRIPTION = (
    "Reconstruct a wallet's DeFi profitability from its decoded on-chain "
    "transaction history (Ethereum, Base, Arbitrum, Optimism): per-position and "
    "total realized/unrealized P&L under average-cost lots, every leg valued at "
    "its own block timestamp - never trusting an aggregator's number. Returns "
    "{wallet (masked), positions: [{position_id, chain, pool_address (on-chain pool "
    "contract; null when the source omits it), is_lp, realized_usd, unrealized_usd, "
    "cost_basis_usd, vs_hodl_usd (LP only), incomplete, notes, windows, "
    "unclaimed_rewards}], "
    "position_count, incomplete, partial, incomplete_position_count, realized_usd, "
    "unrealized_usd, unclaimed_rewards, "
    "crosscheck_zerion_total, crosscheck_warning, error, message}. LP positions are "
    "the headline and are listed FIRST (is_lp=true); non-LP positions (lending, loose "
    "tokens, unpriceable exotics) follow, de-emphasized, and never suppress the LP "
    "figures. unclaimed_rewards "
    "is a labeled CURRENT-STATE on-chain read of gauge emissions owed-but-not-yet-"
    "claimed ([{symbol, amount, usd_value}], per position + a wallet roll-up); it is "
    "deliberately kept OUT of realized/unrealized and out of the deterministic re-run "
    "guarantee (there is no claim tx to replay), null when a position owes nothing. "
    "windows is the per-position P&L over a fixed rolling set "
    "([{window: 7d|30d|90d|all, realized_usd, total_return_usd, estimated}]): "
    "realized_usd is EXACT, anchored to the run's analysis time, with the 'all' window "
    "equal to the position's all-time realized_usd; total_return_usd is an ESTIMATE "
    "(estimated=true always) of realized-in-window plus the unrealized drift since the "
    "window start, null for any window whose start mark cannot be priced (an honest "
    "per-window gap that does NOT mark the position incomplete). "
    "A position with a missing historical "
    "price or an unbooked event kind reports null figures with incomplete=true "
    "and a naming note - never a silently-zeroed number. Wallet totals sum over "
    "the COMPLETE positions only (Plan 0088 / ADR-0082): an incomplete position "
    "is excluded - never zeroed, never nulling the wallet - with partial=true and "
    "incomplete_position_count flagging the exclusion. crosscheck_zerion_total is Zerion's "
    "own FIFO figure, advisory only; crosscheck_warning flags gross (order-of-"
    "magnitude or sign) divergence - small differences are expected because the "
    "methods differ (average-cost vs FIFO). refresh=true pulls new transactions "
    "before replaying; the default replays the immutable cached history "
    "(deterministic re-run, zero upstream calls). First pull of a long history "
    "is slow (rate-limit-spaced pagination + per-event price lookups); re-runs "
    "read SQLite. On failure positions is null and error is 'auth' (no Zerion "
    "API key set - set it via the Settings secret endpoint), 'rate_limited', "
    "'upstream_unavailable', or 'malformed_response'. address must be a raw 0x "
    "EVM address; ENS is not supported. Streams pnl_started/pnl_completed/"
    "pnl_failed on the SSE stream. Data from Zerion (history) + DefiLlama "
    "(historical prices)."
)


class ComputeWalletPnlInput(BaseModel):
    """MCP-boundary input. Unknown keys rejected; `address` must be a raw
    `0x…` EVM address. `refresh=true` gap-fetches before replaying."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    address: str = Field(pattern=EVM_ADDRESS_PATTERN)
    refresh: bool = False


def register_compute_wallet_pnl(
    server: FastMCP,
    *,
    tx_history_sources: Mapping[str, TxHistorySource],
    wallet_positions_sources: Mapping[str, WalletPositionsSource],
    historical_price_source: HistoricalPriceSource,
    defi_tx_repository: DefiTxRepository,
    event_bus: EventBus,
    gauge_source: GaugeResolutionSource | None = None,
    unclaimed_rewards_source: UnclaimedRewardsSource | None = None,
    dust_tokens: frozenset[str] = frozenset(),
) -> None:
    """Bind the `compute_wallet_pnl` tool to `server`. Dependencies are
    captured by closure so the tool body keeps its single declared parameter
    (FastMCP introspects it for the input schema). `gauge_source` /
    `unclaimed_rewards_source` are optional (Plan 0084): supplied, they resolve
    Aerodrome gauge emissions to the right position and read owed-but-unclaimed
    rewards; absent, the pre-0084 behavior holds."""
    tx_source = tx_history_sources[_DEFAULT_SOURCE]
    positions_source = wallet_positions_sources[_DEFAULT_SOURCE]
    crosscheck = tx_source if isinstance(tx_source, PnlCrosscheckSource) else None

    @server.tool(description=COMPUTE_WALLET_PNL_DESCRIPTION)
    async def compute_wallet_pnl(params: ComputeWalletPnlInput) -> dict[str, Any]:
        try:
            result = await run_wallet_pnl(
                tx_source=tx_source,
                positions_source=positions_source,
                price_source=historical_price_source,
                tx_repository=defi_tx_repository,
                event_bus=event_bus,
                address=params.address,
                refresh=params.refresh,
                crosscheck_source=crosscheck,
                gauge_source=gauge_source,
                unclaimed_source=unclaimed_rewards_source,
                dust_tokens=dust_tokens,
            )
        except ZerionAuthError as err:
            return _error("auth", err)
        except UpstreamDataError as err:
            return _error(failure_reason(err), err)
        except (ZerionError, ValidationError) as err:
            return _error("malformed_response", err)
        return {
            "wallet": result.wallet,
            "positions": [position.model_dump(mode="json") for position in result.positions],
            "position_count": len(result.positions),
            "incomplete": result.incomplete,
            "partial": result.partial,
            "incomplete_position_count": result.incomplete_position_count,
            "realized_usd": result.realized_usd,
            "unrealized_usd": result.unrealized_usd,
            "unclaimed_rewards": (
                [reward.model_dump(mode="json") for reward in result.unclaimed_rewards]
                if result.unclaimed_rewards is not None
                else None
            ),
            "crosscheck_zerion_total": result.crosscheck_zerion_total,
            "crosscheck_warning": result.crosscheck_warning,
            "error": None,
            "message": None,
        }


def _error(reason: str, err: Exception) -> dict[str, Any]:
    return {
        "wallet": None,
        "positions": None,
        "position_count": None,
        "incomplete": None,
        "partial": None,
        "incomplete_position_count": None,
        "realized_usd": None,
        "unrealized_usd": None,
        "unclaimed_rewards": None,
        "crosscheck_zerion_total": None,
        "crosscheck_warning": None,
        "error": reason,
        "message": str(err),
    }


__all__ = [
    "COMPUTE_WALLET_PNL_DESCRIPTION",
    "ComputeWalletPnlInput",
    "register_compute_wallet_pnl",
]
