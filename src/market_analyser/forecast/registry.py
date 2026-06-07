"""Versioned model registry (Plan 0036 phase 4, ADR-0040 §3).

`compute_model_version` hashes the **prediction-affecting inputs** of a trained
model into a stable `model_version`; `save_model` / `load_model` / `model_exists`
persist and recover the fitted estimator under a gitignored `models/` root
(sibling to `runs/`). The contract ADR-0040 pins:

* The same inputs always hash to the same `model_version`; any change to the
  feature set, a hyperparameter, the seed, the training-window cutoff, or a pinned
  library version produces a new one. The hash inputs are exactly those five —
  audited against *everything that affects predictions*, the ADR's named failure
  mode being an omitted input that lets two genuinely-different models collide.
* Library versions in the hash are the **prediction-affecting** ones only —
  scikit-learn (`model.model_lib_versions()`). statsmodels is a declared dep for a
  future classical baseline but no prediction path uses it, so including it would
  rotate `model_version` on a bump that cannot change any forecast.

Artifacts are **data, not credentials** (ADR-0040): a joblib-dumped estimator
(`model.joblib`) plus a human-readable provenance sidecar (`meta.json`).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib

from market_analyser.forecast.labels import Direction
from market_analyser.forecast.model import ModelParams, TrainedModel

# The model class is itself a prediction-affecting input (a different estimator on
# the same features/seed predicts differently), so it is part of the hash.
MODEL_CLASS = "HistGradientBoostingClassifier"


def compute_model_version(
    *,
    feature_set_id: str,
    model_params: ModelParams,
    training_cutoff: datetime,
    lib_versions: dict[str, str],
) -> str:
    """A deterministic 16-hex-char hash over every prediction-affecting input:
    feature-set id, model class + hyperparameters (incl. seed), training-window
    cutoff, and library versions. ``sort_keys`` makes the JSON canonical so the
    hash never depends on dict ordering."""

    payload: dict[str, Any] = {
        "feature_set_id": feature_set_id,
        "model_class": MODEL_CLASS,
        "hyperparameters": model_params.model_dump(mode="json"),
        "training_cutoff": training_cutoff.isoformat(),
        "lib_versions": lib_versions,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _model_dir(root: Path, model_version: str) -> Path:
    return root / model_version


def model_exists(model_version: str, *, root: Path) -> bool:
    """True when both artifact files for ``model_version`` are present under ``root``."""

    dest = _model_dir(root, model_version)
    return (dest / "model.joblib").is_file() and (dest / "meta.json").is_file()


def save_model(
    model: TrainedModel,
    *,
    model_version: str,
    lib_versions: dict[str, str],
    root: Path,
) -> Path:
    """Persist ``model`` under ``root/<model_version>/`` as a joblib estimator plus a
    JSON provenance sidecar. Idempotent: re-saving the same ``model_version``
    rewrites identical content. Returns the artifact directory."""

    cutoff = model.training_cutoff
    assert isinstance(cutoff, datetime)  # set from a bar event_ts in model.train

    dest = _model_dir(root, model_version)
    dest.mkdir(parents=True, exist_ok=True)
    joblib.dump(model.classifier, dest / "model.joblib")
    meta = {
        "model_version": model_version,
        "feature_set_id": model.feature_set_id,
        "model_class": MODEL_CLASS,
        "classes": [c.value for c in model.classes],
        "hyperparameters": model.params.model_dump(mode="json"),
        "n_samples": model.n_samples,
        "training_cutoff": cutoff.isoformat(),
        "lib_versions": lib_versions,
    }
    (dest / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    return dest


def load_model(model_version: str, *, root: Path) -> TrainedModel:
    """Reconstruct a `TrainedModel` from its persisted artifacts. Raises
    `FileNotFoundError` (via the underlying reads) if the artifact is absent."""

    src = _model_dir(root, model_version)
    classifier = joblib.load(src / "model.joblib")
    meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    classes = tuple(Direction(c) for c in meta["classes"])
    params = ModelParams.model_validate(meta["hyperparameters"])
    return TrainedModel(
        clf=classifier,
        classes=classes,
        params=params,
        feature_set_id=meta["feature_set_id"],
        n_samples=meta["n_samples"],
        training_cutoff=datetime.fromisoformat(meta["training_cutoff"]),
    )


__all__ = [
    "MODEL_CLASS",
    "compute_model_version",
    "load_model",
    "model_exists",
    "save_model",
]
