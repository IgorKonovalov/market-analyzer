"""`technical_read` MCP tool — Plan 0074 phase 2 (ADR-0068).

The *lesser* advisory tier's agent-facing surface: a directional call from **one**
curated regime indicator by its textbook mechanical rule, with no conviction and no
entry/stop/target levels. Distinct type (`TechnicalRead`) and distinct SSE event from
the fused `recommend`, so a thin single-indicator read can never be mistaken for the
corroborated call (ADR-0029 is untouched — this *extends* it with a lesser tier).

The flow mirrors `recommend`'s closed-bar discipline:

    validate inputs (symbol / timeframe / indicator_id)
        -> now = datetime.now(UTC)        (the only wall-clock read)
        -> fetch bars [range_start, now]
        -> closed bars only               (same rule as the live evaluator)
        -> technical_read(...) core        (the single-indicator regime->direction rule)
        -> bus.publish("technical_read.completed v1", {read})

The read is computed from the closed-bar series, so its `as_of_bar_ts` is the last bar
it saw — no future leak (anti-lookahead, ADR-0023). **Advisory only, structurally**
(ADR-0068): the tool holds no secret store, opens no network-write path, places no
order — a source scan pins it, the same boundary `recommend` and the forecast tools
carry. The `technical_read.completed v1` envelope publishes exactly once on success and
not at all on failure — any raise above the publish leaves the bus untouched.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

from market_analyser.advisor.models import TechnicalRead
from market_analyser.advisor.technical_read import eligible_indicators
from market_analyser.advisor.technical_read import technical_read as _technical_read_core
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label, timeframe_spec
from market_analyser.events import EventBus, TechnicalReadCompletedPayloadV1

TECHNICAL_READ_DESCRIPTION = (
    "ADVISORY ONLY, LESSER TIER — a single-indicator technical read: the mechanical "
    "direction (long/short/flat) of ONE curated regime indicator by its textbook rule, "
    "with NO conviction and NO entry/stop/target levels. This is NOT the fully-"
    "corroborated `recommend` call — it is one named indicator, said out loud, and "
    "nothing more; there is no ML forecast, no walk-forward edge, no cross-leg "
    "agreement behind it. It may say long while `recommend` says flat — that is thin "
    "vs. corroborated, not a contradiction. The user reads it and sizes it themselves. "
    f"Eligible indicators: {{indicators}}. supertrend -> its direction; ema_stack -> "
    "fast-vs-slow EMA and close; macd -> histogram sign; ichimoku -> price vs the "
    "displaced cloud with tenkan/kijun. Returns a TechnicalRead (direction, the "
    "indicator's regime_state read, and the mechanical rule as rationale). Reads the "
    "last CLOSED bar; requires bars already cached for the window (backfill via "
    "get_ohlcv first). Publishes `technical_read.completed v1` so a connected viewer "
    "renders the read live. This tool holds no trade key, places no order, moves no "
    f"money. Supported timeframes: {supported_timeframes_label()}."
).replace("{indicators}", ", ".join(eligible_indicators()))


async def _technical_read_response(
    *,
    provider: MarketDataProvider,
    event_bus: EventBus,
    symbol: str,
    timeframe: str,
    range_start: datetime,
    indicator_id: str,
    now: datetime | None = None,
) -> TechnicalRead:
    """Body of the `technical_read` tool. `now` is injectable so tests run on a fixed
    instant without a live MCP server (the `recommend` precedent). Publishes the
    `technical_read.completed v1` envelope exactly once, only after a successful read."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    # Validate the indicator at the boundary, before any fetch — an unknown id fails
    # fast with the known set listed (the core re-checks, defense in depth).
    if indicator_id not in eligible_indicators():
        raise ValueError(
            f"unknown indicator_id {indicator_id!r}; "
            f"known technical-read indicators: {list(eligible_indicators())}"
        )

    resolved_now = now if now is not None else datetime.now(UTC)
    bars = list(
        await asyncio.to_thread(provider.get_ohlcv, symbol, timeframe, range_start, resolved_now)
    )
    if not bars:
        raise ValueError(
            f"no cached bars for {symbol} {timeframe} over the requested window; "
            "backfill via get_ohlcv first",
        )

    # Same closedness rule as the live evaluator and `recommend`: a bar is closed once a
    # full duration has elapsed since it opened. The read's as-of bar is the last one.
    duration = timeframe_spec(timeframe).bar_duration
    closed_bars = [bar for bar in bars if bar.event_ts + duration <= resolved_now]
    if not closed_bars:
        raise ValueError(
            f"no closed bars: all {len(bars)} bar(s) are still forming relative "
            f"to now={resolved_now!r}"
        )

    read = _technical_read_core(
        symbol=symbol,
        timeframe=timeframe,
        bars=closed_bars,
        indicator_id=indicator_id,
    )

    # Publish AFTER a successful read — every raise above this line leaves the bus
    # untouched (zero envelopes on failure).
    event_bus.publish("technical_read.completed", TechnicalReadCompletedPayloadV1(read=read))
    return read


def register_technical_read(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    event_bus: EventBus,
) -> None:
    """Bind the `technical_read` tool to `server`. The provider and event bus are
    captured by closure so the tool body keeps the parameter list FastMCP introspects
    for its (strict) input schema."""

    @server.tool(description=TECHNICAL_READ_DESCRIPTION)
    async def technical_read(
        symbol: str,
        timeframe: str,
        range_start: datetime,
        indicator_id: str,
    ) -> TechnicalRead:
        return await _technical_read_response(
            provider=provider,
            event_bus=event_bus,
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            indicator_id=indicator_id,
        )


__all__ = [
    "TECHNICAL_READ_DESCRIPTION",
    "_technical_read_response",
    "register_technical_read",
]
