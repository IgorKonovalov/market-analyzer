"""Phase-2 done-when for Plan 0036 (part 2): the golden determinism test.

ADR-0040's determinism contract, mirroring ADR-0018's backtest golden test:
training twice from the same samples + seed produces **byte-identical** predicted
probabilities. This is the regression guard for the determinism mechanism —
seeded ``random_state`` + single-thread training + frozen feature order. If a
future change reintroduces thread-count-dependent reduction order or unseeded
RNG, this test fails.
"""

from __future__ import annotations

import struct

from market_analyser.forecast.features import build_feature_rows
from market_analyser.forecast.labels import Direction, LabelParams, build_labels
from market_analyser.forecast.model import ModelParams, align_samples, predict_proba, train
from tests.forecast._synthetic import synthetic_bars


def _proba_bytes(dists: list[dict[Direction, float]]) -> bytes:
    """Pack every probability (in a fixed key order) into raw bytes, so equality is
    literally byte-for-byte rather than float ``==`` (which would silently pass on
    NaN-vs-NaN)."""

    out = bytearray()
    for dist in dists:
        for key in sorted(dist, key=lambda d: d.value):
            out += struct.pack("<d", dist[key])
    return bytes(out)


def test_predicted_probabilities_are_byte_identical_across_retrains() -> None:
    bars = synthetic_bars(150)
    rows = build_feature_rows(bars)
    labels = build_labels(bars, LabelParams(horizon_bars=1))
    kept_rows, kept_labels = align_samples(rows, labels)

    model_a = train(kept_rows, kept_labels, ModelParams(seed=1729))
    model_b = train(kept_rows, kept_labels, ModelParams(seed=1729))

    proba_a = predict_proba(model_a, kept_rows)
    proba_b = predict_proba(model_b, kept_rows)

    assert _proba_bytes(proba_a) == _proba_bytes(proba_b)


def test_predict_is_stable_across_repeated_calls() -> None:
    bars = synthetic_bars(150)
    rows = build_feature_rows(bars)
    labels = build_labels(bars, LabelParams(horizon_bars=1))
    kept_rows, kept_labels = align_samples(rows, labels)

    model = train(kept_rows, kept_labels, ModelParams(seed=1729))
    assert _proba_bytes(predict_proba(model, kept_rows)) == _proba_bytes(
        predict_proba(model, kept_rows)
    )
