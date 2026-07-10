"""Regime-transition forecast: a non-directional forecast kind (Plan 0077 phase 2, ADR-0070).

The **current regime** is a trailing classification of market state on two axes:

* **trend** — reused verbatim from `snapshot._classify_trend` (EMA stack + ADX →
  ``UP`` / ``DOWN`` / ``SIDEWAYS``), so there is exactly one trend definition in the
  codebase and this kind inherits any change to it (e.g. the Ichimoku veto of Plan
  0073). This module does **not** re-derive trend.
* **volatility** — a 2-state ``quiet`` / ``volatile`` split of ATR% (ATR / close) by
  whether the current reading sits above the trailing median of its own recent history.
  Adaptive (no fixed threshold) and trailing (the percentile reads only past+current
  bars), so it is anti-lookahead by construction.

Their product is a 6-value `RegimeState`. This is deliberately distinct from the
crypto-macro **current-state** nowcast of ADR-0027 (a whole-market structural read):
this regime is per-symbol, technical, and — crucially — *predictive*, via a
**transition forecast**: a classifier that predicts next-period regime from the shared
feature matrix, gated against a **persistence** baseline (regime unchanged) by the
**Brier score** out of sample. Persistence is a strong baseline (regimes are sticky),
so beating it is a real, non-circular signal. The machinery is shared with the other
forecast kinds — ``fold_bounds`` folds, purge-by-horizon, seeded/single-thread
determinism (ADR-0040) — but the target is multiclass and the loss is Brier, so like the
volatility kind it gets its own validation entry point rather than reusing the
``Direction``-locked ``validate``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

import threadpoolctl
from pydantic import BaseModel, ConfigDict
from sklearn.ensemble import HistGradientBoostingClassifier

from market_analyser.analysis import indicators as ind
from market_analyser.analysis.snapshot import _classify_trend
from market_analyser.analysis.types import Trend
from market_analyser.backtest.walk_forward import fold_bounds
from market_analyser.data.types import Bar
from market_analyser.forecast.features import FEATURE_SET_ID, FeatureRow, build_feature_rows
from market_analyser.forecast.model import ModelParams, model_lib_versions
from market_analyser.forecast.result import ForecastProvenance

# ATR / ADX periods match the analyst surface (snapshot / features) so the regime reads
# the same parameterisation the rest of the app reports.
ATR_PERIOD = 14
ADX_PERIOD = 14

# The volatility axis splits ATR% at the trailing median of a rolling window; a reading
# needs at least MIN_VOL_SAMPLE observations before it is classified (else the regime is
# undefined for that bar). Constants, not tuned here.
VOL_PERCENTILE_WINDOW = 90
VOL_SPLIT_PERCENTILE = 50.0  # median: above -> volatile, at/below -> quiet
MIN_VOL_SAMPLE = 20

MODEL_CLASS = "HistGradientBoostingClassifier"


class VolState(StrEnum):
    """The volatility axis of the regime taxonomy."""

    QUIET = "quiet"
    VOLATILE = "volatile"


class RegimeState(StrEnum):
    """The 6-value regime taxonomy: the product of the reused ``Trend`` axis and the
    2-state `VolState` axis. Ordered deterministically by value so the classifier's
    class order is reproducible."""

    DOWN_QUIET = "down_quiet"
    DOWN_VOLATILE = "down_volatile"
    SIDEWAYS_QUIET = "sideways_quiet"
    SIDEWAYS_VOLATILE = "sideways_volatile"
    UP_QUIET = "up_quiet"
    UP_VOLATILE = "up_volatile"


_TREND_PREFIX: dict[Trend, str] = {
    Trend.UP: "up",
    Trend.DOWN: "down",
    Trend.SIDEWAYS: "sideways",
}


def _compose_regime(trend: Trend, vol: VolState) -> RegimeState:
    """Combine the trend and volatility axes into a `RegimeState`."""

    return RegimeState(f"{_TREND_PREFIX[trend]}_{vol.value}")


class RegimeValidationError(ValueError):
    """Raised when the walk-forward configuration is invalid for the bar series."""


class RegimeFoldScore(BaseModel):
    """Out-of-sample Brier score for one scored fold. ``None`` marks an unscored fold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fold_index: int
    n_test: int
    model_brier: float | None
    persistence_brier: float | None


class RegimeValidation(BaseModel):
    """The transition verdict. ``model_brier`` is pooled out-of-sample Brier score;
    ``persistence_brier`` is the naive "regime unchanged" baseline on the same test bars;
    ``beats_baseline`` is the gate (lower Brier is better, so ``model < persistence``).
    ``score_margin`` is ``persistence_brier - model_brier`` (positive ⇒ improvement) so a
    thin beat reads as thin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_bars: int
    n_splits: int
    n_scored: int
    model_brier: float | None
    persistence_brier: float | None
    score_margin: float | None
    beats_baseline: bool
    folds: list[RegimeFoldScore]


def _atr_pct_series(bars: Sequence[Bar]) -> list[float | None]:
    """Trailing ATR as a fraction of close, per bar — the raw volatility measure the
    regime's vol axis buckets. ``None`` while ATR is undefined or close is non-positive."""

    atr = ind.atr(bars, ATR_PERIOD)
    out: list[float | None] = []
    for bar, a in zip(bars, atr, strict=True):
        out.append(a / bar.close if a is not None and bar.close > 0.0 else None)
    return out


def _vol_state_series(atr_pct: Sequence[float | None]) -> list[VolState | None]:
    """Per-bar quiet/volatile classification: ``VOLATILE`` when ATR% exceeds the trailing
    median of the last `VOL_PERCENTILE_WINDOW` defined readings (including the current
    one), else ``QUIET``. ``None`` until `MIN_VOL_SAMPLE` readings exist. Trailing-only —
    the window never reaches past bar ``i`` — so the classification cannot look ahead."""

    out: list[VolState | None] = [None] * len(atr_pct)
    for i, cur in enumerate(atr_pct):
        if cur is None:
            continue
        window = [
            v for v in atr_pct[max(0, i - VOL_PERCENTILE_WINDOW + 1) : i + 1] if v is not None
        ]
        if len(window) < MIN_VOL_SAMPLE:
            continue
        threshold = _percentile(window, VOL_SPLIT_PERCENTILE)
        out[i] = VolState.VOLATILE if cur > threshold else VolState.QUIET
    return out


def _percentile(sample: Sequence[float], pct: float) -> float:
    """The ``pct``-th percentile of ``sample`` by linear interpolation between the two
    nearest ranks (the numpy default). ``pct`` in [0, 100]. ``sample`` is non-empty."""

    ordered = sorted(sample)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return ordered[lo]
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def build_regime_labels(bars: Sequence[Bar]) -> list[RegimeState | None]:
    """The trailing current-regime label per bar: the reused ``_classify_trend`` output
    crossed with the volatility state. ``None`` until the volatility axis is defined (the
    trend axis is always defined — ``_classify_trend`` falls back to ``SIDEWAYS``). The
    trend component is `_classify_trend(closes[0..=i], adx[i])` verbatim — one trend
    definition, not a second one."""

    closes = [b.close for b in bars]
    adx = ind.adx(bars, ADX_PERIOD)
    vol_states = _vol_state_series(_atr_pct_series(bars))
    out: list[RegimeState | None] = [None] * len(bars)
    for i in range(len(bars)):
        vol = vol_states[i]
        if vol is None:
            continue
        adx_i = adx[i]
        adx_val = adx_i.adx if adx_i is not None else None
        trend = _classify_trend(closes[: i + 1], adx_val)
        out[i] = _compose_regime(trend, vol)
    return out


def _align_regime_samples(
    rows: Sequence[FeatureRow | None], labels: Sequence[RegimeState | None]
) -> tuple[list[FeatureRow], list[RegimeState]]:
    """Pair feature rows with regime labels, keeping only indices where both are
    defined — the regime analogue of `model.align_samples`."""

    if len(rows) != len(labels):
        raise ValueError(f"rows ({len(rows)}) and labels ({len(labels)}) length mismatch")
    kept_rows: list[FeatureRow] = []
    kept_labels: list[RegimeState] = []
    for row, label in zip(rows, labels, strict=True):
        if row is None or label is None:
            continue
        kept_rows.append(row)
        kept_labels.append(label)
    return kept_rows, kept_labels


def _train_regime(
    rows: Sequence[FeatureRow], labels: Sequence[RegimeState], params: ModelParams
) -> tuple[HistGradientBoostingClassifier, tuple[RegimeState, ...]]:
    """Fit the multiclass regime classifier, single-threaded and seeded for byte-identical
    reruns (the ADR-0040 mechanism). Returns the estimator and its class order (the
    column order of ``predict_proba``)."""

    clf = HistGradientBoostingClassifier(
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
    y = [label.value for label in labels]
    with threadpoolctl.threadpool_limits(limits=1):
        clf.fit(x, y)
    classes = tuple(RegimeState(c) for c in clf.classes_)
    return clf, classes


def _predict_regime_proba(
    clf: HistGradientBoostingClassifier,
    classes: tuple[RegimeState, ...],
    rows: Sequence[FeatureRow],
) -> list[dict[RegimeState, float]]:
    """Per-row probability over the **full** regime taxonomy: classes the model never saw
    at fit time are filled with ``0.0`` so every dict has the same six keys."""

    if not rows:
        return []
    x = [list(row.values) for row in rows]
    with threadpoolctl.threadpool_limits(limits=1):
        proba = clf.predict_proba(x)
    out: list[dict[RegimeState, float]] = []
    for prob_row in proba:
        dist = {state: 0.0 for state in RegimeState}
        for cls, value in zip(classes, prob_row, strict=True):
            dist[cls] = float(value)
        out.append(dist)
    return out


def _brier(dist: dict[RegimeState, float], actual: RegimeState) -> float:
    """Multiclass Brier score for one prediction: ``sum_k (p_k - y_k)**2`` over the full
    taxonomy, ``y`` one-hot on ``actual``. Bounded in [0, 2]; 0 is a perfect confident
    call. A proper scoring rule, so a well-calibrated distribution scores best."""

    return sum((p - (1.0 if state == actual else 0.0)) ** 2 for state, p in dist.items())


def _persistence_dist(current: RegimeState) -> dict[RegimeState, float]:
    """The persistence baseline as a distribution: all mass on the current regime (the
    naive 'nothing changes' forecast)."""

    return {state: (1.0 if state == current else 0.0) for state in RegimeState}


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _compute_regime_model_version(
    *,
    feature_set_id: str,
    model_params: ModelParams,
    horizon_bars: int,
    training_cutoff: datetime,
    lib_versions: dict[str, str],
) -> str:
    """Deterministic 16-hex-char hash over the regime model's prediction-affecting inputs
    (the ADR-0040 mechanism): feature-set id, estimator class + hyperparameters, target
    horizon, training cutoff, library versions."""

    payload: dict[str, Any] = {
        "feature_set_id": feature_set_id,
        "model_class": MODEL_CLASS,
        "hyperparameters": model_params.model_dump(mode="json"),
        "horizon_bars": horizon_bars,
        "training_cutoff": training_cutoff.isoformat(),
        "lib_versions": lib_versions,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def validate_regime(
    bars: Sequence[Bar],
    *,
    horizon_bars: int = 1,
    n_splits: int = 5,
    model_params: ModelParams | None = None,
    feature_rows: Sequence[FeatureRow | None] | None = None,
) -> RegimeValidation:
    """Walk-forward-validate the transition classifier and return the Brier baseline-gated
    verdict. Reuses `fold_bounds` and purges the trailing ``horizon`` train samples (a
    label ``regime[i + horizon]`` must sit strictly before the test window). Seeded /
    single-thread, so identical inputs yield an identical `RegimeValidation`.

    Raises `RegimeValidationError` on an invalid ``n_splits``. A too-short series is not an
    error: every fold is unscored and the verdict is an honest ``beats_baseline=False``."""

    if n_splits < 2:
        raise RegimeValidationError(f"n_splits must be >= 2, got {n_splits}")
    if n_splits > len(bars):
        raise RegimeValidationError(f"n_splits ({n_splits}) exceeds bar count ({len(bars)})")
    if feature_rows is not None and len(feature_rows) != len(bars):
        raise RegimeValidationError(
            f"feature_rows ({len(feature_rows)}) must align to bars ({len(bars)})"
        )

    params = model_params if model_params is not None else ModelParams()
    rows = list(feature_rows) if feature_rows is not None else build_feature_rows(bars)
    regimes = build_regime_labels(bars)
    # The label at bar i is the regime `horizon` bars ahead (the transition target).
    labels: list[RegimeState | None] = [
        regimes[i + horizon_bars] if i + horizon_bars < len(bars) else None
        for i in range(len(bars))
    ]

    fold_results: list[RegimeFoldScore] = []
    model_losses: list[float] = []
    persistence_losses: list[float] = []

    for fold_index, (start, end) in enumerate(fold_bounds(len(bars), n_splits)):
        train_cutoff = max(0, start - horizon_bars)
        train_rows, train_labels = _align_regime_samples(rows[:train_cutoff], labels[:train_cutoff])
        test_rows, test_labels = _align_regime_samples(rows[start:end], labels[start:end])

        trainable = len(train_rows) > 0 and len({lab for lab in train_labels}) >= 2
        if not trainable or not test_rows:
            fold_results.append(
                RegimeFoldScore(
                    fold_index=fold_index,
                    n_test=len(test_rows),
                    model_brier=None,
                    persistence_brier=None,
                )
            )
            continue

        clf, classes = _train_regime(train_rows, train_labels, params)
        model_dists = _predict_regime_proba(clf, classes, test_rows)

        fold_model: list[float] = []
        fold_pers: list[float] = []
        for row, actual, dist in zip(test_rows, test_labels, model_dists, strict=True):
            current = regimes[row.bar_index]
            if current is None:
                continue
            fold_model.append(_brier(dist, actual))
            fold_pers.append(_brier(_persistence_dist(current), actual))

        fold_results.append(
            RegimeFoldScore(
                fold_index=fold_index,
                n_test=len(fold_model),
                model_brier=_mean(fold_model),
                persistence_brier=_mean(fold_pers),
            )
        )
        model_losses.extend(fold_model)
        persistence_losses.extend(fold_pers)

    n_scored = len(model_losses)
    model_brier = _mean(model_losses)
    persistence_brier = _mean(persistence_losses)
    score_margin = (
        persistence_brier - model_brier
        if persistence_brier is not None and model_brier is not None
        else None
    )
    beats_baseline = (
        model_brier is not None
        and persistence_brier is not None
        and model_brier < persistence_brier
    )

    return RegimeValidation(
        horizon_bars=horizon_bars,
        n_splits=n_splits,
        n_scored=n_scored,
        model_brier=model_brier,
        persistence_brier=persistence_brier,
        score_margin=score_margin,
        beats_baseline=beats_baseline,
        folds=fold_results,
    )


class RegimeForecast(BaseModel):
    """A regime-transition forecast (Plan 0077, ADR-0070). ``current_regime`` is the
    trailing classification at the as-of bar; ``transition_probs`` is the model's
    probability over next-period regimes (``None`` when no model could train — then the
    honest fallback is persistence, i.e. the regime stays ``current_regime``).
    ``beats_baseline`` (from ``validation``) says whether to trust the transition model
    over persistence; ``provenance`` makes the run reproducible."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    as_of_bar_ts: datetime
    horizon_bars: int
    current_regime: RegimeState | None
    transition_probs: dict[RegimeState, float] | None
    beats_baseline: bool
    score_margin: float | None
    validation: RegimeValidation
    provenance: ForecastProvenance | None


def forecast_regime(
    bars: Sequence[Bar],
    *,
    symbol: str,
    timeframe: str,
    horizon_bars: int = 1,
    n_splits: int = 5,
    model_params: ModelParams | None = None,
    feature_rows: Sequence[FeatureRow | None] | None = None,
) -> RegimeForecast:
    """Produce a regime-transition forecast: walk-forward-validate the classifier (the
    honest edge verdict vs persistence), then train a final model on all causal samples
    and predict next-period regime probabilities from the latest bar's feature row. The
    current regime is always surfaced; ``beats_baseline`` says whether to trust the
    transition distribution over 'regime unchanged'."""

    params = model_params if model_params is not None else ModelParams()
    rows = list(feature_rows) if feature_rows is not None else build_feature_rows(bars)

    validation = validate_regime(
        bars,
        horizon_bars=horizon_bars,
        n_splits=n_splits,
        model_params=params,
        feature_rows=rows,
    )

    regimes = build_regime_labels(bars)
    labels: list[RegimeState | None] = [
        regimes[i + horizon_bars] if i + horizon_bars < len(bars) else None
        for i in range(len(bars))
    ]

    last_row = next((r for r in reversed(rows) if r is not None), None)
    as_of_bar_ts = last_row.event_ts if last_row is not None else bars[-1].event_ts
    current_regime = regimes[last_row.bar_index] if last_row is not None else None

    train_rows, train_labels = _align_regime_samples(rows, labels)
    trainable = (
        last_row is not None and len(train_rows) > 0 and len({lab for lab in train_labels}) >= 2
    )

    transition_probs: dict[RegimeState, float] | None = None
    provenance: ForecastProvenance | None = None
    if trainable:
        assert last_row is not None
        clf, classes = _train_regime(train_rows, train_labels, params)
        transition_probs = _predict_regime_proba(clf, classes, [last_row])[0]
        training_cutoff = train_rows[-1].event_ts
        lib_versions = model_lib_versions()
        provenance = ForecastProvenance(
            model_version=_compute_regime_model_version(
                feature_set_id=FEATURE_SET_ID,
                model_params=params,
                horizon_bars=horizon_bars,
                training_cutoff=training_cutoff,
                lib_versions=lib_versions,
            ),
            feature_set_id=FEATURE_SET_ID,
            training_cutoff=training_cutoff,
            seed=params.seed,
            lib_versions=lib_versions,
        )

    return RegimeForecast(
        symbol=symbol,
        timeframe=timeframe,
        as_of_bar_ts=as_of_bar_ts,
        horizon_bars=horizon_bars,
        current_regime=current_regime,
        transition_probs=transition_probs,
        beats_baseline=validation.beats_baseline,
        score_margin=validation.score_margin,
        validation=validation,
        provenance=provenance,
    )


__all__ = [
    "MODEL_CLASS",
    "RegimeFoldScore",
    "RegimeForecast",
    "RegimeState",
    "RegimeValidation",
    "RegimeValidationError",
    "VolState",
    "build_regime_labels",
    "forecast_regime",
    "validate_regime",
]
