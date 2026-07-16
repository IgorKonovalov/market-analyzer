"""`defi_fundamentals` MCP tool (Plan 0107 phase 2, ADR-0102, ADR-0031, ADR-0029).

Surfaces DeFi-native token/protocol fundamentals as a **condition read** — the
fundamentals price/structure is blind to for a small-cap DeFi token: TVL + short
history, DEX volume, fee/reward APR, token mcap/FDV, and the unlock/dilution
calendar. The read comes from a `DefiFundamentalsSource` resolved by name from an
injected **selector registry** (ADR-0031), keyed "defillama" at the DefiLlama
tier; the Aerodrome-native deep tier (Plan 0107 phases 4-5) enriches the *same*
source's payload rather than adding a registry entry, so this stays one tool.

Conditions only (ADR-0029): the returned `DefiFundamentals` carries **no**
`action`/`signal`/`recommendation` field — it reports what IS, never what to DO.
Wall-clock-sensitive with **no `as_of`** (ADR-0102): current-state protocol data
has no reconstructable point-in-time series, so the only input is the
symbol/protocol and the result stamps its own read time. Every field is
honest-null on miss with a `notes` entry — never a fabricated or zeroed number
(ADR-0019).

The source's `fetch_fundamentals` is synchronous and network-bound, so it is
offloaded with `asyncio.to_thread` to keep the event loop responsive. The body is
factored as `_defi_fundamentals_response` so the dispatch is unit-testable without
a live MCP server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from market_analyser.data.sources import DefiFundamentalsSource
from market_analyser.defi.models import DefiFundamentals, EmissionsDetail, VeGaugeStats

_DEFAULT_SOURCE = "defillama"


class AerodromeDeepReader(Protocol):
    """The Aerodrome-native deep tier the tool folds onto the DefiLlama payload for
    an Aerodrome query (Plan 0107 phase 5). Structural so the tool depends on the
    behavior, not the concrete `AerodromeNativeReader`; `read_aerodrome` appends its
    own provenance to `notes` and returns best-effort `None`s, never raising."""

    def read_aerodrome(
        self, notes: list[str]
    ) -> tuple[EmissionsDetail | None, VeGaugeStats | None]: ...


def _is_aerodrome(result: DefiFundamentals) -> bool:
    """Whether the DefiLlama-resolved result is Aerodrome — the one protocol the
    deep tier covers. Keyed off the resolver's `protocol_slug` (single source of
    truth) so the tool does not re-implement symbol resolution."""
    slug = result.protocol_slug
    return slug is not None and slug.lower().startswith("aerodrome")


DEFI_FUNDAMENTALS_DESCRIPTION = (
    "Read DeFi-native token/protocol fundamentals for a symbol or protocol slug "
    "(e.g. 'AERO', 'aerodrome', 'uniswap') — the fundamentals price/structure is "
    "blind to for a DeFi token. Returns {query, protocol_slug, tvl (USD), tvl_trend "
    "(trailing [date, value] history), dex_volume (24h/7d/30d USD + change_1d_pct), "
    "fee_apr, reward_apr (annualized %, TVL-weighted over the protocol's pools), "
    "mcap, fdv (USD), unlocks (token-unlock calendar), emissions_detail + ve_gauge "
    "(Aerodrome-only deep tier: weekly emission/decay + veAERO lock/vote weight, "
    "read on-chain; null for other protocols), as_of, source, notes}. "
    "Keyless (DefiLlama); any field DefiLlama does not cover comes back null with a "
    "`notes` entry explaining the gap (e.g. a token with no gecko_id has null mcap; "
    "the unlock calendar is DefiLlama-Pro-gated for many small caps) — never a "
    "fabricated or zeroed number. Wall-clock-sensitive: current-state only, no "
    "historical replay (no as_of). This is a CONDITION read, never buy/sell advice."
)


class DefiFundamentalsInput(BaseModel):
    """MCP-boundary input. Unknown keys are rejected (`extra="forbid"`). The single
    field is a token symbol or a DefiLlama protocol slug; the source resolves it to
    the upstream's keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol_or_protocol: str = Field(min_length=1)


async def _defi_fundamentals_response(
    *,
    sources: Mapping[str, DefiFundamentalsSource],
    source: str,
    symbol_or_protocol: str,
    deep_reader: AerodromeDeepReader | None = None,
) -> DefiFundamentals:
    """Body of the `defi_fundamentals` tool: resolve the primary source from the
    registry and dispatch, then — for an Aerodrome query when a deep reader is
    wired — fold the on-chain emission/veAERO deep fields onto the payload. A
    missing/unregistered source is a clear error, not a silent empty; the deep tier
    is best-effort and additive (it only enriches, never fails the read)."""

    src = sources.get(source)
    if src is None:
        raise ValueError(
            f"defi-fundamentals source {source!r} not configured (one of {sorted(sources)})"
        )
    result = await asyncio.to_thread(src.fetch_fundamentals, symbol_or_protocol)
    if deep_reader is None or not _is_aerodrome(result):
        return result
    notes = list(result.notes)
    emissions_detail, ve_gauge = await asyncio.to_thread(deep_reader.read_aerodrome, notes)
    return result.model_copy(
        update={
            "emissions_detail": emissions_detail,
            "ve_gauge": ve_gauge,
            "notes": notes,
        }
    )


def register_defi_fundamentals(
    server: FastMCP,
    *,
    fundamentals_sources: Mapping[str, DefiFundamentalsSource],
    source: str = _DEFAULT_SOURCE,
    deep_reader: AerodromeDeepReader | None = None,
) -> None:
    """Bind the `defi_fundamentals` tool to `server`. The source registry + optional
    Aerodrome deep reader are captured by closure so the tool body keeps its single
    declared parameter; `source` names the primary tier ("defillama"). `deep_reader`
    (wired from the Base RPC in the composition root) enriches an Aerodrome query
    with on-chain emission/veAERO fields; `None` leaves the DefiLlama depth."""

    @server.tool(name="defi_fundamentals", description=DEFI_FUNDAMENTALS_DESCRIPTION)
    async def defi_fundamentals(params: DefiFundamentalsInput) -> DefiFundamentals:
        return await _defi_fundamentals_response(
            sources=fundamentals_sources,
            source=source,
            symbol_or_protocol=params.symbol_or_protocol,
            deep_reader=deep_reader,
        )


__all__ = [
    "DEFI_FUNDAMENTALS_DESCRIPTION",
    "AerodromeDeepReader",
    "DefiFundamentalsInput",
    "_defi_fundamentals_response",
    "register_defi_fundamentals",
]
