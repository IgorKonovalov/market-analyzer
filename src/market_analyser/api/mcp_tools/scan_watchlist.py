"""`scan_watchlist` MCP tool (Plan 0109 phase 1, ADR-0104).

One watchlist-ranking verb with a `rank_by` discriminator, folding the six same-verb
scanners ADR-0104 identified — `squeeze_scan`, `gainers_losers`, `momentum_scan`,
`quality_rank`, `volume_breakout`, `smart_volume` — into modes of a single tool. Each
mode ranks/filters a caller-supplied symbol list on a condition read from cached bars,
and every mode dispatches through the *same* underlying pure compute unchanged: the
`analysis/scanners.py::_scan_symbols` fan-out harness (ADR-0095) with the mode's scorer
and sort key, or (for the two volume modes) the `analysis/volume.py` conditions. No
computation changes here — this is the ADR-0104 surface refactor, not a behaviour
change; determinism, the `as_of` anti-lookahead truncation, the skip discipline, and
the ADR-0029/0096 conditions-only stance are inherited from the compute layer.

`rank_by` values:

- ``squeeze`` — rank by squeeze tightness (ADR-0083 trio), most-coiled first
  (`bb_width_pct90` ascending). Was `squeeze_scan`.
- ``gainers`` — rank by trailing close-to-close % change descending, biggest gainer
  first. Was `gainers_losers`.
- ``losers`` — the same close-to-close move ranked ascending, biggest loser first
  (the mirror of ``gainers``; same scorer, opposite sort).
- ``momentum`` — filter by an RSI band + optional trend (NO volume gate), ranked by
  RSI descending. Per-mode opts in the `momentum` block. Was `momentum_scan`.
- ``quality`` — rank by the composite 0..100 technical-quality score descending
  (ADR-0096 screening rank, conditions only — never a call). Was `quality_rank`.
- ``volume_breakout`` — keep only price+volume breakouts, ranked by relative-volume
  multiple descending. Per-mode opts in the `volume_breakout` block. Was
  `volume_breakout`.
- ``smart_volume`` — keep only volume-surge-with-RSI-in-band hits, ranked by multiple
  descending. Per-mode opts in the `smart_volume` block. Was `smart_volume`.

Each mode's extra parameters live in a nested per-mode opts block so the discriminator
itself stays the routing signal (ADR-0104); a mode with no extra parameters (squeeze,
gainers, losers, quality) takes only `symbols`/`timeframe`/`as_of`. The result is a
frozen `ScanWatchlistResponse` discriminated by `rank_by`: its `matches` list carries
the byte-identical per-mode match model the retired tool returned (a `SqueezeScanMatch`,
`GainersLosersMatch`, `MomentumScanMatch`, `QualityScore`, `VolumeBreakout`, or
`SmartVolumeHit`), alongside `skipped` and the `scanned_at` provenance stamp.

The body is factored as `_scan_watchlist_response` so every mode's scan / skip / filter
path is unit-testable on a single event loop (no live MCP server needed).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal, cast

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.quality import score_quality
from market_analyser.analysis.scanners import (
    MAX_SCAN_SYMBOLS,
    GainersLosersMatch,
    MomentumScanMatch,
    SqueezeScanMatch,
    _scan_symbols,
    _ScanSkip,
    score_momentum,
    score_return,
    score_squeeze,
)
from market_analyser.analysis.types import (
    QualityScore,
    SmartVolumeHit,
    Trend,
    VolumeBreakout,
)
from market_analyser.analysis.volume import (
    BREAKOUT_PRICE_LOOKBACK,
    BREAKOUT_VOL_MULTIPLE,
    SMART_RSI_HIGH,
    SMART_RSI_LOW,
    SMART_VOL_MULTIPLE,
)
from market_analyser.analysis.volume import smart_volume as smart_volume_condition
from market_analyser.analysis.volume import volume_breakout as volume_breakout_condition
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label
from market_analyser.data.types import Bar

RankBy = Literal[
    "squeeze",
    "gainers",
    "losers",
    "momentum",
    "quality",
    "volume_breakout",
    "smart_volume",
]

# One item of a `scan_watchlist` result — the per-mode match model the retired tool
# returned, byte-identical. Which member appears is fixed by `rank_by` (the response
# discriminator), so the list is never mixed within one call.
ScanWatchlistMatch = (
    SqueezeScanMatch
    | GainersLosersMatch
    | MomentumScanMatch
    | QualityScore
    | VolumeBreakout
    | SmartVolumeHit
)

_TREND_VALUES = tuple(t.value for t in Trend)

SCAN_WATCHLIST_DESCRIPTION = (
    "Rank or filter a supplied symbol list (watchlist) on cached bars by a chosen "
    "condition — one watchlist-ranking verb, `rank_by` selects the mode. Returns "
    "{rank_by, matches, skipped, scanned_at}: matches carry the mode's per-symbol "
    "reading (tie-broken by symbol); skipped lists symbols with too short a history "
    "for the mode or no cached bars (backfill via get_ohlcv first). Modes: "
    "`squeeze` ranks by TTM squeeze tightness (ADR-0083 trio: bb_width, its trailing "
    "90-window percentile, squeeze_on), most-coiled first; `gainers` ranks by "
    "trailing close-to-close % change descending (biggest gainer first) and `losers` "
    "the same move ascending (biggest loser first); `momentum` filters by an RSI band "
    "[momentum.rsi_min, momentum.rsi_max] and optional momentum.trend (one of "
    f"{', '.join(_TREND_VALUES)}), NO volume gate, ranked by RSI descending; "
    "`quality` ranks by a composite 0..100 technical-quality score descending "
    "(four factor contributions that sum to the score, plus a liquidity gate); "
    "`volume_breakout` keeps only price+volume breakouts (volume_breakout.vol_multiple "
    "x trailing average AND clearing the volume_breakout.price_lookback-bar high/low), "
    "ranked by multiple descending; `smart_volume` keeps only a volume surge "
    "(smart_volume.vol_multiple x average) with RSI inside [smart_volume.rsi_low, "
    "smart_volume.rsi_high], ranked by multiple descending. Each mode's extra params "
    "live in a nested block named for the mode; modes without extra params take only "
    f"symbols/timeframe/as_of. Max {MAX_SCAN_SYMBOLS} symbols. Pass `as_of` for "
    "historical replay (trailing — no future leak). Conditions only — a ranking is a "
    "fact, never buy/sell advice (the `quality` mode is a SCREENING RANK, not a "
    "recommendation — use `recommend` for a directional call). Supported timeframes: "
    f"{supported_timeframes_label()}."
)


class MomentumOpts(BaseModel):
    """Per-mode options for `rank_by="momentum"` — the RSI band + optional trend
    filter. Ignored by every other mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rsi_min: float = 0.0
    rsi_max: float = 100.0
    trend: str | None = None


class VolumeBreakoutOpts(BaseModel):
    """Per-mode options for `rank_by="volume_breakout"` — the volume multiple and the
    price-range lookback. Ignored by every other mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vol_multiple: float = BREAKOUT_VOL_MULTIPLE
    price_lookback: int = BREAKOUT_PRICE_LOOKBACK


class SmartVolumeOpts(BaseModel):
    """Per-mode options for `rank_by="smart_volume"` — the RSI band and the volume
    multiple. Ignored by every other mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rsi_low: float = SMART_RSI_LOW
    rsi_high: float = SMART_RSI_HIGH
    vol_multiple: float = SMART_VOL_MULTIPLE


class ScanWatchlistResponse(BaseModel):
    """`scan_watchlist` result, discriminated by `rank_by`. `matches` are the ranked
    symbols — the per-mode match model the retired single-mode tool returned, sorted
    by that mode's key (tie-broken by symbol); `skipped` lists symbols scanned but
    uncomputable (too short a history) or with no cached bars / a fetch error;
    `scanned_at` is the wall-clock run time (provenance).

    Conditions only (ADR-0029/0096) — no call-shaped field on any mode's match."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rank_by: RankBy
    matches: list[ScanWatchlistMatch]
    skipped: list[str]
    scanned_at: datetime


async def _dispatch[M: BaseModel](
    *,
    provider: MarketDataProvider,
    symbols: list[str],
    timeframe: str,
    as_of: datetime | None,
    rank_by: RankBy,
    score: Callable[[Sequence[Bar]], M | _ScanSkip | None],
    sort_key: Callable[[M], Any],
) -> ScanWatchlistResponse:
    """Run one mode's scan through the shared `_scan_symbols` harness (ADR-0095) and
    wrap it in the discriminated response. The per-mode match type `M` is widened to
    the response union at the boundary — every member is a valid `ScanWatchlistMatch`."""

    matches, skipped, scanned_at = await _scan_symbols(
        provider=provider,
        symbols=symbols,
        timeframe=timeframe,
        score=score,
        sort_key=sort_key,
        as_of=as_of,
        tool_name="scan_watchlist",
    )
    return ScanWatchlistResponse(
        rank_by=rank_by,
        matches=cast("list[ScanWatchlistMatch]", matches),
        skipped=skipped,
        scanned_at=scanned_at,
    )


async def _scan_watchlist_response(
    *,
    provider: MarketDataProvider,
    symbols: list[str],
    timeframe: str,
    rank_by: RankBy,
    momentum: MomentumOpts | None = None,
    volume_breakout: VolumeBreakoutOpts | None = None,
    smart_volume: SmartVolumeOpts | None = None,
    as_of: datetime | None,
) -> ScanWatchlistResponse:
    """Body of the `scan_watchlist` tool: validate the selected mode's per-mode opts
    at the boundary, then dispatch through the mode's unchanged compute. Each branch
    supplies exactly the scorer + sort key its retired single-mode tool used, so the
    ranked payload is byte-identical to that tool's on the same inputs."""

    if rank_by == "squeeze":

        def _score_squeeze(bars: Sequence[Bar]) -> SqueezeScanMatch | _ScanSkip:
            return score_squeeze(bars, timeframe)

        return await _dispatch(
            provider=provider,
            symbols=symbols,
            timeframe=timeframe,
            as_of=as_of,
            rank_by=rank_by,
            score=_score_squeeze,
            sort_key=lambda m: (m.bb_width_pct90, m.symbol),
        )

    if rank_by in ("gainers", "losers"):
        # Same close-to-close scorer; the two modes differ only by sort direction —
        # gainers rank the move descending (biggest gainer first), losers ascending
        # (biggest loser first). Ties by symbol either way.
        sign = 1.0 if rank_by == "gainers" else -1.0
        return await _dispatch(
            provider=provider,
            symbols=symbols,
            timeframe=timeframe,
            as_of=as_of,
            rank_by=rank_by,
            score=score_return,
            sort_key=lambda m: (-sign * m.change_pct, m.symbol),
        )

    if rank_by == "momentum":
        opts = momentum if momentum is not None else MomentumOpts()
        if opts.rsi_min > opts.rsi_max:
            raise ValueError(f"rsi_min {opts.rsi_min} must be <= rsi_max {opts.rsi_max}")
        if opts.trend is not None and opts.trend not in _TREND_VALUES:
            raise ValueError(f"trend {opts.trend!r} not supported (one of {list(_TREND_VALUES)})")

        def _score_momentum(bars: Sequence[Bar]) -> MomentumScanMatch | _ScanSkip | None:
            return score_momentum(
                bars,
                timeframe,
                rsi_min=opts.rsi_min,
                rsi_max=opts.rsi_max,
                trend=opts.trend,
            )

        return await _dispatch(
            provider=provider,
            symbols=symbols,
            timeframe=timeframe,
            as_of=as_of,
            rank_by=rank_by,
            score=_score_momentum,
            sort_key=lambda m: (-m.rsi, m.symbol),
        )

    if rank_by == "quality":

        def _score_quality(bars: Sequence[Bar]) -> QualityScore | _ScanSkip:
            return score_quality(bars, timeframe)

        return await _dispatch(
            provider=provider,
            symbols=symbols,
            timeframe=timeframe,
            as_of=as_of,
            rank_by=rank_by,
            score=_score_quality,
            sort_key=lambda m: (-m.score, m.symbol),
        )

    if rank_by == "volume_breakout":
        vb_opts = volume_breakout if volume_breakout is not None else VolumeBreakoutOpts()

        def _score_breakout(bars: Sequence[Bar]) -> VolumeBreakout | None:
            result = volume_breakout_condition(bars, vb_opts.vol_multiple, vb_opts.price_lookback)
            return result if result.is_breakout else None

        return await _dispatch(
            provider=provider,
            symbols=symbols,
            timeframe=timeframe,
            as_of=as_of,
            rank_by=rank_by,
            score=_score_breakout,
            sort_key=lambda r: (-(r.volume_multiple or 0.0), r.symbol),
        )

    # rank_by == "smart_volume"
    sv_opts = smart_volume if smart_volume is not None else SmartVolumeOpts()
    if sv_opts.rsi_low > sv_opts.rsi_high:
        raise ValueError(f"rsi_low {sv_opts.rsi_low} must be <= rsi_high {sv_opts.rsi_high}")

    def _score_smart(bars: Sequence[Bar]) -> SmartVolumeHit | None:
        result = smart_volume_condition(
            bars, sv_opts.rsi_low, sv_opts.rsi_high, sv_opts.vol_multiple
        )
        return result if result.qualifies else None

    return await _dispatch(
        provider=provider,
        symbols=symbols,
        timeframe=timeframe,
        as_of=as_of,
        rank_by=rank_by,
        score=_score_smart,
        sort_key=lambda r: (-(r.volume_multiple or 0.0), r.symbol),
    )


def register_scan_watchlist(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `scan_watchlist` tool to `server`. The provider is captured by closure
    so the tool body keeps the parameters FastMCP introspects."""

    @server.tool(name="scan_watchlist", description=SCAN_WATCHLIST_DESCRIPTION)
    async def scan_watchlist_tool(
        symbols: list[str],
        timeframe: str,
        rank_by: RankBy,
        momentum: MomentumOpts | None = None,
        volume_breakout: VolumeBreakoutOpts | None = None,
        smart_volume: SmartVolumeOpts | None = None,
        as_of: datetime | None = None,
    ) -> ScanWatchlistResponse:
        return await _scan_watchlist_response(
            provider=provider,
            symbols=symbols,
            timeframe=timeframe,
            rank_by=rank_by,
            momentum=momentum,
            volume_breakout=volume_breakout,
            smart_volume=smart_volume,
            as_of=as_of,
        )


__all__ = [
    "MAX_SCAN_SYMBOLS",
    "SCAN_WATCHLIST_DESCRIPTION",
    "MomentumOpts",
    "RankBy",
    "ScanWatchlistMatch",
    "ScanWatchlistResponse",
    "SmartVolumeOpts",
    "VolumeBreakoutOpts",
    "_scan_watchlist_response",
    "register_scan_watchlist",
]
