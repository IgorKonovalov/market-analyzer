"""`forecast_volatility` MCP tool — Plan 0077 phase 3 (ADR-0070).

A non-directional forecast: realised **volatility** over the next ``horizon_bars``,
scored against deterministic EWMA/persistence baselines by QLIKE out of sample. Unlike
the near-random direction target, volatility clusters, so this can clear its baseline —
and the output sizes positions and stops (the advisor wiring, Plan 0077 phase 5), never
a direction. A CONDITION (a magnitude), never a recommendation (ADR-0029) and never a
price level.

The flow mirrors `forecast`: validate inputs -> fetch cached bars -> build ONE feature
matrix via the ADR-0057 tier ladder (richest-first v2-full -> v2-deep -> v1; no metric
store -> v1 with the unwired reason) -> compute the forecast off-thread -> publish
`volatility_forecast.completed v1` exactly once, strictly after the result is built (any
raise above the publish leaves the bus untouched). Read-only: the tool holds no key,
places no order, writes nothing (the `recommend`/advisor source-scan discipline).
Deterministic (ADR-0040): no wall-clock field, so a re-run on the same bars is
byte-identical.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
    _require_supported_timeframe,
)
from market_analyser.api.mcp_tools.forecast import FALLBACK_REASON_UNWIRED
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Bar
from market_analyser.events import EventBus, VolatilityForecastCompletedPayloadV1
from market_analyser.forecast.exogenous import MetricAsOfLookup
from market_analyser.forecast.features import FEATURE_SET_ID, FeatureRow, build_feature_rows
from market_analyser.forecast.model import DEFAULT_SEED, ModelParams
from market_analyser.forecast.result import SeriesInput
from market_analyser.forecast.tiers import select_feature_tier
from market_analyser.forecast.volatility import VolatilityForecast
from market_analyser.forecast.volatility import forecast_volatility as _run_volatility_forecast

FORECAST_VOLATILITY_DESCRIPTION = (
    "Forecast realised VOLATILITY (not direction) of a cached symbol over the next "
    "horizon_bars: the predicted per-bar volatility with a 1-sigma out-of-sample band, "
    "scored against deterministic EWMA + persistence baselines by QLIKE. beats_baseline "
    "is the honest gate (the model must beat the better baseline out-of-sample); when it "
    "does not, trust baseline_vol (the winning baseline's current reading), which is "
    "always surfaced. Features use the same richest-first tier ladder as `forecast` "
    "(v2-full -> v2-deep -> v1 by exogenous history depth); provenance names the tier "
    "(feature_set_id), its series (series_inputs), any skipped tier (fallback_reason), "
    "and the top out-of-sample permutation-importance drivers. This is a CONDITION (a "
    "magnitude), never a buy/sell recommendation and never a price level; use it for "
    "position sizing and stop distance. Requires bars already cached for the window "
    "(backfill via get_ohlcv first). Supported timeframes: 1d, 1h, 15m, 4h, 1w."
)


async def _volatility_forecast_response(
    *,
    provider: MarketDataProvider,
    event_bus: EventBus,
    metric_lookup: MetricAsOfLookup | None,
    symbol: str,
    timeframe: str,
    range_start: datetime,
    range_end: datetime,
    horizon_bars: int,
    n_splits: int,
    seed: int,
) -> VolatilityForecast:
    """Body of `forecast_volatility`: validate, fetch, tier-select, offload the model
    work, then publish the completed envelope exactly once after success."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    _require_ordered_range(range_start, range_end)
    if horizon_bars < 1:
        raise ValueError(f"horizon_bars must be >= 1, got {horizon_bars}")
    if n_splits < 2:
        raise ValueError(f"n_splits must be >= 2, got {n_splits}")

    bars = list(
        await asyncio.to_thread(
            provider.get_ohlcv,
            symbol=symbol,
            timeframe=timeframe,
            start=range_start,
            end=range_end,
        )
    )
    if not bars:
        raise ValueError(
            f"no cached bars for {symbol} {timeframe} over the requested window; "
            "backfill via get_ohlcv first",
        )

    result = await asyncio.to_thread(
        _compute_volatility_forecast,
        bars=bars,
        symbol=symbol,
        timeframe=timeframe,
        horizon_bars=horizon_bars,
        n_splits=n_splits,
        seed=seed,
        metric_lookup=metric_lookup,
    )

    # Publish AFTER a successful computation — every raise above leaves the bus untouched.
    event_bus.publish(
        "volatility_forecast.completed", VolatilityForecastCompletedPayloadV1(forecast=result)
    )
    return result


def _compute_volatility_forecast(
    *,
    bars: list[Bar],
    symbol: str,
    timeframe: str,
    horizon_bars: int,
    n_splits: int,
    seed: int,
    metric_lookup: MetricAsOfLookup | None,
) -> VolatilityForecast:
    """The deterministic, CPU-bound core: pick the feature tier (or v1 when no store is
    wired), then forecast. Factored out of the async body so it is unit-testable without
    a live MCP server (the `forecast` tool precedent)."""

    rows: list[FeatureRow | None]
    series_inputs: tuple[SeriesInput, ...]
    fallback_reason: str | None
    if metric_lookup is not None:
        selection = select_feature_tier(bars, metric_lookup, n_splits=n_splits)
        rows = selection.rows
        feature_set_id = selection.feature_set_id
        series_inputs = selection.series_inputs
        fallback_reason = selection.fallback_reason
    else:
        rows = build_feature_rows(bars)
        feature_set_id = FEATURE_SET_ID
        series_inputs = ()
        fallback_reason = FALLBACK_REASON_UNWIRED

    return _run_volatility_forecast(
        bars,
        symbol=symbol,
        timeframe=timeframe,
        horizon_bars=horizon_bars,
        n_splits=n_splits,
        model_params=ModelParams(seed=seed),
        feature_rows=rows,
        feature_set_id=feature_set_id,
        series_inputs=series_inputs,
        fallback_reason=fallback_reason,
    )


def register_forecast_volatility(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    event_bus: EventBus,
    metric_lookup: MetricAsOfLookup | None = None,
) -> None:
    """Bind `forecast_volatility` to `server`. The provider, event bus and metric store
    are captured by closure. ``metric_lookup`` (the ADR-0051 as_of surface) enables the
    v2 exogenous tiers; without it the tool computes on the v1 set and says so."""

    @server.tool(description=FORECAST_VOLATILITY_DESCRIPTION)
    async def forecast_volatility(
        symbol: str,
        timeframe: str,
        range_start: datetime,
        range_end: datetime,
        horizon_bars: int = 5,
        n_splits: int = 5,
        seed: int = DEFAULT_SEED,
    ) -> VolatilityForecast:
        return await _volatility_forecast_response(
            provider=provider,
            event_bus=event_bus,
            metric_lookup=metric_lookup,
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            range_end=range_end,
            horizon_bars=horizon_bars,
            n_splits=n_splits,
            seed=seed,
        )


__all__ = [
    "FORECAST_VOLATILITY_DESCRIPTION",
    "_compute_volatility_forecast",
    "_volatility_forecast_response",
    "register_forecast_volatility",
]
