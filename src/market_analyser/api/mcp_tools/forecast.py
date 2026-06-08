"""`forecast` MCP tool — Plan 0036 phase 4 (ADR-0030 / ADR-0040).

The first **forward-looking** tool in the app. It returns a calibrated up/down/flat
direction probability for the next ``horizon_bars``, or an honest **no-edge**
verdict — never a price level, never a recommendation (that is the advisor,
ADR-0029). The flow:

    validate inputs (symbol / timeframe / range / horizon)
        -> fetch cached bars via the provider (ADR-0007)
        -> validate(): expanding-window walk-forward + baseline gate (phase 3)
        -> train the final model on all labelled bars (deterministic, seeded)
        -> model_version = hash of all prediction-affecting inputs (ADR-0040)
        -> if beats_baseline: ship probabilities + persist the accepted model
           else:             ship prob_*=None (no edge), keep the validation basis
        -> return ForecastResult (prob + validation basis + full provenance)

**Honest uncertainty** (ADR-0030 invariant 4): the `validation` block — the
out-of-sample `skill`, the `baseline_skill`, and `beats_baseline` — travels with
every result, so a marginal beat reads as marginal and a no-edge reads as no-edge.
**Determinism** (ADR-0040): the result carries no wall-clock field, so re-running
on the same cached bars + seed returns a byte-identical `ForecastResult` —
`prob_*`, `skill`, and `model_version` all stable.

The CPU-bound model work is offloaded with `asyncio.to_thread`; the body is
factored into `_forecast_response` / `_compute_forecast` so it is unit-testable
without a live MCP server. Persistence targets a gitignored `models/` root
(sibling to `runs/`); when no such root is wired the forecast still computes and
returns, it is simply not cached to disk.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.api.mcp_tools._validation import (
    _require_non_empty_symbol,
    _require_ordered_range,
    _require_supported_timeframe,
)
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Bar
from market_analyser.forecast.features import FEATURE_SET_ID, build_feature_rows
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
from market_analyser.forecast.validation import ForecastValidation, validate

# How far the out-of-sample model skill must exceed the baseline skill for the
# edge to read as "clear" rather than "marginal". 0.02 = two percentage points of
# directional accuracy: the 2026-06-08 incident (skill 0.490 vs baseline 0.488, a
# 0.002 margin) sits firmly in "marginal", while a comfortable beat clears it. A
# judgment-call default (ADR-0030 invariant 4 refinement) — a single named knob,
# not a no-edge gate (that stays `beats_baseline` / `prob_*=None`).
EDGE_MARGIN_THRESHOLD = 0.02

# The edge-strength label travelling with every forecast. "no_edge" means the
# model did not beat baseline out-of-sample (prob_* are null); "marginal" / "clear"
# split a real beat by EDGE_MARGIN_THRESHOLD so a thin beat reads as thin.
EdgeStrength = Literal["no_edge", "marginal", "clear"]


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
    "Forecast the next-N-bar price DIRECTION for a cached symbol as a calibrated "
    "up/down/flat probability, or an honest 'no edge over baseline' verdict. A "
    "causal, leakage-free model (trained only on bars[0..=i]) is validated by "
    "rolling out-of-sample walk-forward and must beat a naive baseline "
    "(persistence + majority-class) to ship a probability; otherwise prob_up/down/"
    "flat are null and only the validation basis is returned. Every result carries "
    "its out-of-sample skill, the baseline skill, and full model provenance "
    "(model_version, feature-set id, training cutoff, seed, library versions). "
    "edge_strength labels how decisively the model beat baseline — 'no_edge' (no "
    "probability shipped), 'marginal' (beat baseline but by less than the margin "
    "threshold), or 'clear' — with edge_margin = skill - baseline_skill; treat a "
    "high prob_* under a 'marginal' edge as thin, not near-certain. This is a "
    "CONDITION (a probability), never a buy/sell recommendation and never a price "
    "level. Requires bars already cached for the window (backfill via get_ohlcv "
    "first). Supported timeframes: 1d, 1h, 15m, 4h, 1w."
)


class ForecastProvenance(BaseModel):
    """The audit trail that makes a forecast reproducible and traceable to its
    exact model (ADR-0040 §4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_version: str
    feature_set_id: str
    training_cutoff: datetime
    seed: int
    lib_versions: dict[str, str]


class ForecastResult(BaseModel):
    """A direction forecast. ``prob_*`` are ``None`` when the model did not beat
    baseline out-of-sample (the honest no-edge verdict); the ``validation`` basis
    and ``provenance`` are always present.

    ``edge_margin`` (out-of-sample ``skill - baseline_skill``) and ``edge_strength``
    (``no_edge`` / ``marginal`` / ``clear``) make a thin beat read as thin: a high
    ``prob_*`` riding a barely-above-baseline edge is labelled ``marginal`` so it is
    not mistaken for near-certainty (ADR-0030 invariant 4 refinement)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    as_of_bar_ts: datetime
    horizon_bars: int
    prob_up: float | None
    prob_down: float | None
    prob_flat: float | None
    validation: ForecastValidation
    provenance: ForecastProvenance
    # Appended after provenance to keep the wire-stable field order (ADR-0040
    # determinism contract: model_dump order is part of the byte-identical result).
    edge_margin: float | None
    edge_strength: EdgeStrength


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
                model, model_version=model_version, lib_versions=lib_versions, root=models_dir
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


async def _forecast_response(
    *,
    provider: MarketDataProvider,
    models_dir: Path | None,
    symbol: str,
    timeframe: str,
    range_start: datetime,
    range_end: datetime,
    horizon_bars: int,
    flat_band: float,
    n_splits: int,
    seed: int,
) -> ForecastResult:
    """Body of `forecast`: validate, fetch, then offload the model work."""

    _require_non_empty_symbol(symbol)
    _require_supported_timeframe(timeframe)
    _require_ordered_range(range_start, range_end)
    if horizon_bars < 1:
        raise ValueError(f"horizon_bars must be >= 1, got {horizon_bars}")
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

    return await asyncio.to_thread(
        _compute_forecast,
        bars=bars,
        symbol=symbol,
        timeframe=timeframe,
        horizon_bars=horizon_bars,
        flat_band=flat_band,
        n_splits=n_splits,
        seed=seed,
        models_dir=models_dir,
    )


def register_forecast(
    server: FastMCP, *, provider: MarketDataProvider, models_dir: Path | None
) -> None:
    """Bind `forecast` to `server`. The provider + models_dir are captured by
    closure so the tool body keeps the parameter list FastMCP introspects for the
    schema."""

    @server.tool(description=FORECAST_DESCRIPTION)
    async def forecast(
        symbol: str,
        timeframe: str,
        range_start: datetime,
        range_end: datetime,
        horizon_bars: int = 1,
        flat_band: float = 0.001,
        n_splits: int = 5,
        seed: int = DEFAULT_SEED,
    ) -> ForecastResult:
        return await _forecast_response(
            provider=provider,
            models_dir=models_dir,
            symbol=symbol,
            timeframe=timeframe,
            range_start=range_start,
            range_end=range_end,
            horizon_bars=horizon_bars,
            flat_band=flat_band,
            n_splits=n_splits,
            seed=seed,
        )


__all__ = [
    "EDGE_MARGIN_THRESHOLD",
    "FORECAST_DESCRIPTION",
    "EdgeStrength",
    "ForecastProvenance",
    "ForecastResult",
    "_classify_edge",
    "_compute_forecast",
    "_forecast_response",
    "register_forecast",
]
