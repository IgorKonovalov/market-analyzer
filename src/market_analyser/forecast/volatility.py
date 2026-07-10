"""Volatility forecast: a non-directional forecast kind (Plan 0077 phase 1, ADR-0070).

Where the direction forecaster (`forecast/model.py` + `validation.py`) predicts the
*sign* of the next move — a near-random target that beats no baseline — this module
predicts the **magnitude** of movement: realised volatility over the next ``horizon``
bars. Volatility clusters (it is autocorrelated in a way returns are not), so the
target is genuinely forecastable, and its output drives position sizing and stop
distance (the advisor wiring, Plan 0077 phase 5).

The kind reuses the harness at the *machinery* level, not the classification code:
``backtest.walk_forward.fold_bounds`` (the contiguous fold partition), the
purge-by-horizon anti-lookahead discipline, the frozen `features.FeatureRow` matrix,
and the seeded / single-thread determinism mechanism (ADR-0040). It does **not** reuse
`validation.validate` — that is `Direction`-locked (accuracy, argmax, majority-class).
A volatility forecast is a regression, scored against deterministic baselines by a
regression loss, so it gets its own validation entry point here.

Design decisions (Plan 0077 interview):

* **Label** — realised volatility at bar ``i`` is the root-mean-square of the next
  ``horizon`` log returns: ``sqrt(mean(r[i+1]**2 .. r[i+horizon]**2))`` where
  ``r[j] = ln(close[j] / close[j-1])``. A per-bar volatility (not annualised); for
  ``horizon == 1`` it degenerates to ``|r[i+1]|``, which is noisy but honest. Like the
  direction label it looks *forward* — that is the target — and is never a feature.
* **Baselines** (both causal — known at bar ``i``): **persistence** (the realised RMS
  vol of the *trailing* ``horizon`` returns ending at ``i``) and **EWMA** (RiskMetrics
  ``sigma2[i] = lam * sigma2[i-1] + (1 - lam) * r[i]**2``).
* **Model** — an sklearn ``HistGradientBoostingRegressor`` over the shared feature
  matrix, trained on **log** volatility (positive target, variance-stabilised) and
  exp-ed back. Seeded + single-thread, so a retrain from the same samples is
  byte-identical (the ADR-0040 mechanism, mirrored from `model.py`).
* **Gate** — **QLIKE** on variance (``v/vhat - ln(v/vhat) - 1``), the volatility-
  forecasting literature's standard loss, robust to the noisy realised-vol proxy.
  Lower is better, so ``beats_baseline = model_qlike < min(persistence, ewma)`` out of
  sample. Vols are floored at `VOL_FLOOR` before squaring so a zero realised return can
  never produce ``ln(0)``.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

import threadpoolctl
from pydantic import BaseModel, ConfigDict
from sklearn.ensemble import HistGradientBoostingRegressor

from market_analyser.backtest.walk_forward import fold_bounds
from market_analyser.data.types import Bar
from market_analyser.forecast.features import FEATURE_SET_ID, FeatureRow, build_feature_rows
from market_analyser.forecast.model import ModelParams, model_lib_versions
from market_analyser.forecast.result import ForecastProvenance

# Floor applied to any volatility before it is squared into a variance for QLIKE, so
# a genuinely-zero realised move (possible at horizon 1) cannot yield ``ln(0)``. Small
# enough to be negligible against real crypto/equity per-bar vols (~1e-2).
VOL_FLOOR = 1e-8

# RiskMetrics daily decay for the EWMA baseline. A conventional, documented constant —
# not tuned here; the model must beat it, not match it.
DEFAULT_EWMA_LAMBDA = 0.94

# The estimator class is a prediction-affecting input (a different model on the same
# features/seed predicts differently), so it is part of the model-version hash — and it
# differs from the direction path's classifier, so this kind hashes its own version.
MODEL_CLASS = "HistGradientBoostingRegressor"

BaselineKind = Literal["persistence", "ewma"]


def _compute_vol_model_version(
    *,
    feature_set_id: str,
    model_params: ModelParams,
    horizon_bars: int,
    ewma_lambda: float,
    training_cutoff: datetime,
    lib_versions: dict[str, str],
) -> str:
    """A deterministic 16-hex-char hash over the volatility model's prediction-affecting
    inputs (the ADR-0040 mechanism, mirrored from `registry.compute_model_version` for
    the regressor): feature-set id, estimator class + hyperparameters, the target's
    horizon and EWMA-baseline decay, the training cutoff, and library versions."""

    payload: dict[str, Any] = {
        "feature_set_id": feature_set_id,
        "model_class": MODEL_CLASS,
        "hyperparameters": model_params.model_dump(mode="json"),
        "horizon_bars": horizon_bars,
        "ewma_lambda": ewma_lambda,
        "training_cutoff": training_cutoff.isoformat(),
        "lib_versions": lib_versions,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


class VolatilityValidationError(ValueError):
    """Raised when the walk-forward configuration is invalid for the bar series
    (fewer than two folds, or more folds than bars) — mirrors
    `validation.ForecastValidationError` for the regression path."""


class VolatilityFoldScore(BaseModel):
    """Out-of-sample QLIKE for one scored fold. ``None`` marks an unscored fold (no
    trainable history yet, or no scorable test sample)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fold_index: int
    n_test: int
    model_qlike: float | None
    persistence_qlike: float | None
    ewma_qlike: float | None


class VolatilityValidation(BaseModel):
    """The regression verdict. ``model_qlike`` is pooled out-of-sample QLIKE;
    ``baseline_qlike`` is the *better* (lower) of the two naive baselines on the same
    test bars; ``beats_baseline`` is the gate (``model_qlike < baseline_qlike``).
    ``score_margin`` is ``baseline_qlike - model_qlike`` (positive ⇒ the model
    improves on the baseline) so a thin beat reads as thin. Both baselines are kept so
    a marginal beat stays visible (ADR-0030 invariant 4, carried to the regression
    kind by ADR-0070)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_bars: int
    n_splits: int
    n_scored: int
    model_qlike: float | None
    baseline_qlike: float | None
    baseline_kind: BaselineKind | None
    persistence_qlike: float | None
    ewma_qlike: float | None
    score_margin: float | None
    beats_baseline: bool
    folds: list[VolatilityFoldScore]


def _log_returns(closes: Sequence[float]) -> list[float | None]:
    """Per-bar log return ``ln(close[i] / close[i-1])``; ``None`` at bar 0 and wherever
    a non-positive close makes the log undefined."""

    out: list[float | None] = [None]
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        out.append(math.log(cur / prev) if prev > 0.0 and cur > 0.0 else None)
    return out


def _rms(values: Sequence[float]) -> float:
    """Root-mean-square — the per-bar realised-volatility estimator over a return
    window. ``sqrt(mean(x**2))``; empty window returns ``0.0`` (no movement observed)."""

    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))


def build_volatility_labels(bars: Sequence[Bar], horizon: int) -> list[float | None]:
    """Realised-volatility label aligned to ``bars``: entry ``i`` is the RMS of the
    ``horizon`` log returns spanning ``close[i] .. close[i + horizon]``.

    ``None`` for the trailing ``horizon`` bars (no future window) and wherever any close
    in the window is non-positive (log undefined). The forward look is confined to the
    label; no caller joins entry ``i`` onto a feature at index ``> i``."""

    if horizon < 1:
        raise ValueError(f"horizon must be >= 1, got {horizon}")
    closes = [b.close for b in bars]
    rets = _log_returns(closes)
    n = len(bars)
    out: list[float | None] = [None] * n
    for i in range(n - horizon):
        window = rets[i + 1 : i + 1 + horizon]
        if any(r is None for r in window):
            continue
        out[i] = _rms([r for r in window if r is not None])
    return out


def _persistence_vol(rets: Sequence[float | None], i: int, horizon: int) -> float | None:
    """Persistence baseline at bar ``i``: the realised RMS vol of the *trailing*
    ``horizon`` returns ending at ``i`` — known at decision time, so anti-lookahead.
    ``None`` when the trailing window runs off the start or contains an undefined
    return."""

    if i - horizon + 1 < 1:
        return None
    window = rets[i - horizon + 1 : i + 1]
    if any(r is None for r in window):
        return None
    return _rms([r for r in window if r is not None])


def _ewma_vol_series(rets: Sequence[float | None], lam: float) -> list[float | None]:
    """Causal RiskMetrics EWMA volatility per bar: ``sigma2[i] = lam*sigma2[i-1] +
    (1-lam)*r[i]**2``, seeded from the first defined squared return. Entry ``i`` reads
    only returns up to ``i`` (anti-lookahead). ``None`` until the recursion is seeded."""

    out: list[float | None] = [None] * len(rets)
    sigma2: float | None = None
    for i, r in enumerate(rets):
        if r is None:
            out[i] = math.sqrt(sigma2) if sigma2 is not None else None
            continue
        sigma2 = r * r if sigma2 is None else lam * sigma2 + (1.0 - lam) * r * r
        out[i] = math.sqrt(sigma2)
    return out


def _qlike(actual_vol: float, pred_vol: float) -> float:
    """QLIKE loss on variance: ``v/vhat - ln(v/vhat) - 1`` with ``v = actual_vol**2``,
    ``vhat = pred_vol**2``. Zero when the forecast variance equals the realised
    variance, positive otherwise; asymmetric (penalises under-prediction of a large
    move more than over-prediction) — the property that makes it the standard vol loss.
    Both vols are floored at `VOL_FLOOR` first so the ratio and its log are finite."""

    v = max(actual_vol, VOL_FLOOR) ** 2
    vhat = max(pred_vol, VOL_FLOOR) ** 2
    ratio = v / vhat
    return ratio - math.log(ratio) - 1.0


def _align_float_samples(
    rows: Sequence[FeatureRow | None], labels: Sequence[float | None]
) -> tuple[list[FeatureRow], list[float]]:
    """Pair feature rows with float labels, keeping only indices where **both** are
    defined — the regression analogue of `model.align_samples`."""

    if len(rows) != len(labels):
        raise ValueError(f"rows ({len(rows)}) and labels ({len(labels)}) length mismatch")
    kept_rows: list[FeatureRow] = []
    kept_labels: list[float] = []
    for row, label in zip(rows, labels, strict=True):
        if row is None or label is None:
            continue
        kept_rows.append(row)
        kept_labels.append(label)
    return kept_rows, kept_labels


def _train_log_vol(
    rows: Sequence[FeatureRow], vols: Sequence[float], params: ModelParams
) -> HistGradientBoostingRegressor:
    """Fit the regressor on ``log(max(vol, VOL_FLOOR))`` — positive, variance-stabilised
    target — single-threaded and seeded for byte-identical reruns (the ADR-0040
    determinism mechanism, mirrored from `model.train`)."""

    reg = HistGradientBoostingRegressor(
        random_state=params.seed,
        max_iter=params.max_iter,
        learning_rate=params.learning_rate,
        max_leaf_nodes=params.max_leaf_nodes,
        max_depth=params.max_depth,
        min_samples_leaf=params.min_samples_leaf,
        l2_regularization=params.l2_regularization,
        early_stopping=False,
    )
    x = [list(row.values) for row in rows]
    y = [math.log(max(v, VOL_FLOOR)) for v in vols]
    with threadpoolctl.threadpool_limits(limits=1):
        reg.fit(x, y)
    return reg


def _predict_vol(reg: HistGradientBoostingRegressor, rows: Sequence[FeatureRow]) -> list[float]:
    """Per-row predicted volatility: the regressor predicts log-vol; exp back to a
    strictly-positive vol. Empty input → empty output."""

    if not rows:
        return []
    x = [list(row.values) for row in rows]
    with threadpoolctl.threadpool_limits(limits=1):
        log_preds = reg.predict(x)
    return [math.exp(float(p)) for p in log_preds]


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def validate_volatility(
    bars: Sequence[Bar],
    *,
    horizon_bars: int = 1,
    n_splits: int = 5,
    model_params: ModelParams | None = None,
    feature_rows: Sequence[FeatureRow | None] | None = None,
    ewma_lambda: float = DEFAULT_EWMA_LAMBDA,
    log_residual_sink: list[float] | None = None,
) -> VolatilityValidation:
    """Run expanding-window walk-forward validation of the volatility regressor and
    return the QLIKE baseline-gated verdict.

    ``log_residual_sink`` (optional) collects the out-of-sample log residuals
    ``ln(actual_vol) - ln(pred_vol)`` for every scored test bar, so a caller can size an
    honest predictive band from the model's realised OOS spread without re-fitting.

    Reuses `fold_bounds` for the contiguous fold partition and purges the trailing
    ``horizon`` train samples exactly as the direction harness does (a train sample
    ``i`` whose label reads ``close[i + horizon]`` must sit strictly before the test
    window). Every per-fold fit is seeded and single-threaded, so identical inputs
    yield an identical `VolatilityValidation`.

    Raises `VolatilityValidationError` when ``n_splits`` is invalid for the series. An
    empty/too-short series is not an error: every fold is unscored and the verdict is an
    honest ``model_qlike=None``, ``beats_baseline=False``."""

    if n_splits < 2:
        raise VolatilityValidationError(f"n_splits must be >= 2, got {n_splits}")
    if n_splits > len(bars):
        raise VolatilityValidationError(f"n_splits ({n_splits}) exceeds bar count ({len(bars)})")
    if feature_rows is not None and len(feature_rows) != len(bars):
        raise VolatilityValidationError(
            f"feature_rows ({len(feature_rows)}) must align to bars ({len(bars)})"
        )

    params = model_params if model_params is not None else ModelParams()
    closes = [b.close for b in bars]
    rets = _log_returns(closes)
    rows = list(feature_rows) if feature_rows is not None else build_feature_rows(bars)
    labels = build_volatility_labels(bars, horizon_bars)
    ewma = _ewma_vol_series(rets, ewma_lambda)

    fold_results: list[VolatilityFoldScore] = []
    model_losses: list[float] = []
    persistence_losses: list[float] = []
    ewma_losses: list[float] = []

    for fold_index, (start, end) in enumerate(fold_bounds(len(bars), n_splits)):
        train_cutoff = max(0, start - horizon_bars)
        train_rows, train_labels = _align_float_samples(rows[:train_cutoff], labels[:train_cutoff])
        test_rows, test_labels = _align_float_samples(rows[start:end], labels[start:end])

        # A regressor needs at least two distinct targets to fit a non-degenerate model;
        # a single repeated vol (or no samples) is the training seed, not a scored fold.
        trainable = len(train_rows) > 0 and len({round(v, 12) for v in train_labels}) >= 2
        if not trainable or not test_rows:
            fold_results.append(
                VolatilityFoldScore(
                    fold_index=fold_index,
                    n_test=len(test_rows),
                    model_qlike=None,
                    persistence_qlike=None,
                    ewma_qlike=None,
                )
            )
            continue

        reg = _train_log_vol(train_rows, train_labels, params)
        model_preds = _predict_vol(reg, test_rows)

        fold_model: list[float] = []
        fold_pers: list[float] = []
        fold_ewma: list[float] = []
        for row, actual, pred in zip(test_rows, test_labels, model_preds, strict=True):
            i = row.bar_index
            pers = _persistence_vol(rets, i, horizon_bars)
            ew = ewma[i]
            if pers is None or ew is None:
                continue
            fold_model.append(_qlike(actual, pred))
            fold_pers.append(_qlike(actual, pers))
            fold_ewma.append(_qlike(actual, ew))
            if log_residual_sink is not None:
                log_residual_sink.append(
                    math.log(max(actual, VOL_FLOOR)) - math.log(max(pred, VOL_FLOOR))
                )

        fold_results.append(
            VolatilityFoldScore(
                fold_index=fold_index,
                n_test=len(fold_model),
                model_qlike=_mean(fold_model),
                persistence_qlike=_mean(fold_pers),
                ewma_qlike=_mean(fold_ewma),
            )
        )
        model_losses.extend(fold_model)
        persistence_losses.extend(fold_pers)
        ewma_losses.extend(fold_ewma)

    n_scored = len(model_losses)
    model_qlike = _mean(model_losses)
    persistence_qlike = _mean(persistence_losses)
    ewma_qlike = _mean(ewma_losses)

    baselines: list[tuple[BaselineKind, float]] = []
    if persistence_qlike is not None:
        baselines.append(("persistence", persistence_qlike))
    if ewma_qlike is not None:
        baselines.append(("ewma", ewma_qlike))
    # The baseline to beat is the *stronger* (lower QLIKE) of the two; ties break to
    # persistence by the list order, a deterministic choice.
    best = min(baselines, key=lambda kv: kv[1]) if baselines else None
    baseline_kind = best[0] if best is not None else None
    baseline_qlike = best[1] if best is not None else None
    score_margin = (
        baseline_qlike - model_qlike
        if baseline_qlike is not None and model_qlike is not None
        else None
    )
    beats_baseline = (
        model_qlike is not None and baseline_qlike is not None and model_qlike < baseline_qlike
    )

    return VolatilityValidation(
        horizon_bars=horizon_bars,
        n_splits=n_splits,
        n_scored=n_scored,
        model_qlike=model_qlike,
        baseline_qlike=baseline_qlike,
        baseline_kind=baseline_kind,
        persistence_qlike=persistence_qlike,
        ewma_qlike=ewma_qlike,
        score_margin=score_margin,
        beats_baseline=beats_baseline,
        folds=fold_results,
    )


class VolatilityForecast(BaseModel):
    """A volatility forecast for the next ``horizon_bars`` (Plan 0077, ADR-0070).

    ``predicted_vol`` is the model's per-bar RMS volatility for the forecast window,
    with ``band`` a 1-sigma out-of-sample interval around it (both ``None`` when the
    series could not train a final model — then the honest answer is the baseline).
    ``baseline_vol`` is the current reading of the walk-forward-winning baseline
    (``baseline_kind``), so a no-edge verdict still surfaces a usable number.
    ``beats_baseline`` (echoed from ``validation``) says whether to trust the model over
    the baseline; ``provenance`` makes the run reproducible (``None`` only when no model
    trained)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    as_of_bar_ts: datetime
    horizon_bars: int
    predicted_vol: float | None
    band: tuple[float, float] | None
    baseline_vol: float | None
    baseline_kind: BaselineKind | None
    beats_baseline: bool
    score_margin: float | None
    validation: VolatilityValidation
    provenance: ForecastProvenance | None


def _baseline_reading(
    kind: BaselineKind | None,
    rets: Sequence[float | None],
    ewma: Sequence[float | None],
    bar_index: int,
    horizon: int,
) -> float | None:
    """The current reading of the walk-forward-winning baseline at the as-of bar, so a
    no-edge forecast still surfaces the number the user should fall back on. ``None``
    when validation picked no baseline (no scored folds) or the reading is undefined."""

    if kind == "persistence":
        return _persistence_vol(rets, bar_index, horizon)
    if kind == "ewma":
        return ewma[bar_index]
    return None


def forecast_volatility(
    bars: Sequence[Bar],
    *,
    symbol: str,
    timeframe: str,
    horizon_bars: int = 1,
    n_splits: int = 5,
    model_params: ModelParams | None = None,
    feature_rows: Sequence[FeatureRow | None] | None = None,
    ewma_lambda: float = DEFAULT_EWMA_LAMBDA,
) -> VolatilityForecast:
    """Produce a volatility forecast for the bar series: walk-forward-validate the
    regressor (the honest edge verdict), then train a final model on all causal samples
    and predict the volatility of the next ``horizon_bars`` from the latest bar's feature
    row. The point prediction is present whenever a model could train; ``beats_baseline``
    (from the validation) says whether it should be trusted over the baseline reading,
    which is always surfaced when the walk-forward picked one."""

    params = model_params if model_params is not None else ModelParams()
    rows = list(feature_rows) if feature_rows is not None else build_feature_rows(bars)

    residuals: list[float] = []
    validation = validate_volatility(
        bars,
        horizon_bars=horizon_bars,
        n_splits=n_splits,
        model_params=params,
        feature_rows=rows,
        ewma_lambda=ewma_lambda,
        log_residual_sink=residuals,
    )

    closes = [b.close for b in bars]
    rets = _log_returns(closes)
    ewma = _ewma_vol_series(rets, ewma_lambda)
    labels = build_volatility_labels(bars, horizon_bars)

    # Forecast off the latest defined feature row — the current bar, whose label is the
    # (future) volatility we are predicting, so it never entered training.
    last_row = next((r for r in reversed(rows) if r is not None), None)
    as_of_bar_ts = last_row.event_ts if last_row is not None else bars[-1].event_ts
    last_index = last_row.bar_index if last_row is not None else len(bars) - 1

    baseline_vol = _baseline_reading(validation.baseline_kind, rets, ewma, last_index, horizon_bars)

    train_rows, train_labels = _align_float_samples(rows, labels)
    trainable = (
        last_row is not None
        and len(train_rows) > 0
        and len({round(v, 12) for v in train_labels}) >= 2
    )

    predicted_vol: float | None = None
    band: tuple[float, float] | None = None
    provenance: ForecastProvenance | None = None
    if trainable:
        assert last_row is not None
        reg = _train_log_vol(train_rows, train_labels, params)
        predicted_vol = _predict_vol(reg, [last_row])[0]
        # 1-sigma band from the model's own out-of-sample log-residual spread; a flat
        # (or unmeasurable) spread collapses the band onto the point estimate.
        resid_std = statistics.pstdev(residuals) if len(residuals) >= 2 else 0.0
        band = (predicted_vol * math.exp(-resid_std), predicted_vol * math.exp(resid_std))
        training_cutoff = train_rows[-1].event_ts
        lib_versions = model_lib_versions()
        provenance = ForecastProvenance(
            model_version=_compute_vol_model_version(
                feature_set_id=FEATURE_SET_ID,
                model_params=params,
                horizon_bars=horizon_bars,
                ewma_lambda=ewma_lambda,
                training_cutoff=training_cutoff,
                lib_versions=lib_versions,
            ),
            feature_set_id=FEATURE_SET_ID,
            training_cutoff=training_cutoff,
            seed=params.seed,
            lib_versions=lib_versions,
        )

    return VolatilityForecast(
        symbol=symbol,
        timeframe=timeframe,
        as_of_bar_ts=as_of_bar_ts,
        horizon_bars=horizon_bars,
        predicted_vol=predicted_vol,
        band=band,
        baseline_vol=baseline_vol,
        baseline_kind=validation.baseline_kind,
        beats_baseline=validation.beats_baseline,
        score_margin=validation.score_margin,
        validation=validation,
        provenance=provenance,
    )


__all__ = [
    "DEFAULT_EWMA_LAMBDA",
    "MODEL_CLASS",
    "VOL_FLOOR",
    "BaselineKind",
    "VolatilityFoldScore",
    "VolatilityForecast",
    "VolatilityValidation",
    "VolatilityValidationError",
    "build_volatility_labels",
    "forecast_volatility",
    "validate_volatility",
]
