"""`scan_wallet` MCP tool (Plan 0032 phase 4).

The agent's entry point to DeFi discovery: paste a public EVM address, get back
the decoded DeFi positions across Ethereum / Base / Arbitrum / Optimism. The tool
runs the async scan job (which streams `defi.scan_*` progress to the SSE stream
so a connected viewer can follow along) and returns the normalized positions.

Input is validated at the MCP boundary with an `extra="forbid"` Pydantic model;
`address` must match `EVM_ADDRESS_PATTERN` (a stray key or a non-address fails
loudly, never reaching the source). On failure the tool returns a structured
`{positions: null, error, message}` — the same typed-error courtesy `quote_for`
adopted — with a precise `error` reason: `auth` when no Zerion key is set (so the
agent can tell the user to set it), `rate_limited` / `upstream_unavailable` for
throttle / outage, or `malformed_response`.

Dispatches through the `WalletPositionsSource` registry (ADR-0031); the tool
selects the default source and never imports a concrete adapter for *fetching*
— only the adapter's typed error classes, to map them to a precise reason.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from market_analyser.data.adapters.zerion import ZerionAuthError, ZerionError
from market_analyser.data.errors import UpstreamDataError, failure_reason
from market_analyser.data.sources import LpPositionDetailSource, WalletPositionsSource
from market_analyser.defi.scan_job import EVM_ADDRESS_PATTERN, run_wallet_scan
from market_analyser.events import EventBus

# The default wallet-positions source for this plan (ADR-0034). The registry seam
# keeps this swappable — a later config could choose another source by name.
_DEFAULT_SOURCE = "zerion"

# The default LP-detail source (Plan 0034); the registry seam keeps it swappable.
_DEFAULT_LP_DETAIL_SOURCE = "rpc"

SCAN_WALLET_DESCRIPTION = (
    "Discover a wallet's DeFi positions from a public EVM address across Ethereum, "
    "Base, Arbitrum, and Optimism. Returns {wallet, positions, chains, "
    "position_count, total_usd_value, error, message}: positions is a list of "
    "decoded positions (each with chain, protocol, kind = lp|lending_supply|"
    "lending_borrow|staking, tokens, usd_value) on success; on failure positions "
    "is null and error is a typed reason — 'auth' (no Zerion API key is set: set "
    "it via the Settings secret endpoint, then retry), 'rate_limited', "
    "'upstream_unavailable', or 'malformed_response' — with a human message. "
    "address must be a raw 0x EVM address (40 hex chars); ENS names are not "
    "supported. Streams scan_started/scan_progress/scan_completed on the SSE "
    "stream. Positions are live (not persisted); values are the source's "
    "interpreted figures. When an on-chain RPC source is configured, LP positions "
    "are enriched (best-effort) with tick_lower/tick_upper/current_tick/in_range "
    "and uncollected_fees; without it those stay null. Data from Zerion (+ RPC)."
)


class ScanWalletInput(BaseModel):
    """MCP-boundary input. Unknown keys are rejected (`extra="forbid"`); `address`
    must be a raw `0x…` EVM address (validated against `EVM_ADDRESS_PATTERN`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    address: str = Field(pattern=EVM_ADDRESS_PATTERN)


def register_scan_wallet(
    server: FastMCP,
    *,
    wallet_positions_sources: Mapping[str, WalletPositionsSource],
    event_bus: EventBus,
    lp_detail_sources: Mapping[str, LpPositionDetailSource] | None = None,
) -> None:
    """Bind the `scan_wallet` tool to `server`. The sources + event bus are
    captured by closure so the tool body keeps its single declared parameter
    (FastMCP introspects it to build the input schema). The LP-detail source, when
    present, enriches discovered LP positions with on-chain detail (Plan 0034)."""
    source = wallet_positions_sources[_DEFAULT_SOURCE]
    lp_detail_source = (
        lp_detail_sources.get(_DEFAULT_LP_DETAIL_SOURCE) if lp_detail_sources else None
    )

    @server.tool(description=SCAN_WALLET_DESCRIPTION)
    async def scan_wallet(params: ScanWalletInput) -> dict[str, Any]:
        try:
            result = await run_wallet_scan(
                source=source,
                address=params.address,
                event_bus=event_bus,
                lp_detail_source=lp_detail_source,
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
            "chains": result.chains,
            "position_count": len(result.positions),
            "total_usd_value": result.total_usd_value,
            "error": None,
            "message": None,
        }


def _error(reason: str, err: Exception) -> dict[str, Any]:
    return {
        "wallet": None,
        "positions": None,
        "chains": None,
        "position_count": None,
        "total_usd_value": None,
        "error": reason,
        "message": str(err),
    }


__all__ = ["SCAN_WALLET_DESCRIPTION", "ScanWalletInput", "register_scan_wallet"]
