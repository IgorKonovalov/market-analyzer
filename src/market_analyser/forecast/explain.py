"""Forecast explainability (Plan 0063 phase 1, ADR-0058).

Every forecast says **exactly why** — for the developer curating sources and
method, and for the trader deciding. The method is out-of-sample permutation
importance: per scored walk-forward fold, ``sklearn.inspection.
permutation_importance`` runs on **that fold's own model over that fold's
out-of-sample test slice** (the `ScoredFold` capture from ``validation.py``),
then per-feature results are aggregated across folds into a mean and a spread.
This measures what the *validated* models lean on — never what a final
full-data fit memorised (the in-sample-importance confusion ADR-0058 rejects).

Determinism (ADR-0040 discipline): each fold's permutation is seeded from the
call's own seed plus the fold index, single-threaded via ``threadpoolctl``, and
every aggregation is explicit-order Python arithmetic — identical inputs
produce a byte-identical `ForecastExplanation` dump.

Honesty (ADR-0030 posture): importance is association within the model, not
causation — the fixed ``disclaimer`` field says so on every explanation, and
correlated features split credit (funding and OI move together), so a low rank
is evidence of removability, not proof. A horizon with no scored folds carries
no importances and states that in ``note`` instead of fabricating a ranking.

Delivery is split per ADR-0058: the tool layer attaches a compact
`ExplanationSummary` (top drivers + artifact path, built by
``summarize_explanation``) to `ForecastProvenance`, and persists the complete
`ForecastExplanationArtifact` under ``runs_dir/forecast/…`` — diffable across
method changes, auditable after the conversation is gone.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from statistics import fmean, pstdev

import threadpoolctl
from pydantic import BaseModel, ConfigDict
from sklearn.inspection import permutation_importance

from market_analyser.forecast.features import (
    FEATURE_NAMES,
    FEATURE_NAMES_V2,
    FEATURE_NAMES_V2_DEEP,
    FEATURE_SET_ID,
    FEATURE_SET_ID_V2,
    FEATURE_SET_ID_V2_DEEP,
    FeatureRow,
)
from market_analyser.forecast.result import (
    ExplanationDriver,
    ExplanationSummary,
    ForecastProvenance,
    MultiHorizonForecastResult,
    SeriesInput,
)
from market_analyser.forecast.validation import ForecastValidation, ScoredFold

# Permutation repeats per feature per fold. Cost is ~features x repeats x fold
# rows extra predictions per horizon (HGB predict is cheap); if a live call
# grows noticeably slower this constant drops before anything structural
# (Plan 0063 risk note).
N_PERMUTATION_REPEATS = 5

# How many drivers the compact wire summary carries. The full ranking (with
# per-fold spread) always lives in the artifact.
TOP_N_DRIVERS = 5

# The fixed association-not-causation disclaimer every explanation carries
# (ADR-0058: documented, not solved — correlated features share credit).
IMPORTANCE_DISCLAIMER = (
    "Permutation importance measures association within the validated model, "
    "not causation. Correlated features share credit, so a low-ranked feature "
    "is evidence of removability, not proof; re-run the feature-set comparison "
    "before deleting a source."
)

# The frozen name tuple behind each feature-set id — the spelling every
# explanation reports its features in. A tier added to the ladder (ADR-0057)
# must register here to be explainable.
_FEATURE_NAMES_BY_SET_ID: dict[str, tuple[str, ...]] = {
    FEATURE_SET_ID: FEATURE_NAMES,
    FEATURE_SET_ID_V2: FEATURE_NAMES_V2,
    FEATURE_SET_ID_V2_DEEP: FEATURE_NAMES_V2_DEEP,
}

NOTE_NO_SCORED_FOLDS = (
    "no scored out-of-sample walk-forward folds for this horizon; importances not computed"
)


class FeatureImportance(BaseModel):
    """One feature's aggregated out-of-sample permutation importance: ``mean``
    across the scored folds and ``spread`` (population standard deviation
    across those folds — 0.0 with a single scored fold). The spread is carried
    precisely because small folds make noisy rankings: it is how a robust
    driver is told apart from a one-fold artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    feature: str
    mean: float
    spread: float


class ForecastExplanation(BaseModel):
    """One horizon's complete explanation: the full per-feature importance
    ranking (ordered — mean descending, feature name as the deterministic
    tie-break), the predict-row's actual feature values (``None`` when the
    horizon had no as-of row to predict), and the fixed disclaimer. ``note``
    states honestly when there was nothing to measure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_bars: int
    feature_set_id: str
    n_folds_used: int
    importances: tuple[FeatureImportance, ...]
    predict_row: dict[str, float] | None
    note: str | None = None
    disclaimer: str = IMPORTANCE_DISCLAIMER


class HorizonExplanationRecord(BaseModel):
    """One horizon's artifact block: the explanation beside the walk-forward
    fold table and the provenance it explains — everything a developer needs
    to audit the ranking against the validation that produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_bars: int
    explanation: ForecastExplanation
    validation: ForecastValidation
    provenance: ForecastProvenance | None


class ForecastExplanationArtifact(BaseModel):
    """The complete persisted explanation for one forecast call
    (``runs_dir/forecast/<started_at>-<symbol>-<timeframe>/explanation.json``).

    ``started_at`` is run provenance — with the on-disk path, one of the two
    documented exceptions to byte-identical re-runs (the ADR-0018 posture).
    ``series_inputs`` repeats the call's per-series freshness so the artifact
    reads standalone. ``local_attribution`` is the reserved ADR-0058 slot for
    a future per-call attribution method (`shap` was rejected for v1); it is
    always ``None`` here so a later plan can fill it without a schema break."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    timeframe: str
    as_of_bar_ts: datetime
    feature_set_id: str
    started_at: datetime
    series_inputs: tuple[SeriesInput, ...]
    horizons: tuple[HorizonExplanationRecord, ...]
    local_attribution: None = None


def feature_names_for_set(feature_set_id: str) -> tuple[str, ...]:
    """The frozen, ordered feature names behind ``feature_set_id``. Raises
    ``KeyError`` for an unregistered id — an unexplainable tier is a bug, not
    a silent empty explanation."""

    return _FEATURE_NAMES_BY_SET_ID[feature_set_id]


def explain_horizon(
    *,
    horizon_bars: int,
    feature_set_id: str,
    feature_names: tuple[str, ...],
    scored_folds: Sequence[ScoredFold],
    predict_row: FeatureRow | None,
    seed: int,
) -> ForecastExplanation:
    """Compute one horizon's out-of-sample explanation from the walk-forward's
    captured scored folds.

    Per fold: seeded permutation importance (``random_state = seed +
    fold_index`` — deterministic per fold, per call) of the fold's own model on
    the fold's own test slice, single-threaded. Across folds: per-feature mean
    and population-std spread, ranked mean-descending with the feature name as
    the tie-break. With no scored folds the explanation is honest and empty
    (``note`` set) — never a fabricated ranking.
    """

    per_fold_means: list[list[float]] = []
    for fold in scored_folds:
        x = [list(row.values) for row in fold.test_rows]
        y = [label.value for label in fold.test_labels]
        with threadpoolctl.threadpool_limits(limits=1):
            outcome = permutation_importance(
                fold.model.classifier,
                x,
                y,
                n_repeats=N_PERMUTATION_REPEATS,
                random_state=seed + fold.fold_index,
            )
        per_fold_means.append([float(value) for value in outcome.importances_mean])

    importances: tuple[FeatureImportance, ...] = ()
    if per_fold_means:
        per_feature = list(zip(*per_fold_means, strict=True))
        assert len(per_feature) == len(feature_names)  # matrix column order is the frozen order
        importances = tuple(
            sorted(
                (
                    FeatureImportance(
                        feature=name,
                        mean=fmean(values),
                        spread=pstdev(values) if len(values) > 1 else 0.0,
                    )
                    for name, values in zip(feature_names, per_feature, strict=True)
                ),
                key=lambda fi: (-fi.mean, fi.feature),
            )
        )

    predict_map: dict[str, float] | None = None
    if predict_row is not None:
        predict_map = {
            name: float(value)
            for name, value in zip(feature_names, predict_row.values, strict=True)
        }

    return ForecastExplanation(
        horizon_bars=horizon_bars,
        feature_set_id=feature_set_id,
        n_folds_used=len(per_fold_means),
        importances=importances,
        predict_row=predict_map,
        note=None if per_fold_means else NOTE_NO_SCORED_FOLDS,
    )


def summarize_explanation(
    explanation: ForecastExplanation, *, artifact: str | None
) -> ExplanationSummary:
    """The compact wire summary: the top `TOP_N_DRIVERS` of the (already
    ordered) full ranking as ``(feature, importance)`` pairs, plus the
    artifact's ``runs_dir``-relative path (``None`` when no ``runs_dir`` is
    wired — the drivers still ride the wire)."""

    return ExplanationSummary(
        top_drivers=tuple(
            ExplanationDriver(feature=fi.feature, importance=fi.mean)
            for fi in explanation.importances[:TOP_N_DRIVERS]
        ),
        artifact=artifact,
    )


def build_forecast_explanation_artifact(
    result: MultiHorizonForecastResult,
    explanations: Sequence[ForecastExplanation],
    *,
    started_at: datetime,
) -> ForecastExplanationArtifact:
    """Assemble the persisted artifact from the call's result and its
    per-horizon explanations (one per block, in block order). ``series_inputs``
    is call-level (every block shares the selected tier's series), read from
    the first block that carries provenance — empty when no block trained."""

    if len(explanations) != len(result.horizons):
        raise ValueError(
            f"explanations ({len(explanations)}) must align to horizons ({len(result.horizons)})"
        )
    series_inputs: tuple[SeriesInput, ...] = next(
        (
            block.provenance.series_inputs
            for block in result.horizons
            if block.provenance is not None
        ),
        (),
    )
    return ForecastExplanationArtifact(
        symbol=result.symbol,
        timeframe=result.timeframe,
        as_of_bar_ts=result.as_of_bar_ts,
        feature_set_id=result.feature_set_id,
        started_at=started_at,
        series_inputs=series_inputs,
        horizons=tuple(
            HorizonExplanationRecord(
                horizon_bars=block.horizon_bars,
                explanation=explanation,
                validation=block.validation,
                provenance=block.provenance,
            )
            for block, explanation in zip(result.horizons, explanations, strict=True)
        ),
    )


__all__ = [
    "IMPORTANCE_DISCLAIMER",
    "NOTE_NO_SCORED_FOLDS",
    "N_PERMUTATION_REPEATS",
    "TOP_N_DRIVERS",
    "FeatureImportance",
    "ForecastExplanation",
    "ForecastExplanationArtifact",
    "HorizonExplanationRecord",
    "build_forecast_explanation_artifact",
    "explain_horizon",
    "feature_names_for_set",
    "summarize_explanation",
]
