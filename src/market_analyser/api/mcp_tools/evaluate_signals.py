"""`evaluate_signals` MCP tool — Plan 0026 phase 2.

The agent-facing primitive for a *live* signal read: what does strategy X say on
the **current** bar of symbol Y, right now? It resolves the strategy, validates
the timeframe + params at the boundary, fetches fresh bars up to *now*
(fetch-on-miss, exactly as `get_ohlcv` does), runs the pure phase-1 evaluation
core, returns the `SignalEvaluation`, and publishes one `signal.evaluated v1`
envelope so a connected viewer can render it live.

    validate inputs (timeframe in META.timeframes, params vs the strategy Params)
        -> resolve strategy_module = discover()[strategy_id]
        -> now = datetime.now(UTC)
        -> fetch bars [range_start, now]  (coordinator fetch-on-miss, else provider)
        -> evaluate_signals_core(strategy_module, bars, params, now=now)
        -> bus.publish("signal.evaluated v1", {evaluation})
        -> return the SignalEvaluation

This is a **condition report, never a recommendation** (ADR-0029): it states
what the strategy's signals *are*, never buy/sell. Nothing is
persisted — a live read is ephemeral, so the SSE payload carries the full
(small) `SignalEvaluation` inline and there is no GET route. There is **no
`range_end` and no `as_of`**: the read always runs to the latest available bar (a
now-read). The tool signature omits both, so passing either fails the strict
input schema at the MCP boundary.

The `signal.evaluated v1` envelope is published exactly once on success and not
at all on failure (any raise above the publish leaves the bus untouched — the
same discipline as `run.completed`). The body is factored out as
`_evaluate_signals_response` so the fetch / validation / publish paths are
unit-testable on a single event loop with an injectable `now` (no live MCP
server needed), while the core itself stays clock-free.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_supported_timeframe,
)
from market_analyser.backtest import evaluate_signals as evaluate_signals_core
from market_analyser.backtest.types import SignalEvaluation
from market_analyser.contracts.strategy import BaseParams, discover
from market_analyser.data.backfill import BackfillCoordinator
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.timeframes import supported_timeframes_label
from market_analyser.events import EventBus, SignalEvaluatedPayloadV1

EVALUATE_SIGNALS_DESCRIPTION = (
    "Evaluate a strategy against the CURRENT bar of one symbol — a live signal "
    "read, not a historical backtest. Reports the current implied position "
    "(flat/long), the most recent signal (kind + bar + timestamp + reason), "
    "bars-since-last-signal, and a `fresh_signal` flag that is true when a "
    "signal fired on the last closed bar. A still-forming latest bar is excluded "
    "(surfaced via `latest_bar_excluded_as_forming` / `evaluated_through_ts`). "
    "`range_start` is the warm-up lookback — request enough history for the "
    "strategy's indicators to warm up; there is no range_end (the read always "
    "runs to the latest available bar) and no as_of. This is a CONDITION REPORT, "
    "never a buy/sell recommendation. Publishes a `signal.evaluated v1` event so "
    "the viewer's live-signal panel updates. Nothing is persisted. Supported "
    f"timeframes: {supported_timeframes_label()}."
)


async def _evaluate_signals_response(
    *,
    provider: MarketDataProvider,
    coordinator: BackfillCoordinator | None,
    event_bus: EventBus,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    range_start: datetime,
    params: dict[str, Any],
    now: datetime | None = None,
) -> SignalEvaluation:
    """Body of the `evaluate_signals` tool. `now` is injectable so tests run on a
    fixed instant; production passes `None` and reads `datetime.now(UTC)` here
    (the only wall-clock read — the core stays pure). Validates and fetches
    before evaluating; publishes the `signal.evaluated v1` envelope exactly once,
    only after a successful evaluation."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)

    strategies = discover()
    if strategy_id not in strategies:
        raise ValueError(
            f"unknown strategy_id {strategy_id!r}; known: {sorted(strategies)}",
        )
    strategy_module = strategies[strategy_id]

    supported = strategy_module.META.timeframes
    if timeframe not in supported:
        raise ValueError(
            f"timeframe {timeframe!r} not supported by strategy {strategy_id!r} "
            f"(supported: {list(supported)})",
        )

    # Validate params at the boundary against the strategy's own Params model
    # (extra="forbid" rejects unknown keys; field constraints reject bad values).
    # Raises pydantic.ValidationError on violation — surfaced as a tool error.
    params_instance: BaseParams = strategy_module.Params(**params)

    resolved_now = now if now is not None else datetime.now(UTC)

    # Fetch to `resolved_now` (a now-read, no range_end). With a coverage-capable
    # provider, fetch-on-miss surfaces partial failures the same way get_ohlcv
    # does; otherwise fall back to the plain fetch. Offloaded so the blocking
    # fetch never stalls the event loop.
    if coordinator is not None:
        result = await asyncio.to_thread(
            coordinator.get_ohlcv_with_status, symbol, timeframe, range_start, resolved_now
        )
        bars = list(result.bars)
    else:
        bars = list(
            await asyncio.to_thread(
                provider.get_ohlcv, symbol, timeframe, range_start, resolved_now
            )
        )

    evaluation = evaluate_signals_core(strategy_module, bars, params_instance, now=resolved_now)

    # Publish AFTER a successful evaluation — every raise above this line leaves
    # the bus untouched (zero envelopes on failure).
    event_bus.publish("signal.evaluated", SignalEvaluatedPayloadV1(evaluation=evaluation))

    return evaluation


def register_evaluate_signals(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    backfill_coordinator: BackfillCoordinator | None,
    event_bus: EventBus,
) -> None:
    """Bind the `evaluate_signals` tool to `server`. Dependencies are captured by
    closure so the tool body keeps the declared parameter list FastMCP
    introspects to build the (strict) input schema — which is exactly why an
    `as_of`/`range_end` key is rejected: the signature does not declare it."""

    @server.tool(description=EVALUATE_SIGNALS_DESCRIPTION)
    async def evaluate_signals(
        strategy_id: str,
        symbol: str,
        timeframe: str,
        range_start: datetime,
        params: dict[str, Any],
    ) -> SignalEvaluation:
        return await _evaluate_signals_response(
            provider=provider,
            coordinator=backfill_coordinator,
            event_bus=event_bus,
            strategy_id=strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            params=params,
        )


__all__ = [
    "EVALUATE_SIGNALS_DESCRIPTION",
    "_evaluate_signals_response",
    "register_evaluate_signals",
]
