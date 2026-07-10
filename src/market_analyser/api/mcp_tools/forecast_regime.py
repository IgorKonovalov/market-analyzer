"""`forecast_regime` MCP tool — Plan 0077 phase 3 (ADR-0070).

A non-directional forecast: the current market **regime** (a trailing trend x volatility
classification) plus a probability distribution over the **next-period** regime, scored
against a persistence baseline (regime unchanged) by the Brier score out of sample.
Distinct from the crypto-macro nowcast (ADR-0027): per-symbol, technical, and predictive.
A CONDITION, never a recommendation (ADR-0029).

The flow mirrors `forecast` / `forecast_volatility`: validate -> fetch cached bars ->
build ONE feature matrix via the ADR-0057 tier ladder -> compute off-thread -> publish
`regime_forecast.completed v1` exactly once, strictly after the result is built.
Read-only (no key, no order, no write); deterministic (no wall-clock field).
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
from market_analyser.events import EventBus, RegimeForecastCompletedPayloadV1
from market_analyser.forecast.exogenous import MetricAsOfLookup
from market_analyser.forecast.features import FEATURE_SET_ID, FeatureRow, build_feature_rows
from market_analyser.forecast.model import DEFAULT_SEED, ModelParams
from market_analyser.forecast.regime import RegimeForecast
from market_analyser.forecast.regime import forecast_regime as _run_regime_forecast
from market_analyser.forecast.result import SeriesInput
from market_analyser.forecast.tiers import select_feature_tier

FORECAST_REGIME_DESCRIPTION = (
    "Forecast the market REGIME TRANSITION (not direction) of a cached symbol: the "
    "current regime (a trailing trend x volatility state, e.g. up_quiet / down_volatile) "
    "and a probability distribution over the next-period regime horizon_bars ahead, "
    "scored against a persistence baseline (regime unchanged) by the Brier score. "
    "beats_baseline is the honest gate (the classifier must beat persistence "
    "out-of-sample); regimes are sticky, so persistence is a strong baseline and beating "
    "it is a real signal. The trend axis is the same classifier the analyst snapshot "
    "uses; the volatility axis splits ATR% at its trailing median. Features use the "
    "richest-first tier ladder (v2-full -> v2-deep -> v1); provenance names the tier, its "
    "series, any skipped tier, and the top out-of-sample permutation-importance drivers. "
    "Distinct from bitcoin_market_pulse's whole-market regime: this is per-symbol and "
    "predictive. A CONDITION, never a buy/sell recommendation. Requires bars already "
    "cached for the window (backfill via get_ohlcv first). Supported timeframes: 1d, 1h, "
    "15m, 4h, 1w."
)


async def _regime_forecast_response(
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
) -> RegimeForecast:
    """Body of `forecast_regime`: validate, fetch, tier-select, offload the model work,
    then publish the completed envelope exactly once after success."""

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
        _compute_regime_forecast,
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
        "regime_forecast.completed", RegimeForecastCompletedPayloadV1(forecast=result)
    )
    return result


def _compute_regime_forecast(
    *,
    bars: list[Bar],
    symbol: str,
    timeframe: str,
    horizon_bars: int,
    n_splits: int,
    seed: int,
    metric_lookup: MetricAsOfLookup | None,
) -> RegimeForecast:
    """The deterministic, CPU-bound core: pick the feature tier (or v1 when no store is
    wired), then forecast. Factored out of the async body so it is unit-testable without
    a live MCP server."""

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

    return _run_regime_forecast(
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


def register_forecast_regime(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    event_bus: EventBus,
    metric_lookup: MetricAsOfLookup | None = None,
) -> None:
    """Bind `forecast_regime` to `server`. ``metric_lookup`` (the ADR-0051 as_of surface)
    enables the v2 exogenous tiers; without it the tool computes on the v1 set and says
    so."""

    @server.tool(description=FORECAST_REGIME_DESCRIPTION)
    async def forecast_regime(
        symbol: str,
        timeframe: str,
        range_start: datetime,
        range_end: datetime,
        horizon_bars: int = 5,
        n_splits: int = 5,
        seed: int = DEFAULT_SEED,
    ) -> RegimeForecast:
        return await _regime_forecast_response(
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
    "FORECAST_REGIME_DESCRIPTION",
    "_compute_regime_forecast",
    "_regime_forecast_response",
    "register_forecast_regime",
]
