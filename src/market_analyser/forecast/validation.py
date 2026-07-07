"""Walk-forward validation + baseline-beating gate (Plan 0036 phase 3).

ADR-0030 invariant 3: *validated edge or nothing*. A forecast model is accepted
only if it beats a naive baseline **out-of-sample**; one that does not is reported
as "no edge over baseline", not shipped as a forecast.

`validate(bars, ...)` runs an **expanding-window** walk-forward over the
contiguous fold partition produced by `backtest.walk_forward.fold_bounds` (the
reused ADR-0024 machinery — one source of truth for "how a bar series is split").
Fold *k*'s model is trained on bars strictly *before* fold *k*'s test window and
scored only on that test window, so no future bar ever informs an earlier fold —
the same anti-lookahead grain the strategy walk-forward holds. Because the label
is forward-looking (``label[i]`` reads ``close[i + horizon]``), the train window is
additionally **purged** by ``horizon`` bars: a fold whose test window starts at
``start`` trains only on samples ``i`` with ``i + horizon < start``, so no training
*label* peeks into the test window either. Without the purge the last ``horizon``
training labels would read test-window closes — a subtle leak the contiguous
feature partition alone does not close. Fold 0 (and any fold whose purged train
window is empty) has nothing to train on and is the training seed; out-of-sample
scoring pools the remaining folds.

**Directional skill** is out-of-sample accuracy: the fraction of test bars whose
predicted argmax direction matches the realised label. It is reported per fold and
pooled across folds. Two naive baselines are computed on the *same* test bars and
reported **alongside** the model, never hidden:

* **persistence** — predict the next move equals the last realised ``horizon``-bar
  move (``sign(close[i] / close[i-horizon] - 1)`` against the same flat band);
  known at decision time, so anti-lookahead.
* **majority-class** — predict the most frequent label in the training window.

The gate is ``beats_baseline = model_skill > max(persistence, majority)``. Pure and
deterministic: every per-fold train is seeded and single-threaded (via
``model.train``), and all tie-breaks are explicit, so identical inputs produce an
identical `ForecastValidation`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from market_analyser.backtest.walk_forward import fold_bounds
from market_analyser.data.types import Bar
from market_analyser.forecast.features import FeatureRow, build_feature_rows
from market_analyser.forecast.labels import Direction, LabelParams, build_labels
from market_analyser.forecast.model import (
    ModelParams,
    TrainedModel,
    align_samples,
    predict_proba,
    train,
)


class ForecastValidationError(ValueError):
    """Raised when the validation configuration is invalid for the bar series
    (e.g. fewer than two folds, or more folds than bars)."""


@dataclass(frozen=True)
class ScoredFold:
    """One walk-forward fold that actually trained and scored, captured for
    downstream out-of-sample analysis (Plan 0063, ADR-0058: permutation
    importances are computed on exactly these fold models over exactly these
    test slices — never on a final full-data fit). Not a pydantic model: it
    holds the live fitted estimator. Capturing happens inside the walk-forward
    itself, so no fold is ever re-trained to be explained."""

    fold_index: int
    model: TrainedModel
    test_rows: list[FeatureRow]
    test_labels: list[Direction]


class FoldSkill(BaseModel):
    """Out-of-sample directional skill for one scored fold. ``None`` skills mark an
    unscored fold (no trainable history yet, or no scorable test sample)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fold_index: int
    n_test: int
    model_skill: float | None
    persistence_skill: float | None
    majority_skill: float | None


class ForecastValidation(BaseModel):
    """The verdict. ``skill`` is pooled out-of-sample model accuracy; ``baseline_skill``
    is the stronger of the two naive baselines on the same bars; ``beats_baseline``
    is the gate (``False`` ⇒ no-edge verdict, no probability should be shipped).
    Per-fold detail and both baselines are kept so a marginal beat is visible as
    marginal (ADR-0030 invariant 4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_bars: int
    n_splits: int
    n_scored: int
    skill: float | None
    baseline_skill: float | None
    persistence_skill: float | None
    majority_skill: float | None
    beats_baseline: bool
    folds: list[FoldSkill]


def _classify_return(fractional_return: float, flat_band: float) -> Direction:
    """The same up/down/flat rule the label builder uses, factored out so the
    persistence baseline classifies a realised return identically to the target."""

    if fractional_return > flat_band:
        return Direction.UP
    if fractional_return < -flat_band:
        return Direction.DOWN
    return Direction.FLAT


def _argmax_direction(dist: dict[Direction, float]) -> Direction:
    """The model's predicted class: highest probability, ties broken deterministically
    by the `Direction` value order (so the prediction never depends on dict order)."""

    return min(Direction, key=lambda d: (-dist[d], d.value))


def _majority_class(labels: Sequence[Direction]) -> Direction:
    """The most frequent training label; ties broken by `Direction` value order."""

    counts = Counter(labels)
    return min(Direction, key=lambda d: (-counts[d], d.value))


def _accuracy(predicted: Sequence[Direction], actual: Sequence[Direction]) -> float | None:
    """Fraction of matches, or ``None`` when there is nothing to score."""

    if not actual:
        return None
    correct = sum(1 for p, a in zip(predicted, actual, strict=True) if p == a)
    return correct / len(actual)


def _persistence_predictions(
    test_rows: Sequence[FeatureRow], closes: Sequence[float], horizon: int, flat_band: float
) -> list[Direction]:
    """Persistence baseline per test row: the last realised ``horizon``-bar move,
    read from ``closes`` at the row's own bar index (only past data)."""

    preds: list[Direction] = []
    for row in test_rows:
        i = row.bar_index
        prev = closes[i - horizon]
        realised = closes[i] / prev - 1.0 if prev != 0.0 else 0.0
        preds.append(_classify_return(realised, flat_band))
    return preds


def validate(
    bars: Sequence[Bar],
    *,
    horizon_bars: int = 1,
    flat_band: float = 0.001,
    n_splits: int = 5,
    model_params: ModelParams | None = None,
    feature_rows: Sequence[FeatureRow | None] | None = None,
    scored_fold_sink: list[ScoredFold] | None = None,
) -> ForecastValidation:
    """Run expanding-window walk-forward validation and return the baseline-gated
    verdict.

    ``feature_rows`` optionally supplies a prebuilt bar-aligned feature matrix
    (Plan 0059: the v2 matrix is built once per call and shared across the
    horizon set — the exogenous as-of join is the expensive step). When omitted
    the v1 matrix is built from ``bars``, the Plan 0036 behaviour.

    ``scored_fold_sink`` (Plan 0063) optionally captures every fold that trained
    and scored — the fitted fold model plus its out-of-sample test slice — so
    explainability can measure the *validated* models without re-training them.
    Capture does not alter the verdict in any way.

    Raises `ForecastValidationError` when ``n_splits`` is invalid for the series
    (``< 2`` — at least one seed fold plus one scored fold are required — or larger
    than the bar count). An empty/too-short series is not an error: every fold is
    simply unscored and the verdict is an honest ``skill=None``, ``beats_baseline=False``.
    """

    if n_splits < 2:
        raise ForecastValidationError(f"n_splits must be >= 2, got {n_splits}")
    if n_splits > len(bars):
        raise ForecastValidationError(f"n_splits ({n_splits}) exceeds bar count ({len(bars)})")
    if feature_rows is not None and len(feature_rows) != len(bars):
        raise ForecastValidationError(
            f"feature_rows ({len(feature_rows)}) must align to bars ({len(bars)})"
        )

    params = model_params if model_params is not None else ModelParams()
    closes = [b.close for b in bars]
    rows = list(feature_rows) if feature_rows is not None else build_feature_rows(bars)
    labels = build_labels(bars, LabelParams(horizon_bars=horizon_bars, flat_band=flat_band))

    fold_results: list[FoldSkill] = []
    model_correct = persistence_correct = majority_correct = 0
    n_scored = 0

    for fold_index, (start, end) in enumerate(fold_bounds(len(bars), n_splits)):
        # Purge the trailing `horizon` training samples: label[i] reads
        # close[i + horizon], so any train sample with i + horizon >= start would
        # peek into this fold's test window. Training only on i < start - horizon
        # keeps the *label* (not just the feature row) strictly out-of-sample.
        train_cutoff = max(0, start - horizon_bars)
        train_rows, train_labels = align_samples(rows[:train_cutoff], labels[:train_cutoff])
        test_rows, test_labels = align_samples(rows[start:end], labels[start:end])

        trainable = len(train_rows) > 0 and len({lab for lab in train_labels}) >= 2
        if not trainable or not test_rows:
            fold_results.append(
                FoldSkill(
                    fold_index=fold_index,
                    n_test=len(test_rows),
                    model_skill=None,
                    persistence_skill=None,
                    majority_skill=None,
                )
            )
            continue

        model = train(train_rows, train_labels, params)
        if scored_fold_sink is not None:
            scored_fold_sink.append(
                ScoredFold(
                    fold_index=fold_index,
                    model=model,
                    test_rows=test_rows,
                    test_labels=test_labels,
                )
            )
        model_preds = [_argmax_direction(dist) for dist in predict_proba(model, test_rows)]
        persistence_preds = _persistence_predictions(test_rows, closes, horizon_bars, flat_band)
        majority = _majority_class(train_labels)
        majority_preds = [majority] * len(test_rows)

        fold_results.append(
            FoldSkill(
                fold_index=fold_index,
                n_test=len(test_rows),
                model_skill=_accuracy(model_preds, test_labels),
                persistence_skill=_accuracy(persistence_preds, test_labels),
                majority_skill=_accuracy(majority_preds, test_labels),
            )
        )
        model_correct += sum(1 for p, a in zip(model_preds, test_labels, strict=True) if p == a)
        persistence_correct += sum(
            1 for p, a in zip(persistence_preds, test_labels, strict=True) if p == a
        )
        majority_correct += sum(
            1 for p, a in zip(majority_preds, test_labels, strict=True) if p == a
        )
        n_scored += len(test_labels)

    skill = model_correct / n_scored if n_scored else None
    persistence_skill = persistence_correct / n_scored if n_scored else None
    majority_skill = majority_correct / n_scored if n_scored else None

    baseline_candidates = [s for s in (persistence_skill, majority_skill) if s is not None]
    baseline_skill = max(baseline_candidates) if baseline_candidates else None
    beats_baseline = skill is not None and baseline_skill is not None and skill > baseline_skill

    return ForecastValidation(
        horizon_bars=horizon_bars,
        n_splits=n_splits,
        n_scored=n_scored,
        skill=skill,
        baseline_skill=baseline_skill,
        persistence_skill=persistence_skill,
        majority_skill=majority_skill,
        beats_baseline=beats_baseline,
        folds=fold_results,
    )


__all__ = [
    "FoldSkill",
    "ForecastValidation",
    "ForecastValidationError",
    "ScoredFold",
    "validate",
]
