"""`forecast` MCP tool — Plan 0036 phase 4, multi-horizon per Plan 0059 (ADR-0030
/ ADR-0040 / ADR-0054); the three forecast *kinds* unified behind one `kind`
discriminator by Plan 0109 phase 2 (ADR-0104).

The app's forward-looking tool, with `kind` ∈ {`direction` (default), `volatility`,
`regime`} selecting what is predicted. The tool returns a discriminated envelope
`ForecastResponse{kind, result}` — a single object (so FastMCP does not wrap it in a
generic `{result}`), with the selected kind's existing model riding **byte-identical**
under `result`. All three kinds share the same skeleton — validate, fetch cached bars,
build ONE feature matrix via the ADR-0057 tier ladder, offload the model work, publish a
per-kind `*.completed v1` envelope exactly once after success — and every kind reports a
CONDITION (a probability / a magnitude / a state distribution), never a price level and
never a recommendation (that is the advisor, ADR-0029). The `direction` computation, its
`result` model, and its `forecast.completed` event are unchanged from the
pre-consolidation tool (the default `kind` preserves today's call inputs); `volatility`
and `regime` keep their own `result` models and their `volatility_forecast.completed` /
`regime_forecast.completed` events, absorbed here from the retired `forecast_volatility`
/ `forecast_regime` modules.

The `direction` flow:

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
it is unit-testable without a live MCP server. `_compute_multi_horizon_forecast`
is the single tiered core BOTH the `forecast` tool and the advisor's
`recommend` leg run (Plan 0066 reversed Plan 0059's "single-horizon v1 core left
untouched" note — the advisor now walks the same ladder so the two tools cannot
disagree at a shared horizon). Persistence targets a gitignored `models/` root
(sibling to `runs/`); when no such root is wired the forecast still computes and
returns, it is simply not cached to disk.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.api.mcp_tools._artifacts import _fs_safe, _write_explanation_artifact
from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Bar
from market_analyser.events import (
    EventBus,
    ForecastCompletedPayloadV1,
    RegimeForecastCompletedPayloadV1,
    VolatilityForecastCompletedPayloadV1,
)
from market_analyser.forecast.exogenous import MetricAsOfLookup
from market_analyser.forecast.explain import (
    ForecastExplanation,
    build_forecast_explanation_artifact,
    explain_horizon,
    feature_names_for_set,
    summarize_explanation,
)
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
from market_analyser.forecast.regime import RegimeForecast
from market_analyser.forecast.regime import forecast_regime as _run_regime_forecast
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
from market_analyser.forecast.validation import ForecastValidation, ScoredFold, validate
from market_analyser.forecast.volatility import VolatilityForecast
from market_analyser.forecast.volatility import forecast_volatility as _run_volatility_forecast

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


def _explanation_artifact_rel_path(symbol: str, timeframe: str, started_at: datetime) -> str:
    """The `runs_dir`-relative path of one forecast call's explanation JSON
    (Plan 0063, ADR-0058). ``started_at`` (with the path itself) is one of the
    two documented run-provenance exceptions to byte-identical re-runs."""

    stamp = started_at.strftime("%Y%m%dT%H%M%S%fZ")
    return f"forecast/{stamp}-{_fs_safe(symbol)}-{_fs_safe(timeframe)}/explanation.json"


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


ForecastKind = Literal["direction", "volatility", "regime"]

FORECAST_DESCRIPTION = (
    "Forecast a cached symbol over a window; `kind` selects WHAT is predicted, all "
    "read-only conditions (never a buy/sell call, never a price level). Returns "
    "{kind, result}: the kind's payload rides under `result`. "
    "kind='direction' (default): the price DIRECTION over one or more horizons, each a "
    "calibrated up/down/flat probability or an honest 'no edge over baseline' verdict. "
    "Horizons default to 1/5/21 bars on 1d (next-day / ~1w / ~1mo) and next-bar "
    "elsewhere; pass horizons=[...] to override. Each horizon trains and walk-forward-"
    "validates its OWN model and passes/fails the naive-baseline gate INDEPENDENTLY "
    "('edge at 1d, no edge at 1mo' is normal); a failed horizon ships prob_*=null with "
    "its validation basis, and each block carries out-of-sample skill, baseline skill, "
    "edge_margin, and edge_strength ('no_edge'/'marginal'/'clear'). "
    "kind='volatility': realised VOLATILITY over the next horizon_bars — the predicted "
    "per-bar magnitude with a 1-sigma band, scored against EWMA + persistence baselines "
    "by QLIKE; when beats_baseline is false, trust baseline_vol (always surfaced). Use "
    "it for position sizing and stop distance. "
    "kind='regime': the market REGIME TRANSITION — the current trend x volatility state "
    "(e.g. up_quiet / down_volatile) and a probability distribution over the "
    "next-period regime horizon_bars ahead, scored against a persistence baseline "
    "(regime unchanged) by the Brier score; regimes are sticky, so beating persistence "
    "is a real signal. horizons/flat_band apply to 'direction' only; horizon_bars to "
    "'volatility'/'regime' only. Features (all kinds): the symbol's OHLCV indicators "
    "plus BTC cycle + exogenous series (Fear & Greed, BTC dominance, funding, open "
    "interest, MVRV) joined lag-1 as-of at bar open (no publication-lag lookahead), on "
    "the richest-first tier ladder v2-full -> v2-deep -> v1; provenance names the tier "
    "(feature_set_id), its series (series_inputs), any skipped tier (fallback_reason), "
    "and the top out-of-sample permutation-importance drivers. Requires bars already "
    "cached for the window (backfill via get_ohlcv first). Supported timeframes: 1d, "
    "1h, 15m, 4h, 1w."
)


class ForecastResponse(BaseModel):
    """`forecast` result — the discriminated envelope (Plan 0109 ph2, ADR-0104).

    A single object (so FastMCP does not wrap it in a generic ``{"result": …}``),
    discriminated by `kind`. `result` is the selected kind's existing model, byte-
    identical to what the retired single-kind tool returned: a `MultiHorizonForecastResult`
    for `direction`, a `VolatilityForecast` for `volatility`, a `RegimeForecast` for
    `regime`. The union is a plain field union (pydantic serializes each member by its
    runtime type — the `scan_watchlist` precedent)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ForecastKind
    result: MultiHorizonForecastResult | VolatilityForecast | RegimeForecast


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
    explanation_artifact_path: str | None = None,
    explanation_sink: list[ForecastExplanation] | None = None,
) -> MultiHorizonForecastResult:
    """The deterministic, CPU-bound Plan 0059 core: build one feature matrix,
    then validate / train / gate / (persist) each horizon independently.

    Plan 0063: every horizon is also **explained** — seeded out-of-sample
    permutation importances over the walk-forward's own scored folds (captured
    via ``scored_fold_sink``, never re-trained, never the final fit). Each
    trained block's provenance carries the compact `ExplanationSummary` (top
    drivers + ``explanation_artifact_path``, the caller-supplied
    ``runs_dir``-relative location — ``None`` when no ``runs_dir`` is wired,
    in which case the drivers still ride the wire). ``explanation_sink``
    collects the full per-horizon `ForecastExplanation`s, one per block in
    block order, for the caller's artifact writer.

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

    feature_names = feature_names_for_set(feature_set_id)
    blocks: list[HorizonForecast] = []
    for horizon in horizons:
        label_params = LabelParams(horizon_bars=horizon, flat_band=flat_band)
        scored_folds: list[ScoredFold] = []
        validation = validate(
            bars,
            horizon_bars=horizon,
            flat_band=flat_band,
            n_splits=n_splits,
            model_params=model_params,
            feature_rows=rows,
            scored_fold_sink=scored_folds,
        )
        explanation = explain_horizon(
            horizon_bars=horizon,
            feature_set_id=feature_set_id,
            feature_names=feature_names,
            scored_folds=scored_folds,
            predict_row=predict_row,
            seed=seed,
        )
        if explanation_sink is not None:
            explanation_sink.append(explanation)
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
            explanation=summarize_explanation(explanation, artifact=explanation_artifact_path),
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
    runs_dir: Path | None = None,
) -> MultiHorizonForecastResult:
    """Body of `forecast`: validate, fetch, then offload the model work.
    Publishes the `forecast.completed v1` envelope exactly once, only after a
    successful computation (Plan 0037).

    Plan 0063: with a ``runs_dir`` wired, the complete explanation JSON is
    persisted under ``runs_dir/forecast/…`` **before** the publish (a failed
    write leaves the bus untouched, like every other raise above it) and each
    block's provenance summary names the artifact's relative path. Without a
    ``runs_dir`` no artifact is written and the summary's ``artifact`` is
    ``None`` — the top drivers still ride the wire."""

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

    # Wall-clock is confined to run provenance (the artifact's stamp + path,
    # the documented ADR-0018-style exceptions); the computation itself stays
    # clock-free.
    started_at = datetime.now(UTC) if runs_dir is not None else None
    artifact_rel_path = (
        _explanation_artifact_rel_path(symbol, timeframe, started_at)
        if started_at is not None
        else None
    )

    explanations: list[ForecastExplanation] = []
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
        explanation_artifact_path=artifact_rel_path,
        explanation_sink=explanations,
    )

    if runs_dir is not None and started_at is not None and artifact_rel_path is not None:
        artifact = build_forecast_explanation_artifact(result, explanations, started_at=started_at)
        await asyncio.to_thread(
            _write_explanation_artifact,
            artifact.model_dump_json(indent=2),
            runs_dir,
            artifact_rel_path,
        )

    # Publish AFTER a successful computation — every raise above this line
    # leaves the bus untouched (zero envelopes on failure). A no-edge horizon
    # still travels: its block carries null probabilities, the event fires.
    event_bus.publish("forecast.completed", ForecastCompletedPayloadV1(forecast=result))

    return result


# --------------------------------------------------------------------------- #
# kind="volatility" (absorbed from the retired forecast_volatility module)      #
# --------------------------------------------------------------------------- #


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
    a live MCP server (the `forecast` direction-core precedent)."""

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
    """Body of `forecast(kind="volatility")`: validate, fetch, tier-select, offload the
    model work, then publish the completed envelope exactly once after success."""

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


# --------------------------------------------------------------------------- #
# kind="regime" (absorbed from the retired forecast_regime module)             #
# --------------------------------------------------------------------------- #


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
    """Body of `forecast(kind="regime")`: validate, fetch, tier-select, offload the model
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


def register_forecast(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    event_bus: EventBus,
    models_dir: Path | None,
    metric_lookup: MetricAsOfLookup | None = None,
    runs_dir: Path | None = None,
) -> None:
    """Bind the unified `forecast` tool to `server`. The provider, event bus,
    models_dir and metric store are captured by closure so the tool body keeps the
    parameter list FastMCP introspects for the schema. ``metric_lookup`` (the ADR-0051
    as_of surface) enables the v2 exogenous feature set for every kind; without it the
    tool computes on the v1 set and says so in its provenance. ``runs_dir`` (Plan
    0063, ADR-0058) enables the per-call explanation artifact for the ``direction``
    kind under ``runs_dir/forecast/…``; without it the explanation summary still rides
    the wire, only the full JSON is skipped.

    `kind` dispatches to the unchanged per-kind body (Plan 0109 ph2, ADR-0104), and the
    result is wrapped in the `ForecastResponse{kind, result}` envelope: ``direction``
    (default — preserves today's call inputs and `result` model) publishes
    ``forecast.completed``, ``volatility`` publishes ``volatility_forecast.completed``,
    ``regime`` publishes ``regime_forecast.completed``. ``horizons`` / ``flat_band`` are
    read by ``direction`` only; ``horizon_bars`` by ``volatility`` / ``regime`` only."""

    @server.tool(description=FORECAST_DESCRIPTION)
    async def forecast(
        symbol: str,
        timeframe: str,
        range_start: datetime,
        range_end: datetime,
        kind: ForecastKind = "direction",
        horizons: list[int] | None = None,
        flat_band: float = 0.001,
        horizon_bars: int = 5,
        n_splits: int = 5,
        seed: int = DEFAULT_SEED,
    ) -> ForecastResponse:
        result: MultiHorizonForecastResult | VolatilityForecast | RegimeForecast
        if kind == "volatility":
            result = await _volatility_forecast_response(
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
        elif kind == "regime":
            result = await _regime_forecast_response(
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
        else:
            result = await _multi_forecast_response(
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
                runs_dir=runs_dir,
            )
        return ForecastResponse(kind=kind, result=result)


__all__ = [
    "DAILY_HORIZONS",
    "EDGE_MARGIN_THRESHOLD",
    "FALLBACK_REASON_UNWIRED",
    "FORECAST_DESCRIPTION",
    "EdgeStrength",
    "ForecastKind",
    "ForecastProvenance",
    "ForecastResponse",
    "ForecastResult",
    "HorizonForecast",
    "MultiHorizonForecastResult",
    "RegimeForecast",
    "SeriesInput",
    "VolatilityForecast",
    "_classify_edge",
    "_compute_multi_horizon_forecast",
    "_compute_regime_forecast",
    "_multi_forecast_response",
    "_regime_forecast_response",
    "_volatility_forecast_response",
    "default_horizons",
    "register_forecast",
]
