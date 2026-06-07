"""Phase-2 done-when for Plan 0036 (part 1): causal labels + model training.

The load-bearing test here is **no label leakage**: the label at bar ``i`` looks
*forward* to construct its target, but that forward look must never reach the
feature row at ``i``. The test perturbs the future bar the label reads and asserts
the label at ``i`` moves while the feature row at ``i`` stays byte-identical —
the structural proof that the (forward-looking) target is not bleeding into the
(strictly causal) feature matrix. Plus: training aligns samples correctly and
produces well-formed probability distributions.

The determinism golden test lives in ``test_determinism.py``.
"""

from __future__ import annotations

from market_analyser.forecast.features import FEATURE_NAMES, build_feature_rows
from market_analyser.forecast.labels import Direction, LabelParams, build_labels
from market_analyser.forecast.model import (
    ModelParams,
    align_samples,
    predict_proba,
    train,
)
from tests.forecast._synthetic import synthetic_bars, with_close


def test_label_looks_forward_but_does_not_leak_into_features() -> None:
    """Perturbing the bar the label at ``i`` reads (``i + horizon``) changes the
    label at ``i`` but leaves the feature row at ``i`` untouched — the no-leakage
    guard (ADR-0030 invariant 1)."""

    bars = synthetic_bars(120)
    i = 80
    full_rows = build_feature_rows(bars)
    full_labels = build_labels(bars, LabelParams(horizon_bars=1))
    assert full_rows[i] is not None
    assert full_labels[i] is not None

    base_close = bars[i].close
    up_bars = list(bars)
    up_bars[i + 1] = with_close(bars[i + 1], base_close * 1.05)  # +5% -> UP
    down_bars = list(bars)
    down_bars[i + 1] = with_close(bars[i + 1], base_close * 0.95)  # -5% -> DOWN

    up_rows = build_feature_rows(up_bars)
    down_rows = build_feature_rows(down_bars)
    up_labels = build_labels(up_bars, LabelParams(horizon_bars=1))
    down_labels = build_labels(down_bars, LabelParams(horizon_bars=1))

    # The label at i responds to the future bar — it looks forward.
    assert up_labels[i] is Direction.UP
    assert down_labels[i] is Direction.DOWN
    assert up_labels[i] != down_labels[i]

    # The feature row at i does NOT — changing bar i+1 cannot reach it.
    full_row = full_rows[i]
    up_row = up_rows[i]
    down_row = down_rows[i]
    assert full_row is not None and up_row is not None and down_row is not None
    assert up_row.values == full_row.values
    assert down_row.values == full_row.values


def test_label_values_are_not_feature_columns() -> None:
    """A second, structural guard: no label class name is a feature column."""

    assert {d.value for d in Direction}.isdisjoint(FEATURE_NAMES)


def test_align_samples_drops_warmup_and_trailing_horizon() -> None:
    bars = synthetic_bars(150)
    horizon = 3
    rows = build_feature_rows(bars)
    labels = build_labels(bars, LabelParams(horizon_bars=horizon))

    kept_rows, kept_labels = align_samples(rows, labels)
    assert len(kept_rows) == len(kept_labels)

    expected = sum(
        1 for r, lab in zip(rows, labels, strict=True) if r is not None and lab is not None
    )
    assert len(kept_rows) == expected

    # Trailing `horizon` bars have no label, so they are excluded.
    assert kept_rows[-1].bar_index <= len(bars) - 1 - horizon
    # Leading warm-up bars have no features, so the first kept index is past them.
    assert kept_rows[0].bar_index > 0


def test_train_and_predict_yields_valid_distributions() -> None:
    bars = synthetic_bars(150)
    rows = build_feature_rows(bars)
    labels = build_labels(bars, LabelParams(horizon_bars=1))
    kept_rows, kept_labels = align_samples(rows, labels)

    model = train(kept_rows, kept_labels, ModelParams(seed=1729))
    assert model.n_samples == len(kept_rows)
    assert model.training_cutoff == kept_rows[-1].event_ts
    assert len(model.classes) >= 2

    probs = predict_proba(model, kept_rows[:5])
    assert len(probs) == 5
    for dist in probs:
        assert set(dist.keys()) == set(Direction)
        assert abs(sum(dist.values()) - 1.0) < 1e-9
        assert all(0.0 <= v <= 1.0 for v in dist.values())
