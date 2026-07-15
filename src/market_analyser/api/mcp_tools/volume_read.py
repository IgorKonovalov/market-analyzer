"""`volume_read` MCP tool (Plan 0109 phase 5, ADR-0104).

One single-symbol volume-read verb with a `kind` discriminator, folding the two retired
reads — `volume_confirmation` (Plan 0021) and `counter_trend_volume` (Plan 0090) — into
modes of a single tool. `kind` ∈ {`confirmation`, `counter_trend`}. Each mode reads cached
bars through the `MarketDataProvider` Protocol (ADR-0007) and dispatches through the same
underlying `analysis.volume` compute unchanged.

Both retired tools returned `{result, partial_reason, scanned_at}`, so — like the
watchlist scanners (phase 1) and price-structure reads (phase 4) — this folds into ONE
object with the discriminator added and the per-mode `result` a field union:
`VolumeReadResponse{kind, result, partial_reason, scanned_at}`. The `result` /
`partial_reason` / `scanned_at` fields are byte-identical to the retired tool's on the
same inputs; the envelope adds only the `kind` tag (a single object, so FastMCP does not
generically wrap it). `counter_trend`'s anchoring to the canonical snapshot trend
(ADR-0083 — the same up/down/sideways label `analyze_symbol` reports) is preserved.

`as_of` is honoured — the window ends at `as_of` and is passed to the provider, which
truncates to `event_ts <= as_of` (anti-lookahead replay for free). The single fetch is
shared across modes. The body is factored as `_volume_read_response` so every mode's
fetch / empty-cache path is unit-testable on a single event loop (no live MCP server).
Conditions only — never a buy/sell call.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.analysis.types import CounterTrendVolume, VolumeConfirmation
from market_analyser.analysis.volume import (
    CONFIRMATION_LOOKBACK,
    COUNTER_TREND_LOOKBACK,
    counter_trend_volume,
    volume_confirmation,
)
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import max_history, supported_timeframes_label
from market_analyser.data.types import Bar

# Fetch window: the timeframe's feed-limited history, or a generous default for the
# unbounded cadences — wide enough for the trend classifier's longest window (Ichimoku
# span_b + displacement) plus the read's lookback.
_DEFAULT_WINDOW = timedelta(days=5 * 365)

VolumeReadKind = Literal["confirmation", "counter_trend"]

# One `volume_read` result payload — the mode's existing model, byte-identical. Which
# member appears is fixed by `kind` (the response discriminator).
VolumeReadResult = VolumeConfirmation | CounterTrendVolume

VOLUME_READ_DESCRIPTION = (
    "Read one symbol's recent volume against its price move on cached bars; `kind` "
    "selects the read. Returns {kind, result, partial_reason, scanned_at}: result is the "
    "mode's read (null with partial_reason='no_bars' when nothing is cached — backfill "
    "via get_ohlcv first), scanned_at is run provenance. Modes: "
    "kind='confirmation' — how well volume backs the recent move (VolumeConfirmation: "
    "score, a 0..1 share of directional volume aligned with the net move over the "
    "trailing confirmation.lookback bars — high when the move is carried by trend "
    "volume, low on a counter-trend divergence — plus confirmed, direction, and the "
    "supportive/opposing volume figures). "
    "kind='counter_trend' — the volume decomposed with-trend vs counter-trend, anchored "
    "to the symbol's canonical trend (the same up/down/sideways label analyze_symbol "
    "reports, NOT the net move): result.bars lists each trailing counter_trend.lookback "
    "bar with its direction, trailing relative volume, and counter-trend flag, and "
    "result.counter_trend_volume_share is the share of directional volume on the "
    "counter-trend bars (high = a volume divergence against the trend); when the trend "
    "is sideways there is nothing to run counter to, anchored_to_sideways is true and "
    "the share is null. Pass `as_of` for historical replay (trailing — no future leak). "
    "Conditions only — never buy/sell advice. Supported timeframes: "
    f"{supported_timeframes_label()}."
)


class ConfirmationOpts(BaseModel):
    """Per-mode options for `kind="confirmation"` — the trailing window length. Ignored
    by every other mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lookback: int = CONFIRMATION_LOOKBACK


class CounterTrendOpts(BaseModel):
    """Per-mode options for `kind="counter_trend"` — the trailing window length. Ignored
    by every other mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lookback: int = COUNTER_TREND_LOOKBACK


class VolumeReadResponse(BaseModel):
    """`volume_read` result, discriminated by `kind`. `result` is the mode's read (a
    `VolumeConfirmation` / `CounterTrendVolume`), or `None` with
    `partial_reason="no_bars"` when the cache holds nothing for the symbol. `scanned_at`
    is the wall-clock run time (run provenance). Conditions only — never a call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: VolumeReadKind
    result: VolumeReadResult | None
    partial_reason: Literal["no_bars"] | None
    scanned_at: datetime


def _counter_trend_volume(bars: list[Bar], timeframe: str, lookback: int) -> CounterTrendVolume:
    """Classify the trend off the snapshot (the canonical anchor, ADR-0083) and decompose
    the trailing window against it — the synchronous core, run off-thread."""

    trend = condition_snapshot(bars, timeframe).trend
    return counter_trend_volume(bars, trend, lookback)


async def _volume_read_response(
    *,
    provider: MarketDataProvider,
    symbol: str,
    timeframe: str,
    kind: VolumeReadKind,
    confirmation: ConfirmationOpts | None = None,
    counter_trend: CounterTrendOpts | None = None,
    as_of: datetime | None,
) -> VolumeReadResponse:
    """Body of the `volume_read` tool: validate at the boundary, read the shared bar
    window once, then dispatch the selected mode's unchanged compute and wrap it in the
    discriminated envelope. The compute per mode is exactly the retired tool's, so
    `result` is byte-identical on the same inputs."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)

    end = as_of if as_of is not None else datetime.now(tz=UTC)
    start = end - (max_history(timeframe) or _DEFAULT_WINDOW)
    bars = await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, start, end, as_of)
    now = datetime.now(tz=UTC)
    if not bars:
        return VolumeReadResponse(kind=kind, result=None, partial_reason="no_bars", scanned_at=now)

    bar_list = list(bars)
    result: VolumeReadResult
    if kind == "confirmation":
        lookback = (confirmation if confirmation is not None else ConfirmationOpts()).lookback
        result = volume_confirmation(bar_list, lookback)
    else:  # counter_trend
        lookback = (counter_trend if counter_trend is not None else CounterTrendOpts()).lookback
        result = await asyncio.to_thread(_counter_trend_volume, bar_list, timeframe, lookback)

    return VolumeReadResponse(kind=kind, result=result, partial_reason=None, scanned_at=now)


def register_volume_read(server: FastMCP, *, provider: MarketDataProvider) -> None:
    """Bind the `volume_read` tool to `server`. The provider is captured by closure so the
    tool body keeps the parameters FastMCP introspects."""

    @server.tool(name="volume_read", description=VOLUME_READ_DESCRIPTION)
    async def volume_read_tool(
        symbol: str,
        timeframe: str,
        kind: VolumeReadKind,
        confirmation: ConfirmationOpts | None = None,
        counter_trend: CounterTrendOpts | None = None,
        as_of: datetime | None = None,
    ) -> VolumeReadResponse:
        return await _volume_read_response(
            provider=provider,
            symbol=symbol,
            timeframe=timeframe,
            kind=kind,
            confirmation=confirmation,
            counter_trend=counter_trend,
            as_of=as_of,
        )


__all__ = [
    "VOLUME_READ_DESCRIPTION",
    "ConfirmationOpts",
    "CounterTrendOpts",
    "VolumeReadKind",
    "VolumeReadResponse",
    "VolumeReadResult",
    "_volume_read_response",
    "register_volume_read",
]
