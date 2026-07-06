"""Phase-4 done-when (registry half) for Plan 0036: model_version + persistence.

The `model_version` is the model-artifact analogue of ADR-0018's determinism
check: it is **stable** for identical prediction-affecting inputs and **changes**
when any one of them changes (feature set, a hyperparameter, the seed, the
training cutoff, a pinned lib version) — the property that makes "which model said
this" answerable and the determinism claim falsifiable (ADR-0040). Plus: a saved
model round-trips back to byte-identical predictions, and saving is idempotent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from market_analyser.forecast.features import FEATURE_SET_ID, FeatureRow, build_feature_rows
from market_analyser.forecast.labels import LabelParams, build_labels
from market_analyser.forecast.model import (
    DEFAULT_SEED,
    ModelParams,
    TrainedModel,
    align_samples,
    model_lib_versions,
    predict_proba,
    train,
)
from market_analyser.forecast.registry import (
    compute_model_version,
    load_model,
    model_exists,
    save_model,
)
from tests.forecast._synthetic import synthetic_bars

LABELS_1D = LabelParams(horizon_bars=1, flat_band=0.001)


def _trained_model(seed: int = DEFAULT_SEED) -> tuple[TrainedModel, list[FeatureRow]]:
    bars = synthetic_bars(200)
    rows = build_feature_rows(bars)
    labels = build_labels(bars, LabelParams(horizon_bars=1, flat_band=0.001))
    train_rows, train_labels = align_samples(rows, labels)
    return train(train_rows, train_labels, ModelParams(seed=seed)), train_rows


def _cutoff() -> datetime:
    return datetime(2025, 6, 1, tzinfo=UTC)


def test_model_version_is_stable_for_identical_inputs() -> None:
    lib = {"scikit-learn": "1.8.0"}
    params = ModelParams(seed=DEFAULT_SEED)
    cutoff = _cutoff()
    first = compute_model_version(
        feature_set_id=FEATURE_SET_ID,
        model_params=params,
        label_params=LABELS_1D,
        training_cutoff=cutoff,
        lib_versions=lib,
    )
    second = compute_model_version(
        feature_set_id=FEATURE_SET_ID,
        model_params=params,
        label_params=LABELS_1D,
        training_cutoff=cutoff,
        lib_versions=lib,
    )
    assert first == second


def test_model_version_changes_when_any_input_changes() -> None:
    lib = {"scikit-learn": "1.8.0"}
    base = compute_model_version(
        feature_set_id=FEATURE_SET_ID,
        model_params=ModelParams(seed=DEFAULT_SEED),
        label_params=LABELS_1D,
        training_cutoff=_cutoff(),
        lib_versions=lib,
    )
    # Each variant flips exactly one prediction-affecting input.
    variants = [
        compute_model_version(
            feature_set_id="different-feature-set",
            model_params=ModelParams(seed=DEFAULT_SEED),
            label_params=LABELS_1D,
            training_cutoff=_cutoff(),
            lib_versions=lib,
        ),
        compute_model_version(
            feature_set_id=FEATURE_SET_ID,
            model_params=ModelParams(seed=DEFAULT_SEED + 1),  # seed
            label_params=LABELS_1D,
            training_cutoff=_cutoff(),
            lib_versions=lib,
        ),
        compute_model_version(
            feature_set_id=FEATURE_SET_ID,
            model_params=ModelParams(seed=DEFAULT_SEED, max_iter=100),  # a hyperparameter
            label_params=LABELS_1D,
            training_cutoff=_cutoff(),
            lib_versions=lib,
        ),
        compute_model_version(
            feature_set_id=FEATURE_SET_ID,
            model_params=ModelParams(seed=DEFAULT_SEED),
            label_params=LabelParams(horizon_bars=21, flat_band=0.001),  # horizon (Plan 0059)
            training_cutoff=_cutoff(),
            lib_versions=lib,
        ),
        compute_model_version(
            feature_set_id=FEATURE_SET_ID,
            model_params=ModelParams(seed=DEFAULT_SEED),
            label_params=LabelParams(horizon_bars=1, flat_band=0.002),  # flat band (Plan 0059)
            training_cutoff=_cutoff(),
            lib_versions=lib,
        ),
        compute_model_version(
            feature_set_id=FEATURE_SET_ID,
            model_params=ModelParams(seed=DEFAULT_SEED),
            label_params=LABELS_1D,
            training_cutoff=_cutoff() + timedelta(days=1),  # training window
            lib_versions=lib,
        ),
        compute_model_version(
            feature_set_id=FEATURE_SET_ID,
            model_params=ModelParams(seed=DEFAULT_SEED),
            label_params=LABELS_1D,
            training_cutoff=_cutoff(),
            lib_versions={"scikit-learn": "9.9.9"},  # pinned lib
        ),
    ]
    for variant in variants:
        assert variant != base
    assert len(set(variants)) == len(variants)  # and all distinct from each other


def test_save_load_roundtrip_predicts_identically(tmp_path: Path) -> None:
    model, train_rows = _trained_model()
    lib = model_lib_versions()
    cutoff = model.training_cutoff
    assert isinstance(cutoff, datetime)
    model_version = compute_model_version(
        feature_set_id=FEATURE_SET_ID,
        model_params=model.params,
        label_params=LABELS_1D,
        training_cutoff=cutoff,
        lib_versions=lib,
    )

    assert not model_exists(model_version, root=tmp_path)
    save_model(model, model_version=model_version, lib_versions=lib, root=tmp_path)
    assert model_exists(model_version, root=tmp_path)

    loaded = load_model(model_version, root=tmp_path)
    assert loaded.classes == model.classes
    assert loaded.feature_set_id == model.feature_set_id
    assert predict_proba(loaded, train_rows[:20]) == predict_proba(model, train_rows[:20])


def test_save_is_idempotent(tmp_path: Path) -> None:
    model, _ = _trained_model()
    lib = model_lib_versions()
    cutoff = model.training_cutoff
    assert isinstance(cutoff, datetime)
    model_version = compute_model_version(
        feature_set_id=FEATURE_SET_ID,
        model_params=model.params,
        label_params=LABELS_1D,
        training_cutoff=cutoff,
        lib_versions=lib,
    )
    dest1 = save_model(model, model_version=model_version, lib_versions=lib, root=tmp_path)
    meta_first = (dest1 / "meta.json").read_text(encoding="utf-8")
    dest2 = save_model(model, model_version=model_version, lib_versions=lib, root=tmp_path)
    assert dest1 == dest2
    assert (dest2 / "meta.json").read_text(encoding="utf-8") == meta_first
