"""`sector_rotation` MCP tool (Plan 0102 phase 3, ADR-0097).

Ranks the shipped crypto sector taxonomy (`analysis/sector_taxonomy.py`) by
equal-weighted constituent momentum over a caller `timeframe` + `lookback`, surfacing
which sectors are hot vs cold and the leaders/laggards within each. Crypto has no
canonical, fetchable sector index, so the taxonomy is defined in-house as versioned
config and momentum is synthesized from cached OHLCV we already fetch (ADR-0097) — no
new data source. The compute is the Plan 0102 `analysis/sectors.py` engine over the
Plan 0100 `_scan_symbols` fan-out (ADR-0095): trailing, deterministic, `as_of`-safe.

The body is factored as `_sector_rotation_response` so the whole scan / rank / skip
path is unit-testable on a single event loop with an injected fixture taxonomy (no
live MCP server needed). Conditions only — a rotation reading is a fact, never a
buy/sell call (ADR-0029): the response carries no action / signal / recommendation /
buy / sell field on any sector.
"""

from __future__ import annotations

from datetime import datetime

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.sector_taxonomy import CRYPTO_SECTOR_TAXONOMY, SectorTaxonomy
from market_analyser.analysis.sectors import rank_sectors
from market_analyser.analysis.types import SectorMomentum
from market_analyser.api.mcp_tools._validation import _require_supported_timeframe
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label

# The default trailing window (bars), matching the plan's first-behaviour example
# `sector_rotation("1d", lookback=30)`.
DEFAULT_LOOKBACK = 30

SECTOR_ROTATION_DESCRIPTION = (
    "Rank a self-defined set of crypto sectors (Layer-1, Layer-2, DeFi, Memecoins, AI, "
    "DePIN, ...) by equal-weighted constituent momentum over cached bars — the classic "
    "'where is capital rotating' read, for crypto. Crypto has no canonical sector index, "
    "so the taxonomy is an in-house versioned config (sector -> a basket of liquid "
    "USD-native constituents) and each sector's momentum is the equal-weighted mean of "
    "its constituents' trailing `lookback`-bar close-to-close returns. Returns "
    "{taxonomy_version, timeframe, lookback, sectors, scanned_at}: `sectors` are ranked "
    "hottest-first (complete sectors before incomplete ones, momentum descending), each "
    "carrying its equal-weight momentum, n_priced, a `complete` flag (>= the priced "
    "floor), its best/worst constituents (`leaders` / `laggards`, return %), and any "
    "`skipped` constituents (no cached bars / too short a history). A sector with too few "
    "priced constituents is reported `complete=false` and ranked last rather than "
    "silently mixed in; `momentum` is null when nothing priced. Pass `lookback` (bars, "
    "default 30) and `as_of` for historical replay (trailing — no future leak). "
    "Conditions only — a rotation reading is a fact about relative momentum, never a "
    "buy/sell call; use `recommend` for a directional call. Constituents are priced "
    "through the existing USD-native sources; backfill via get_ohlcv if a sector reports "
    f"many skipped. Supported timeframes: {supported_timeframes_label()}."
)


class SectorRotationResponse(BaseModel):
    """`sector_rotation` result. `sectors` are the taxonomy's sectors ranked by
    equal-weighted constituent momentum (hottest first; complete sectors ahead of
    incomplete ones). `taxonomy_version` dates the config the ranking used;
    `scanned_at` is the wall-clock run time (provenance).

    Conditions only (ADR-0029) — no call-shaped field on the response or any sector."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    taxonomy_version: str
    timeframe: str
    lookback: int
    sectors: list[SectorMomentum]
    scanned_at: datetime


async def _sector_rotation_response(
    *,
    provider: MarketDataProvider,
    timeframe: str,
    lookback: int,
    as_of: datetime | None,
    taxonomy: SectorTaxonomy = CRYPTO_SECTOR_TAXONOMY,
) -> SectorRotationResponse:
    """Body of the `sector_rotation` tool: validate the timeframe + lookback at the
    boundary (fail fast before any fan-out), then rank the taxonomy's sectors through
    the `analysis/sectors.py` engine. `taxonomy` is injectable so a test drives a fixture
    set without touching the shipped config."""

    _require_supported_timeframe(timeframe)
    if lookback < 1:
        raise ValueError(f"lookback must be >= 1 (got {lookback})")

    sectors, scanned_at = await rank_sectors(
        provider=provider,
        taxonomy=taxonomy,
        timeframe=timeframe,
        lookback=lookback,
        as_of=as_of,
    )
    return SectorRotationResponse(
        taxonomy_version=taxonomy.version,
        timeframe=timeframe,
        lookback=lookback,
        sectors=sectors,
        scanned_at=scanned_at,
    )


def register_sector_rotation(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `sector_rotation` tool to `server`. The provider is captured by closure
    so the tool body keeps the parameters FastMCP introspects for its input schema."""

    @server.tool(name="sector_rotation", description=SECTOR_ROTATION_DESCRIPTION)
    async def sector_rotation_tool(
        timeframe: str,
        lookback: int = DEFAULT_LOOKBACK,
        as_of: datetime | None = None,
    ) -> SectorRotationResponse:
        return await _sector_rotation_response(
            provider=provider,
            timeframe=timeframe,
            lookback=lookback,
            as_of=as_of,
        )


__all__ = [
    "DEFAULT_LOOKBACK",
    "SECTOR_ROTATION_DESCRIPTION",
    "SectorRotationResponse",
    "_sector_rotation_response",
    "register_sector_rotation",
]
