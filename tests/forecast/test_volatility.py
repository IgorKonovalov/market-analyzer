"""Phase-1 done-when for Plan 0077: the volatility forecast kind (ADR-0070).

The load-bearing tests are the anti-lookahead guarantee and the honest gate:

* the realised-vol **label looks forward** (its whole point) while the feature row and
  both baselines read only the past, so a future bar can never change a past prediction;
* a no-edge fixture (iid constant-vol returns, where realised vol is unpredictable
  noise) is reported as ``beats_baseline=False`` with the QLIKE baselines surfaced, not
  raised as an error;
* the result carries a predicted vol + baseline value + gate verdict;
* two runs on the same fixture are byte-identical (the ADR-0040 determinism contract,
  carried to the regression kind).
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.data.types import Bar
from market_analyser.forecast.features import build_feature_rows
from market_analyser.forecast.volatility import (
    DEFAULT_EWMA_LAMBDA,
    VolatilityValidationError,
    build_volatility_labels,
    forecast_volatility,
    validate_volatility,
)
from market_analyser.forecast.volatility import (
    _ewma_vol_series as ewma_vol_series,
)
from market_analyser.forecast.volatility import (
    _log_returns as log_returns,
)
from market_analyser.forecast.volatility import (
    _persistence_vol as persistence_vol,
)
from tests.forecast._synthetic import synthetic_bars, with_close

HORIZON = 5


def _constant_vol_bars(n: int, seed: int) -> list[Bar]:
    """An iid constant-volatility geometric random walk: every bar's return is an
    independent draw from the *same* distribution, so realised volatility is stationary
    noise with no structure a feature could predict. The honest verdict is 'no edge over
    the baseline'. Deterministic via ``random.Random``."""

    rng = random.Random(seed)
    start_ts = datetime(2025, 1, 1, tzinfo=UTC)
    price = 100.0
    bars: list[Bar] = []
    for i in range(n):
        ret = rng.gauss(0.0, 0.02)
        price = max(1.0, price * math.exp(ret))
        open_ = price
        close = max(1.0, price * math.exp(rng.gauss(0.0, 0.02)))
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


def test_label_looks_forward_but_features_and_baselines_do_not() -> None:
    """Perturbing a *future* bar moves the realised-vol label at an earlier bar (the
    label spans that future window) but leaves the earlier bar's feature row and its
    causal baselines byte-identical — the no-lookahead guarantee."""

    bars = synthetic_bars(120)
    i = 80
    future = i + HORIZON  # inside label[i]'s forward window (returns i+1..i+HORIZON)
    perturbed = list(bars)
    perturbed[future] = with_close(bars[future], bars[future].close * 1.10)

    rows_orig = build_feature_rows(bars)
    rows_pert = build_feature_rows(perturbed)
    labels_orig = build_volatility_labels(bars, HORIZON)
    labels_pert = build_volatility_labels(perturbed, HORIZON)

    assert rows_orig[i] is not None
    assert rows_orig[i] == rows_pert[i]  # feature row is trailing — unchanged
    assert labels_orig[i] is not None
    assert labels_pert[i] is not None
    assert labels_orig[i] != labels_pert[i]  # label is forward — moved

    rets_orig = log_returns([b.close for b in bars])
    rets_pert = log_returns([b.close for b in perturbed])
    assert ewma_vol_series(rets_orig, DEFAULT_EWMA_LAMBDA)[i] == pytest.approx(
        ewma_vol_series(rets_pert, DEFAULT_EWMA_LAMBDA)[i]
    )
    assert persistence_vol(rets_orig, i, HORIZON) == pytest.approx(
        persistence_vol(rets_pert, i, HORIZON)
    )


def test_label_forward_window_is_exactly_horizon() -> None:
    """The label at ``i`` needs bars only up to ``i + HORIZON``: truncating the series
    right after that window leaves the label byte-identical (it peeks no further)."""

    bars = synthetic_bars(120)
    i = 80
    truncated = bars[: i + HORIZON + 1]
    assert (
        build_volatility_labels(bars, HORIZON)[i] == build_volatility_labels(truncated, HORIZON)[i]
    )


def test_no_edge_fixture_reports_no_edge_with_baselines_surfaced() -> None:
    """An iid constant-vol series has no forecastable volatility structure, so the model
    must not beat the QLIKE baselines out-of-sample — reported honestly, not raised."""

    bars = _constant_vol_bars(160, seed=7)
    v = validate_volatility(bars, horizon_bars=HORIZON, n_splits=4)

    assert v.n_scored > 0
    assert v.beats_baseline is False
    assert v.model_qlike is not None
    assert v.persistence_qlike is not None
    assert v.ewma_qlike is not None
    assert v.baseline_kind in ("persistence", "ewma")


def test_forecast_result_carries_prediction_baseline_and_verdict() -> None:
    """The forecast surfaces a positive predicted vol, a band bracketing it, the winning
    baseline's reading, and a boolean gate verdict backed by OOS QLIKE."""

    bars = synthetic_bars(160)
    f = forecast_volatility(bars, symbol="SYN", timeframe="1d", horizon_bars=HORIZON, n_splits=4)

    assert f.predicted_vol is not None and f.predicted_vol > 0.0
    assert f.band is not None
    assert f.band[0] <= f.predicted_vol <= f.band[1]
    assert f.baseline_vol is not None and f.baseline_vol > 0.0
    assert isinstance(f.beats_baseline, bool)
    assert f.validation.model_qlike is not None
    assert f.validation.baseline_qlike is not None
    assert f.provenance is not None
    assert f.as_of_bar_ts == bars[-1].event_ts


def test_two_runs_are_byte_identical() -> None:
    """Determinism (ADR-0040): the same bars produce an identical forecast, dump for
    dump — no wall-clock, seed, or thread-order dependence (VolatilityForecast carries no
    run-provenance fields, so the whole dump is compared)."""

    bars = synthetic_bars(150)
    a = forecast_volatility(bars, symbol="SYN", timeframe="1d", horizon_bars=HORIZON, n_splits=4)
    b = forecast_volatility(bars, symbol="SYN", timeframe="1d", horizon_bars=HORIZON, n_splits=4)
    assert a.model_dump() == b.model_dump()


def test_invalid_n_splits_raises() -> None:
    bars = synthetic_bars(60)
    with pytest.raises(VolatilityValidationError):
        validate_volatility(bars, horizon_bars=HORIZON, n_splits=1)
    with pytest.raises(VolatilityValidationError):
        validate_volatility(bars, horizon_bars=HORIZON, n_splits=len(bars) + 1)


def test_too_short_series_is_honest_not_error() -> None:
    """A series too short for any feature row to become defined yields an honest empty
    verdict (no scored folds, no edge, no model) rather than raising."""

    bars = synthetic_bars(12)
    f = forecast_volatility(bars, symbol="SYN", timeframe="1d", horizon_bars=HORIZON, n_splits=2)
    assert f.validation.n_scored == 0
    assert f.beats_baseline is False
    assert f.predicted_vol is None
    assert f.provenance is None
