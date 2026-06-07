"""Deterministic direction model (Plan 0036 phase 2, ADR-0040).

Trains an sklearn ``HistGradientBoostingClassifier`` over the frozen feature
order (``features.FEATURE_NAMES``) to predict the ``Direction`` label, and
exposes calibrated per-class probabilities. ADR-0040's determinism mechanism is
enforced here: an explicit ``random_state``/seed, training pinned to a **single
thread** (``threadpoolctl.threadpool_limits(1)`` — no thread-count-dependent
float-reduction order), against the frozen feature order and the pinned library
versions. A model retrained from the same samples + seed produces byte-identical
predicted probabilities — the golden contract in ``tests/forecast/
test_determinism.py``, mirroring ADR-0018.

This module deliberately does **not** validate the model (that is the
walk-forward + baseline gate, phase 3, owned by ``backtester``) and does **not**
hash a ``model_version`` (phase 4). It exposes the prediction-affecting inputs —
params, seed, feature-set id, training cutoff — that those phases consume.
"""

from __future__ import annotations

import sklearn
import threadpoolctl
from pydantic import BaseModel, ConfigDict, Field
from sklearn.ensemble import HistGradientBoostingClassifier

from market_analyser.forecast.features import FEATURE_SET_ID, FeatureRow
from market_analyser.forecast.labels import Direction

# A fixed default seed so an unparameterised train is still reproducible. Not
# wall-clock, not random — a constant the determinism test can rely on.
DEFAULT_SEED = 1729


class ModelParams(BaseModel):
    """Hyperparameters of the direction classifier. Every field is
    prediction-affecting and flows into the model-version hash (phase 4); the
    defaults are deterministic (``early_stopping`` off avoids the internal
    validation-split randomness)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seed: int = DEFAULT_SEED
    max_iter: int = Field(default=200, ge=1)
    learning_rate: float = Field(default=0.1, gt=0.0)
    max_leaf_nodes: int = Field(default=31, ge=2)
    max_depth: int | None = Field(default=None, ge=1)
    min_samples_leaf: int = Field(default=20, ge=1)
    l2_regularization: float = Field(default=0.0, ge=0.0)


class TrainedModel:
    """A fitted classifier plus the metadata that identifies what produced it.

    Not a pydantic model — it wraps the live sklearn estimator. ``classes`` is the
    classifier's class order (the column order of ``predict_proba``); ``params``,
    ``feature_set_id`` and ``seed`` are the prediction-affecting inputs phase 4
    hashes into ``model_version``; ``n_samples`` and ``training_cutoff`` record the
    causal training boundary (the last bar the model saw)."""

    __slots__ = ("_clf", "classes", "feature_set_id", "n_samples", "params", "training_cutoff")

    def __init__(
        self,
        clf: HistGradientBoostingClassifier,
        classes: tuple[Direction, ...],
        params: ModelParams,
        feature_set_id: str,
        n_samples: int,
        training_cutoff: object,
    ) -> None:
        self._clf = clf
        self.classes = classes
        self.params = params
        self.feature_set_id = feature_set_id
        self.n_samples = n_samples
        self.training_cutoff = training_cutoff

    @property
    def classifier(self) -> HistGradientBoostingClassifier:
        """The fitted estimator — exposed read-only so the registry (phase 4) can
        persist it without reaching into the private slot."""

        return self._clf


def align_samples(
    rows: list[FeatureRow | None], labels: list[Direction | None]
) -> tuple[list[FeatureRow], list[Direction]]:
    """Pair feature rows with labels into a training set, keeping only indices where
    **both** are defined. The leading warm-up bars (no features) and the trailing
    horizon bars (no label) drop out; the survivors are the causal samples."""

    if len(rows) != len(labels):
        raise ValueError(f"rows ({len(rows)}) and labels ({len(labels)}) length mismatch")
    kept_rows: list[FeatureRow] = []
    kept_labels: list[Direction] = []
    for row, label in zip(rows, labels, strict=True):
        if row is None or label is None:
            continue
        kept_rows.append(row)
        kept_labels.append(label)
    return kept_rows, kept_labels


def _matrix(rows: list[FeatureRow]) -> list[list[float]]:
    return [list(row.values) for row in rows]


def train(
    rows: list[FeatureRow], labels: list[Direction], params: ModelParams | None = None
) -> TrainedModel:
    """Fit the classifier on already-aligned ``(rows, labels)`` (see
    `align_samples`). Single-threaded and seeded for byte-identical reruns.

    Raises ``ValueError`` if there are no samples or fewer than two label classes
    (a classifier needs at least two outcomes to predict between)."""

    p = params if params is not None else ModelParams()
    if not rows:
        raise ValueError("cannot train on zero samples")
    if len(rows) != len(labels):
        raise ValueError(f"rows ({len(rows)}) and labels ({len(labels)}) length mismatch")
    distinct = sorted({label.value for label in labels})
    if len(distinct) < 2:
        raise ValueError(f"need >= 2 label classes to train, got {distinct}")

    clf = HistGradientBoostingClassifier(
        random_state=p.seed,
        max_iter=p.max_iter,
        learning_rate=p.learning_rate,
        max_leaf_nodes=p.max_leaf_nodes,
        max_depth=p.max_depth,
        min_samples_leaf=p.min_samples_leaf,
        l2_regularization=p.l2_regularization,
        early_stopping=False,
    )
    x = _matrix(rows)
    y = [label.value for label in labels]
    with threadpoolctl.threadpool_limits(limits=1):
        clf.fit(x, y)

    classes = tuple(Direction(c) for c in clf.classes_)
    return TrainedModel(
        clf=clf,
        classes=classes,
        params=p,
        feature_set_id=FEATURE_SET_ID,
        n_samples=len(rows),
        training_cutoff=rows[-1].event_ts,
    )


def predict_proba(model: TrainedModel, rows: list[FeatureRow]) -> list[dict[Direction, float]]:
    """Per-row calibrated class probabilities, one dict over the full `Direction`
    set per row. Classes the model never saw at fit time are filled with ``0.0`` so
    every dict has the same three keys regardless of the training class balance."""

    if not rows:
        return []
    x = _matrix(rows)
    with threadpoolctl.threadpool_limits(limits=1):
        proba = model._clf.predict_proba(x)
    out: list[dict[Direction, float]] = []
    for prob_row in proba:
        dist = {direction: 0.0 for direction in Direction}
        for cls, value in zip(model.classes, prob_row, strict=True):
            dist[cls] = float(value)
        out.append(dist)
    return out


def model_lib_versions() -> dict[str, str]:
    """The pinned library version(s) that affect predictions, for provenance. Only
    scikit-learn here — statsmodels powers the classical baseline (phase 3), not
    this estimator."""

    return {"scikit-learn": str(sklearn.__version__)}


__all__ = [
    "DEFAULT_SEED",
    "ModelParams",
    "TrainedModel",
    "align_samples",
    "model_lib_versions",
    "predict_proba",
    "train",
]
