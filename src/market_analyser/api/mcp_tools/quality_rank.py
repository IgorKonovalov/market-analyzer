"""`quality_rank` MCP tool (Plan 0101 phase 2, ADR-0096 / ADR-0095).

Ranks a supplied symbol list (watchlist) by a composite technical-quality score on
cached bars: for each symbol it reads the trailing condition snapshot, fuses it into
a normalized 0..100 composite decomposed into trend/momentum/volume/volatility
contributions (that sum to the score), applies a per-asset-class liquidity gate, and
returns the symbols sorted by score descending. Symbols with too short a history to
score (or no cached bars / a fetch error) are skipped and reported in `skipped`; they
never fail the whole scan.

The fan-out, cap, `as_of` anti-lookahead truncation, skip discipline, and
`scanned_at` stamp are the shared `_scan_symbols` harness (ADR-0095); this module
supplies only the quality scorer (`analysis/quality.py`) + sort key and wraps the
result. The body is factored as `_quality_rank_response` so the scan / skip paths are
unit-testable on a single event loop (no live MCP server needed).

A **screening rank, not a call** (ADR-0096, on the ADR-0029 conditions side): the
response carries no action / signal / recommendation / grade field. For a directional
call, use `recommend` (the advisor) — which may itself consume this rank.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.quality import score_quality
from market_analyser.analysis.scanners import MAX_SCAN_SYMBOLS, _scan_symbols, _ScanSkip
from market_analyser.analysis.types import QualityScore
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label
from market_analyser.data.types import Bar

QUALITY_RANK_DESCRIPTION = (
    "Rank a supplied symbol list (watchlist) by a composite technical-quality score "
    "on cached bars. For each symbol a normalized 0..100 score is fused from its "
    "trailing condition snapshot and decomposed into four named factor contributions "
    "(trend, momentum, volume, volatility) that SUM to the score, plus a per-asset-"
    "class liquidity gate that flags/caps thin names. Returns {matches, skipped, "
    "scanned_at}: matches are the whole watchlist ranked by score descending "
    "(highest-quality setup first), each carrying its factors, liquidity_ok, and an "
    "optional liquidity_note, ties broken by symbol; skipped lists symbols with too "
    "short a history to score or no cached bars (backfill via get_ohlcv first). Max "
    f"{MAX_SCAN_SYMBOLS} symbols. Pass `as_of` for historical replay (trailing — no "
    "future leak). This is a SCREENING RANK, conditions only — NOT a recommendation "
    "(no buy/sell, no grade); use `recommend` for a directional call. Supported "
    f"timeframes: {supported_timeframes_label()}."
)


class QualityRankResponse(BaseModel):
    """`quality_rank` result. `matches` are the scanned symbols ranked by composite
    `score` descending (highest-quality setup first), tie-broken by symbol; `skipped`
    lists symbols with too short a history to score or no cached bars / a fetch error;
    `scanned_at` is the wall-clock run time (provenance).

    A screening rank, never a call (ADR-0096) — no call-shaped field anywhere."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    matches: list[QualityScore]
    skipped: list[str]
    scanned_at: datetime


async def _quality_rank_response(
    *,
    provider: MarketDataProvider,
    symbols: list[str],
    timeframe: str,
    as_of: datetime | None,
) -> QualityRankResponse:
    """Body of the `quality_rank` tool. Delegates the fan-out to `_scan_symbols`
    (ADR-0095), scoring each symbol's composite quality and ranking by `score`
    descending (highest first, ties by symbol)."""

    def _score(bars: Sequence[Bar]) -> QualityScore | _ScanSkip:
        return score_quality(bars, timeframe)

    matches, skipped, scanned_at = await _scan_symbols(
        provider=provider,
        symbols=symbols,
        timeframe=timeframe,
        score=_score,
        sort_key=lambda m: (-m.score, m.symbol),
        as_of=as_of,
        tool_name="quality_rank",
    )
    return QualityRankResponse(matches=matches, skipped=skipped, scanned_at=scanned_at)


def register_quality_rank(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `quality_rank` tool to `server`. The provider is captured by closure
    so the tool body keeps the parameters FastMCP introspects."""

    @server.tool(name="quality_rank", description=QUALITY_RANK_DESCRIPTION)
    async def quality_rank_tool(
        symbols: list[str],
        timeframe: str,
        as_of: datetime | None = None,
    ) -> QualityRankResponse:
        return await _quality_rank_response(
            provider=provider,
            symbols=symbols,
            timeframe=timeframe,
            as_of=as_of,
        )


__all__ = [
    "QUALITY_RANK_DESCRIPTION",
    "QualityRankResponse",
    "_quality_rank_response",
    "register_quality_rank",
]
