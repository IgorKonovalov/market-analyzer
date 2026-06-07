"""Phase-3 done-when for Plan 0036: walk-forward validation + baseline gate.

The load-bearing test is the gate itself: a deliberately overfit-prone fixture (an
iid seeded random walk, where the future direction is independent of every feature)
must be **reported as "no edge over baseline"** — the model may fit in-sample noise
but its out-of-sample skill does not beat the naive baselines, so ``beats_baseline``
is ``False`` (ADR-0030 invariant 3). Supporting tests: the baselines are reported
alongside the model (never hidden), the folds are contiguous and strictly
anti-lookahead (each train window precedes its test window), and the harness is
deterministic.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from market_analyser.backtest.walk_forward import fold_bounds
from market_analyser.data.types import Bar
from market_analyser.forecast.features import build_feature_rows
from market_analyser.forecast.labels import LabelParams, build_labels
from market_analyser.forecast.model import align_samples, predict_proba, train
from market_analyser.forecast.validation import (
    ForecastValidationError,
    validate,
)
from market_analyser.forecast.validation import (
    _argmax_direction as argmax_direction,
)
from tests.forecast._synthetic import synthetic_bars


def _noise_bars(n: int, seed: int) -> list[Bar]:
    """An iid seeded random walk: returns are independent draws, so by construction
    no feature (a function of the past) carries information about the next move. The
    honest verdict on this series is 'no edge'. Deterministic via ``random.Random``."""

    rng = random.Random(seed)
    start_ts = datetime(2025, 1, 1, tzinfo=UTC)
    price = 100.0
    bars: list[Bar] = []
    for i in range(n):
        price = max(1.0, price * math.exp(rng.gauss(0.0, 0.02)))
        open_ = price
        close = max(1.0, price * math.exp(rng.gauss(0.0, 0.006)))
        high = max(open_, close) * (1.0 + abs(rng.gauss(0.0, 0.004)))
        low = min(open_, close) * (1.0 - abs(rng.gauss(0.0, 0.004)))
        volume = 1_000_000.0 + rng.random() * 100_000.0
        bars.append(
            Bar(
                symbol="NOISE",
                timeframe="1d",
                event_ts=start_ts + timedelta(days=i),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                source="synthetic",
            )
        )
    return bars


def test_overfit_fixture_is_reported_as_no_edge() -> None:
    """The gate: on a no-signal series the model does not beat baseline OOS."""

    bars = _noise_bars(400, seed=12345)
    result = validate(bars, horizon_bars=1, flat_band=0.001, n_splits=5)

    assert result.skill is not None
    assert result.baseline_skill is not None
    assert result.beats_baseline is False
    assert result.skill <= result.baseline_skill

    # Evidence of the overfit gap: an in-sample fit scores above the model's
    # out-of-sample skill — it memorised noise it cannot reproduce on held-out bars.
    rows = build_feature_rows(bars)
    labels = build_labels(bars, LabelParams(horizon_bars=1, flat_band=0.001))
    train_rows, train_labels = align_samples(rows, labels)
    model = train(train_rows, train_labels)
    in_sample_preds = [argmax_direction(d) for d in predict_proba(model, train_rows)]
    in_sample_skill = sum(
        1 for p, a in zip(in_sample_preds, train_labels, strict=True) if p == a
    ) / len(train_labels)
    assert in_sample_skill > result.skill


def test_baselines_reported_alongside_model() -> None:
    bars = _noise_bars(400, seed=7)
    result = validate(bars, n_splits=5)

    assert result.persistence_skill is not None
    assert result.majority_skill is not None
    assert result.baseline_skill == max(result.persistence_skill, result.majority_skill)

    assert len(result.folds) == 5
    scored = [f for f in result.folds if f.model_skill is not None]
    assert scored, "expected at least one scored fold"
    for fold in scored:
        assert fold.persistence_skill is not None
        assert fold.majority_skill is not None
        assert fold.n_test > 0


def test_folds_are_contiguous_and_train_precedes_test() -> None:
    """Fold 0 is the training seed (no prior bars → unscored); the partition is
    contiguous and non-overlapping; and a scored fold's whole train window sits
    strictly before its test window (anti-lookahead)."""

    bars = synthetic_bars(400)
    result = validate(bars, horizon_bars=1, flat_band=0.001, n_splits=5)

    assert result.folds[0].model_skill is None  # seed fold has nothing to train on

    bounds = fold_bounds(len(bars), 5)
    assert bounds[0][0] == 0
    assert bounds[-1][1] == len(bars)
    for (_s0, e0), (s1, _e1) in pairwise(bounds):
        assert e0 == s1  # contiguous, non-overlapping

    # Direct anti-lookahead proof for the first scored fold.
    start, end = bounds[1]
    rows = build_feature_rows(bars)
    labels = build_labels(bars, LabelParams(horizon_bars=1, flat_band=0.001))
    train_rows, _ = align_samples(rows[:start], labels[:start])
    test_rows, _ = align_samples(rows[start:end], labels[start:end])
    assert train_rows and test_rows
    assert max(r.bar_index for r in train_rows) < min(r.bar_index for r in test_rows)


def test_validation_is_deterministic() -> None:
    bars = synthetic_bars(400)
    first = validate(bars, n_splits=5)
    second = validate(bars, n_splits=5)
    assert first == second
    assert first.model_dump() == second.model_dump()


def test_invalid_n_splits_raises() -> None:
    bars = synthetic_bars(80)
    with pytest.raises(ForecastValidationError):
        validate(bars, n_splits=1)
    with pytest.raises(ForecastValidationError):
        validate(bars, n_splits=len(bars) + 1)
