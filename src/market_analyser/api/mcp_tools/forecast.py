"""`forecast` MCP tool — Plan 0036 phase 4, multi-horizon per Plan 0059 (ADR-0030
/ ADR-0040 / ADR-0054).

The first **forward-looking** tool in the app. It returns, per requested horizon,
a calibrated up/down/flat direction probability or an honest **no-edge** verdict
— never a price level, never a recommendation (that is the advisor, ADR-0029).
The flow:

    validate inputs (symbol / timeframe / range / horizons)
        -> fetch cached bars via the provider (ADR-0007)
        -> build ONE feature matrix for the call via the ADR-0057 tier
           ladder (Plan 0062): richest-first v2-full -> v2-deep -> v1, each
           exogenous tier eligible only past max(2*n_splits, MIN_TIER_ROWS)
           surviving rows; every skipped tier is stated with its row count
           in fallback_reason, series_inputs names exactly the selected
           tier's series — explicit, never silent. No store wired -> v1
           with the unwired reason.
        -> per horizon, INDEPENDENTLY (ADR-0054 rule 2):
             horizon-purged walk-forward + baseline gate
             -> train the final model (deterministic, seeded)
             -> model_version = hash of all prediction-affecting inputs,
                incl. the labelling rule (ADR-0040)
             -> beats_baseline: ship probabilities + persist the accepted model
                else:           ship prob_*=None, keep the validation basis
        -> return MultiHorizonForecastResult (blocks + series provenance)
        -> publish `forecast.completed v1` carrying the result inline
           (Plan 0037) — exactly once per successful run, strictly after the
           result is built; any raise above the publish leaves the bus
           untouched (the `signal.evaluated`/`recommendation.completed`
           discipline)

**Honest uncertainty** (ADR-0030 invariant 4): every block carries its own
out-of-sample `skill`, `baseline_skill`, and `beats_baseline` — "edge at 1d, no
edge at 1mo" is an expressible verdict. **Determinism** (ADR-0040): no
wall-clock field anywhere, so re-running on the same cached bars + metric points
+ seed returns a byte-identical result.

The CPU-bound model work is offloaded with `asyncio.to_thread`; the body is
factored into `_multi_forecast_response` / `_compute_multi_horizon_forecast` so
it is unit-testable without a live MCP server. `_compute_forecast` remains the
single-horizon v1 core the `recommend` tool consumes (its surface is
deliberately untouched by Plan 0059). Persistence targets a gitignored
`models/` root (sibling to `runs/`); when no such root is wired the forecast
still computes and returns, it is simply not cached to disk.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Bar
from market_analyser.events import EventBus, ForecastCompletedPayloadV1
from market_analyser.forecast.exogenous import MetricAsOfLookup
from market_analyser.forecast.features import (
    FEATURE_SET_ID,
    FeatureRow,
    build_feature_rows,
)
from market_analyser.forecast.labels import Direction, LabelParams, build_labels
from market_analyser.forecast.model import (
    DEFAULT_SEED,
    ModelParams,
    align_samples,
    model_lib_versions,
    predict_proba,
    train,
)
from market_analyser.forecast.registry import (
    compute_model_version,
    model_exists,
    save_model,
)

# Re-exported for existing importers: the result models are domain shapes and
# live in `forecast/result.py`; this tool module is just their wire surface.
from market_analyser.forecast.result import (
    EdgeStrength,
    ForecastProvenance,
    ForecastResult,
    HorizonForecast,
    MultiHorizonForecastResult,
    SeriesInput,
)
from market_analyser.forecast.tiers import select_feature_tier
from market_analyser.forecast.validation import ForecastValidation, validate

# The horizon set for daily bars (ADR-0054: next-day / ~1w / ~1mo). Every other
# timeframe keeps next-bar only for now (plan phase 3).
DAILY_HORIZONS: tuple[int, ...] = (1, 5, 21)

# How far the out-of-sample model skill must exceed the baseline skill for the
# edge to read as "clear" rather than "marginal". 0.02 = two percentage points of
# directional accuracy: the 2026-06-08 incident (skill 0.490 vs baseline 0.488, a
# 0.002 margin) sits firmly in "marginal", while a comfortable beat clears it. A
# judgment-call default (ADR-0030 invariant 4 refinement) — a single named knob,
# not a no-edge gate (that stays `beats_baseline` / `prob_*=None`).
EDGE_MARGIN_THRESHOLD = 0.02

# The stated reason a v1 feature set was used when no metric store exists at
# all (Plan 0061 phase 2): every v1-on-fallback result says why, the unwired
# case included.
FALLBACK_REASON_UNWIRED = "metric store not wired"


def _classify_edge(validation: ForecastValidation) -> tuple[float | None, EdgeStrength]:
    """Return `(edge_margin, edge_strength)` from a validation verdict.

    `edge_margin` is `skill - baseline_skill` when both were scored, else `None`
    (nothing to compare). `edge_strength` is `"no_edge"` whenever the baseline gate
    did not pass (`beats_baseline` False — the unchanged no-edge path); on a real
    beat it is `"clear"` when the margin reaches EDGE_MARGIN_THRESHOLD and
    `"marginal"` otherwise. When `beats_baseline` is True the gate guarantees both
    skills are present, so `edge_margin` is a positive float there."""
    if validation.skill is not None and validation.baseline_skill is not None:
        edge_margin: float | None = validation.skill - validation.baseline_skill
    else:
        edge_margin = None

    if not validation.beats_baseline:
        return edge_margin, "no_edge"
    # beats_baseline True means skill > baseline_skill (both non-None), so margin > 0.
    assert edge_margin is not None
    return edge_margin, ("clear" if edge_margin >= EDGE_MARGIN_THRESHOLD else "marginal")


FORECAST_DESCRIPTION = (
    "Forecast the price DIRECTION of a cached symbol over one or more horizons, "
    "each as a calibrated up/down/flat probability or an honest 'no edge over "
    "baseline' verdict. Horizons default to 1/5/21 bars on 1d (next-day / ~1w / "
    "~1mo) and to next-bar only on other timeframes; pass horizons=[...] to "
    "override. Each horizon trains and walk-forward-validates its OWN model and "
    "passes or fails the naive-baseline gate (persistence + majority-class) "
    "INDEPENDENTLY — 'edge at 1d, no edge at 1mo' is a normal result; a failed "
    "horizon ships prob_*=null with its validation basis. Features: the target "
    "symbol's own OHLCV indicators plus BTC cycle features (halving clock, Mayer "
    "Multiple, 200W-MA distance) and exogenous series (Fear & Greed, BTC "
    "dominance, funding rate, open interest, MVRV) joined lag-1 as-of at bar "
    "open, so publication-lag lookahead is structurally impossible. Feature "
    "sets form a fixed ladder selected richest-first per call by exogenous "
    "history depth: v2-full (all five series) -> v2-deep (F&G/funding/MVRV "
    "only, the deep-history tier) -> v1 (OHLCV only); provenance lists exactly "
    "the selected tier's series under series_inputs (empty for v1) and "
    "provenance.fallback_reason names every richer tier skipped with its "
    "surviving-row count (absent when v2-full trained; check feature_set_id "
    "for the tier used). Each block "
    "carries out-of-sample skill, baseline skill, "
    "edge_margin = skill - baseline_skill, and edge_strength ('no_edge' / "
    "'marginal' / 'clear'); treat a high prob_* under a 'marginal' edge as thin, "
    "not near-certain. This is a CONDITION (a probability), never a buy/sell "
    "recommendation and never a price level. Requires bars already cached for "
    "the window (backfill via get_ohlcv first). Supported timeframes: 1d, 1h, "
    "15m, 4h, 1w."
)


def _compute_forecast(
    *,
    bars: list[Bar],
    symbol: str,
    timeframe: str,
    horizon_bars: int,
    flat_band: float,
    n_splits: int,
    seed: int,
    models_dir: Path | None,
) -> ForecastResult:
    """The deterministic, CPU-bound core: validate, train the final model, predict
    the latest bar, gate on the baseline, and (when accepted) persist."""

    model_params = ModelParams(seed=seed)
    validation = validate(
        bars,
        horizon_bars=horizon_bars,
        flat_band=flat_band,
        n_splits=n_splits,
        model_params=model_params,
    )

    rows = build_feature_rows(bars)
    defined_rows = [row for row in rows if row is not None]
    if not defined_rows:
        raise ValueError("not enough bars to build any feature row; fetch more history")
    predict_row = defined_rows[-1]  # the latest bar we have features for — the as-of bar

    labels = build_labels(bars, LabelParams(horizon_bars=horizon_bars, flat_band=flat_band))
    train_rows, train_labels = align_samples(rows, labels)
    if not train_rows or len({lab for lab in train_labels}) < 2:
        raise ValueError("insufficient labelled history/variation to train a forecast model")

    model = train(train_rows, train_labels, model_params)
    lib_versions = model_lib_versions()
    cutoff = model.training_cutoff
    assert isinstance(cutoff, datetime)  # set from a bar event_ts in model.train
    model_version = compute_model_version(
        feature_set_id=FEATURE_SET_ID,
        model_params=model.params,
        label_params=LabelParams(horizon_bars=horizon_bars, flat_band=flat_band),
        training_cutoff=cutoff,
        lib_versions=lib_versions,
    )
    provenance = ForecastProvenance(
        model_version=model_version,
        feature_set_id=FEATURE_SET_ID,
        training_cutoff=cutoff,
        seed=model.params.seed,
        lib_versions=lib_versions,
    )

    dist = predict_proba(model, [predict_row])[0]
    if validation.beats_baseline:
        prob_up: float | None = dist[Direction.UP]
        prob_down: float | None = dist[Direction.DOWN]
        prob_flat: float | None = dist[Direction.FLAT]
        if models_dir is not None and not model_exists(model_version, root=models_dir):
            save_model(
                model,
                model_version=model_version,
                lib_versions=lib_versions,
                root=models_dir,
                label_params=LabelParams(horizon_bars=horizon_bars, flat_band=flat_band),
            )
    else:
        prob_up = prob_down = prob_flat = None

    edge_margin, edge_strength = _classify_edge(validation)

    return ForecastResult(
        symbol=symbol,
        timeframe=timeframe,
        as_of_bar_ts=predict_row.event_ts,
        horizon_bars=horizon_bars,
        prob_up=prob_up,
        prob_down=prob_down,
        prob_flat=prob_flat,
        validation=validation,
        provenance=provenance,
        edge_margin=edge_margin,
        edge_strength=edge_strength,
    )


def default_horizons(timeframe: str) -> tuple[int, ...]:
    """The horizon set a timeframe gets when the caller does not name one:
    `DAILY_HORIZONS` on daily bars, next-bar only everywhere else."""

    return DAILY_HORIZONS if timeframe == "1d" else (1,)


def _normalise_horizons(horizons: list[int] | None, timeframe: str) -> tuple[int, ...]:
    """Resolve the requested horizon list: default per timeframe, each >= 1,
    deduplicated, ascending (a deterministic block order on the wire)."""

    if horizons is None:
        return default_horizons(timeframe)
    if not horizons:
        raise ValueError("horizons must not be empty; omit it for the default set")
    for horizon in horizons:
        if horizon < 1:
            raise ValueError(f"every horizon must be >= 1, got {horizon}")
    return tuple(sorted(set(horizons)))


def _compute_multi_horizon_forecast(
    *,
    bars: list[Bar],
    symbol: str,
    timeframe: str,
    horizons: tuple[int, ...],
    flat_band: float,
    n_splits: int,
    seed: int,
    models_dir: Path | None,
    metric_lookup: MetricAsOfLookup | None,
) -> MultiHorizonForecastResult:
    """The deterministic, CPU-bound Plan 0059 core: build one feature matrix,
    then validate / train / gate / (persist) each horizon independently.

    With a metric store wired the matrix comes from the ADR-0057 tier ladder
    (`select_feature_tier`): the richest of ``v2-full → v2-deep → v1`` whose
    post-join surviving rows clear ``max(2 * n_splits, MIN_TIER_ROWS)`` trains,
    and every skipped tier is stated with its surviving-row count in
    ``provenance.fallback_reason`` — never silent (the Plan 0061 honesty
    property, now per rung). Without a store the call computes on the v1
    OHLCV-only set with the unwired reason. ``series_inputs`` names exactly
    the selected tier's consumed series. A horizon with nothing to train on
    yields an honest block: ``prob_*`` null, the unscored validation basis
    attached, ``provenance`` None (no model exists to version).
    """

    model_params = ModelParams(seed=seed)
    series_inputs: tuple[SeriesInput, ...]
    fallback_reason: str | None
    rows: list[FeatureRow | None]
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

    defined_rows = [row for row in rows if row is not None]
    predict_row = defined_rows[-1] if defined_rows else None
    as_of_bar_ts = predict_row.event_ts if predict_row is not None else bars[-1].event_ts
    lib_versions = model_lib_versions()

    blocks: list[HorizonForecast] = []
    for horizon in horizons:
        label_params = LabelParams(horizon_bars=horizon, flat_band=flat_band)
        validation = validate(
            bars,
            horizon_bars=horizon,
            flat_band=flat_band,
            n_splits=n_splits,
            model_params=model_params,
            feature_rows=rows,
        )
        labels = build_labels(bars, label_params)
        train_rows, train_labels = align_samples(rows, labels)
        trainable = (
            predict_row is not None
            and bool(train_rows)
            and len({label for label in train_labels}) >= 2
        )
        edge_margin, edge_strength = _classify_edge(validation)

        if not trainable or predict_row is None:
            blocks.append(
                HorizonForecast(
                    horizon_bars=horizon,
                    prob_up=None,
                    prob_down=None,
                    prob_flat=None,
                    validation=validation,
                    edge_margin=edge_margin,
                    edge_strength=edge_strength,
                    provenance=None,
                )
            )
            continue

        model = train(train_rows, train_labels, model_params, feature_set_id=feature_set_id)
        cutoff = model.training_cutoff
        assert isinstance(cutoff, datetime)  # set from a bar event_ts in model.train
        model_version = compute_model_version(
            feature_set_id=feature_set_id,
            model_params=model.params,
            label_params=label_params,
            training_cutoff=cutoff,
            lib_versions=lib_versions,
        )
        provenance = ForecastProvenance(
            model_version=model_version,
            feature_set_id=feature_set_id,
            training_cutoff=cutoff,
            seed=model.params.seed,
            lib_versions=lib_versions,
            series_inputs=series_inputs,
            fallback_reason=fallback_reason,
        )

        dist = predict_proba(model, [predict_row])[0]
        if validation.beats_baseline:
            prob_up: float | None = dist[Direction.UP]
            prob_down: float | None = dist[Direction.DOWN]
            prob_flat: float | None = dist[Direction.FLAT]
            if models_dir is not None and not model_exists(model_version, root=models_dir):
                save_model(
                    model,
                    model_version=model_version,
                    lib_versions=lib_versions,
                    root=models_dir,
                    label_params=label_params,
                )
        else:
            prob_up = prob_down = prob_flat = None

        blocks.append(
            HorizonForecast(
                horizon_bars=horizon,
                prob_up=prob_up,
                prob_down=prob_down,
                prob_flat=prob_flat,
                validation=validation,
                edge_margin=edge_margin,
                edge_strength=edge_strength,
                provenance=provenance,
            )
        )

    return MultiHorizonForecastResult(
        symbol=symbol,
        timeframe=timeframe,
        as_of_bar_ts=as_of_bar_ts,
        feature_set_id=feature_set_id,
        horizons=blocks,
    )


async def _multi_forecast_response(
    *,
    provider: MarketDataProvider,
    event_bus: EventBus,
    models_dir: Path | None,
    metric_lookup: MetricAsOfLookup | None,
    symbol: str,
    timeframe: str,
    range_start: datetime,
    range_end: datetime,
    horizons: list[int] | None,
    flat_band: float,
    n_splits: int,
    seed: int,
) -> MultiHorizonForecastResult:
    """Body of `forecast`: validate, fetch, then offload the model work.
    Publishes the `forecast.completed v1` envelope exactly once, only after a
    successful computation (Plan 0037)."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    _require_ordered_range(range_start, range_end)
    resolved_horizons = _normalise_horizons(horizons, timeframe)
    if n_splits < 2:
        raise ValueError(f"n_splits must be >= 2, got {n_splits}")
    if flat_band < 0:
        raise ValueError(f"flat_band must be >= 0, got {flat_band}")

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
        _compute_multi_horizon_forecast,
        bars=bars,
        symbol=symbol,
        timeframe=timeframe,
        horizons=resolved_horizons,
        flat_band=flat_band,
        n_splits=n_splits,
        seed=seed,
        models_dir=models_dir,
        metric_lookup=metric_lookup,
    )

    # Publish AFTER a successful computation — every raise above this line
    # leaves the bus untouched (zero envelopes on failure). A no-edge horizon
    # still travels: its block carries null probabilities, the event fires.
    event_bus.publish("forecast.completed", ForecastCompletedPayloadV1(forecast=result))

    return result


def register_forecast(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    event_bus: EventBus,
    models_dir: Path | None,
    metric_lookup: MetricAsOfLookup | None = None,
) -> None:
    """Bind `forecast` to `server`. The provider, event bus, models_dir and
    metric store are captured by closure so the tool body keeps the parameter
    list FastMCP introspects for the schema. ``metric_lookup`` (the ADR-0051
    as_of surface) enables the v2 exogenous feature set; without it the tool
    computes on the v1 set and says so in its provenance."""

    @server.tool(description=FORECAST_DESCRIPTION)
    async def forecast(
        symbol: str,
        timeframe: str,
        range_start: datetime,
        range_end: datetime,
        horizons: list[int] | None = None,
        flat_band: float = 0.001,
        n_splits: int = 5,
        seed: int = DEFAULT_SEED,
    ) -> MultiHorizonForecastResult:
        return await _multi_forecast_response(
            provider=provider,
            event_bus=event_bus,
            models_dir=models_dir,
            metric_lookup=metric_lookup,
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            range_end=range_end,
            horizons=horizons,
            flat_band=flat_band,
            n_splits=n_splits,
            seed=seed,
        )


__all__ = [
    "DAILY_HORIZONS",
    "EDGE_MARGIN_THRESHOLD",
    "FALLBACK_REASON_UNWIRED",
    "FORECAST_DESCRIPTION",
    "EdgeStrength",
    "ForecastProvenance",
    "ForecastResult",
    "HorizonForecast",
    "MultiHorizonForecastResult",
    "SeriesInput",
    "_classify_edge",
    "_compute_forecast",
    "_compute_multi_horizon_forecast",
    "_multi_forecast_response",
    "default_horizons",
    "register_forecast",
]
