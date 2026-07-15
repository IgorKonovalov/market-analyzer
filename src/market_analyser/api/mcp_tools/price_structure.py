"""`price_structure` MCP tool (Plan 0109 phase 4, ADR-0104).

One single-symbol price-structure verb with a `kind` discriminator, folding the four
retired reads — `fibonacci_levels`, `pivot_points`, `anchored_vwap`, `market_structure`
— into modes of a single tool. `kind` ∈ {`fibonacci`, `pivots`, `anchored_vwap`,
`market_structure`}. Each mode reads cached bars through the `MarketDataProvider`
Protocol (ADR-0007) and dispatches through the *same* underlying pure compute unchanged
(`analysis.fibonacci` / `analysis.levels` / `analysis.volume` / `analysis.structure`).
Pure reads over cached bars — no chart events (these never drew, unlike `detect_levels`).

The four retired tools each returned `{result, partial_reason, scanned_at}`, so — like
the six watchlist scanners that shared `{matches, skipped, scanned_at}` (phase 1) — this
folds into ONE object with the discriminator added and the per-mode `result` a field
union: `PriceStructureResponse{kind, result, partial_reason, scanned_at}`. The `result` /
`partial_reason` / `scanned_at` fields are byte-identical to the retired tool's on the
same inputs; the envelope adds only the `kind` tag (a single object, so FastMCP does not
generically wrap it — a flatter, cleaner shape than nesting each old `*Response` under a
`result` key). `market_structure` keeps its ADR-0084 second-trend-read semantics as the
`market_structure` mode.

`as_of` is honoured — the window ends at `as_of` and is passed to the provider, which
truncates to `event_ts <= as_of` (anti-lookahead replay for free). The single fetch is
shared across modes (all four read the same window); the synchronous compute is offloaded
with `asyncio.to_thread`. The body is factored as `_price_structure_response` so every
mode's fetch / empty-cache / no-swing path is unit-testable on a single event loop (no
live MCP server). Conditions only — chart geometry, never a buy/sell call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.fibonacci import (
    dominant_swing,
    fibonacci_extension,
    fibonacci_retracement,
)
from market_analyser.analysis.levels import PivotMethod, pivot_points
from market_analyser.analysis.structure import market_structure as compute_market_structure
from market_analyser.analysis.types import (
    AnchoredVwapValue,
    FibonacciLevels,
    MarketStructure,
    PivotPoint,
    PivotPoints,
)
from market_analyser.analysis.volume import anchored_vwap_value
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import max_history, supported_timeframes_label
from market_analyser.data.types import Bar

# Fetch window: the timeframe's feed-limited history, or a generous default for the
# unbounded cadences — wide enough for the auto-anchor's swing lookback / structure build.
_DEFAULT_WINDOW = timedelta(days=5 * 365)

PriceStructureKind = Literal["fibonacci", "pivots", "anchored_vwap", "market_structure"]
FibKind = Literal["retracement", "extension"]

# One `price_structure` result payload — the mode's existing model, byte-identical. Which
# member appears is fixed by `kind` (the response discriminator).
PriceStructureResult = FibonacciLevels | PivotPoints | AnchoredVwapValue | MarketStructure

PRICE_STRUCTURE_DESCRIPTION = (
    "Read a single-symbol price-structure overlay on cached bars; `kind` selects the "
    "read. Returns {kind, result, partial_reason, scanned_at}: result is the mode's "
    "geometry (null with partial_reason when uncomputable), scanned_at is run "
    "provenance. Modes: "
    "kind='fibonacci' — a Fibonacci grid auto-anchored to the dominant recent swing "
    "(FibonacciLevels: the grid kind, high/low anchors, swing direction, ratio->price "
    "levels); fibonacci.kind='retracement' (default) draws inside the swing, 'extension' "
    "projects beyond it off the last close; partial_reason='no_swing' when the bars hold "
    "no dominant swing. "
    "kind='pivots' — classic pivot levels from the last completed bar's HLC "
    "(PivotPoints: central pivot, R1-R3, S1-S3); pivots.method='floor' (default), "
    "'camarilla', or 'woodie'. "
    "kind='anchored_vwap' — the anchored VWAP accumulated from a chosen bar "
    "(AnchoredVwapValue: anchor_index, anchor_ts, latest value); omit "
    "anchored_vwap.anchor_index to auto-anchor to the dominant swing's start (first bar "
    "if none), or pass an explicit 0-based index. "
    "kind='market_structure' — the price-action structure (MarketStructure: "
    "structural_trend from the HH/HL/LH/LL swing sequence, labeled_pivots, BOS/CHoCH "
    "events); this is a SECOND, distinct trend read reported ALONGSIDE analyze_symbol's "
    "indicator trend — disagreement is itself the signal, never merged. "
    "partial_reason='no_bars' (any mode) when nothing is cached (backfill via get_ohlcv "
    "first). Strictly trailing: reads only bars at-or-before the last one. Pass `as_of` "
    "for historical replay (no future leak). Conditions only — never buy/sell advice. "
    f"Supported timeframes: {supported_timeframes_label()}."
)


class FibonacciOpts(BaseModel):
    """Per-mode options for `kind="fibonacci"` — retracement (inside the swing) vs
    extension (projected off the last close). Ignored by every other mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: FibKind = "retracement"


class PivotsOpts(BaseModel):
    """Per-mode options for `kind="pivots"` — the pivot formula set. Ignored by every
    other mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: PivotMethod = "floor"


class AnchoredVwapOpts(BaseModel):
    """Per-mode options for `kind="anchored_vwap"` — an explicit 0-based anchor bar, or
    None to auto-anchor to the dominant swing's start. Ignored by every other mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor_index: int | None = None


class PriceStructureResponse(BaseModel):
    """`price_structure` result, discriminated by `kind`. `result` is the mode's geometry
    (a `FibonacciLevels` / `PivotPoints` / `AnchoredVwapValue` / `MarketStructure`), or
    `None` with `partial_reason` ``no_bars`` (nothing cached) / ``no_swing`` (fibonacci
    with no dominant swing). `scanned_at` is the wall-clock run time (run provenance).

    Conditions only — chart geometry, never a call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: PriceStructureKind
    result: PriceStructureResult | None
    partial_reason: Literal["no_bars", "no_swing"] | None
    scanned_at: datetime


def _fibonacci_levels(bars: Sequence[Bar], kind: FibKind) -> FibonacciLevels | None:
    """The Fibonacci sync core: auto-anchor to the dominant swing and build the grid, or
    `None` when there is no dominant swing. For an extension the pullback anchor is the
    last bar's close."""

    swing = dominant_swing(bars)
    if swing is None:
        return None
    high_anchor, low_anchor = swing
    if kind == "extension":
        last = bars[-1]
        pullback = PivotPoint(ts=last.event_ts, price=last.close)
        return fibonacci_extension(high_anchor, low_anchor, pullback)
    return fibonacci_retracement(high_anchor, low_anchor)


def _resolve_anchor(bars: Sequence[Bar], anchor_index: int | None) -> int:
    """The anchored-VWAP anchor bar index: an explicit `anchor_index` (validated in
    range), or the auto-anchor — the start (earlier pivot) of the dominant recent swing,
    falling back to the first bar when there is no dominant swing."""

    if anchor_index is not None:
        if not 0 <= anchor_index < len(bars):
            raise ValueError(f"anchor_index {anchor_index} out of range for {len(bars)} bars")
        return anchor_index
    swing = dominant_swing(bars)
    if swing is None:
        return 0
    high_anchor, low_anchor = swing
    start = min(high_anchor, low_anchor, key=lambda a: a.ts)  # the swing's earlier pivot
    return next(i for i, b in enumerate(bars) if b.event_ts == start.ts)


def _anchored_vwap(bars: Sequence[Bar], anchor_index: int | None) -> AnchoredVwapValue:
    """The anchored-VWAP sync core: resolve the anchor and compose the latest value."""

    return anchored_vwap_value(bars, _resolve_anchor(bars, anchor_index))


async def _price_structure_response(
    *,
    provider: MarketDataProvider,
    symbol: str,
    timeframe: str,
    kind: PriceStructureKind,
    fibonacci: FibonacciOpts | None = None,
    pivots: PivotsOpts | None = None,
    anchored_vwap: AnchoredVwapOpts | None = None,
    as_of: datetime | None,
) -> PriceStructureResponse:
    """Body of the `price_structure` tool: validate at the boundary, read the shared bar
    window once, then dispatch the selected mode's unchanged compute and wrap it in the
    discriminated envelope. The compute per mode is exactly the retired tool's, so
    `result` / `partial_reason` are byte-identical on the same inputs."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - (max_history(timeframe) or _DEFAULT_WINDOW)
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
    now = datetime.now(tz=UTC)
    if not bars:
        return PriceStructureResponse(
            kind=kind, result=None, partial_reason="no_bars", scanned_at=now
        )

    bar_list = list(bars)
    result: PriceStructureResult | None
    partial_reason: Literal["no_bars", "no_swing"] | None = None

    if kind == "fibonacci":
        fib = fibonacci if fibonacci is not None else FibonacciOpts()
        result = await asyncio.to_thread(_fibonacci_levels, bar_list, fib.kind)
        if result is None:
            partial_reason = "no_swing"
    elif kind == "pivots":
        method = (pivots if pivots is not None else PivotsOpts()).method
        result = await asyncio.to_thread(pivot_points, bar_list, method)
    elif kind == "anchored_vwap":
        av = anchored_vwap if anchored_vwap is not None else AnchoredVwapOpts()
        result = await asyncio.to_thread(_anchored_vwap, bar_list, av.anchor_index)
    else:  # market_structure
        result = await asyncio.to_thread(compute_market_structure, bar_list)

    return PriceStructureResponse(
        kind=kind, result=result, partial_reason=partial_reason, scanned_at=now
    )


def register_price_structure(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `price_structure` tool to `server`. The provider is captured by closure
    so the tool body keeps the parameters FastMCP introspects."""

    @server.tool(name="price_structure", description=PRICE_STRUCTURE_DESCRIPTION)
    async def price_structure_tool(
        symbol: str,
        timeframe: str,
        kind: PriceStructureKind,
        fibonacci: FibonacciOpts | None = None,
        pivots: PivotsOpts | None = None,
        anchored_vwap: AnchoredVwapOpts | None = None,
        as_of: datetime | None = None,
    ) -> PriceStructureResponse:
        return await _price_structure_response(
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            kind=kind,
            fibonacci=fibonacci,
            pivots=pivots,
            anchored_vwap=anchored_vwap,
            as_of=as_of,
        )


__all__ = [
    "PRICE_STRUCTURE_DESCRIPTION",
    "AnchoredVwapOpts",
    "FibonacciOpts",
    "PivotsOpts",
    "PriceStructureKind",
    "PriceStructureResponse",
    "PriceStructureResult",
    "_price_structure_response",
    "register_price_structure",
]
