"""Phase-2 done-when for Plan 0077: the regime-transition forecast kind (ADR-0070).

The load-bearing tests: the current-regime label is **trailing** (truncation-invariant,
no lookahead); its **trend component equals `_classify_trend`** for the same bar (the
reuse is pinned, not a second trend definition); the forecast returns the current regime
plus a next-period probability distribution with a Brier-vs-persistence verdict; an
unpredictable (iid) fixture yields no edge over persistence; and two runs are byte-identical.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.analysis import indicators as ind
from market_analyser.analysis.snapshot import _classify_trend
from market_analyser.data.types import Bar
from market_analyser.forecast.regime import (
    _TREND_PREFIX as TREND_PREFIX,
)
from market_analyser.forecast.regime import (
    ADX_PERIOD,
    RegimeState,
    RegimeValidationError,
    VolState,
    build_regime_labels,
    forecast_regime,
    validate_regime,
)
from tests.forecast._synthetic import synthetic_bars


def _noise_bars(n: int, seed: int) -> list[Bar]:
    """An iid geometric random walk: next-period regime is not predictable from the past,
    so the honest verdict is 'no edge over persistence'. Deterministic."""

    rng = random.Random(seed)
    start_ts = datetime(2025, 1, 1, tzinfo=UTC)
    price = 100.0
    bars: list[Bar] = []
    for i in range(n):
        price = max(1.0, price * math.exp(rng.gauss(0.0, 0.02)))
        open_ = price
        close = max(1.0, price * math.exp(rng.gauss(0.0, 0.015)))
        high = max(open_, close) * (1.0 + abs(rng.gauss(0.0, 0.004)))
        low = min(open_, close) * (1.0 - abs(rng.gauss(0.0, 0.004)))
        bars.append(
            Bar(
                symbol="NOISE",
                timeframe="1d",
                event_ts=start_ts + timedelta(days=i),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=1_000_000.0 + rng.random() * 100_000.0,
                source="synthetic",
            )
        )
    return bars


def _first_defined_index(labels: list[RegimeState | None]) -> int:
    for i, lab in enumerate(labels):
        if lab is not None:
            return i
    raise AssertionError("no defined regime label in fixture")


def test_regime_label_is_trailing() -> None:
    """Truncating the series at bar ``i`` and rebuilding leaves the regime label at ``i``
    byte-identical — every input (trend, ATR%, the percentile window) is trailing."""

    bars = synthetic_bars(160)
    full = build_regime_labels(bars)
    for i in (100, 120, 140):
        assert full[i] is not None
        assert build_regime_labels(bars[: i + 1])[i] == full[i]


def test_regime_trend_component_equals_classify_trend() -> None:
    """The trend half of the regime is exactly ``_classify_trend`` (the reused snapshot
    classifier), not a re-derivation — pinned so a drift between the two would fail."""

    bars = synthetic_bars(160)
    closes = [b.close for b in bars]
    adx = ind.adx(bars, ADX_PERIOD)
    regimes = build_regime_labels(bars)

    checked = 0
    for i in range(_first_defined_index(regimes), len(bars)):
        regime = regimes[i]
        if regime is None:
            continue
        adx_i = adx[i]
        adx_val = adx_i.adx if adx_i is not None else None
        expected = _classify_trend(closes[: i + 1], adx_val)
        assert regime.value.startswith(f"{TREND_PREFIX[expected]}_")
        checked += 1
    assert checked > 0


def test_forecast_returns_current_regime_and_transition_distribution() -> None:
    """The forecast surfaces the current regime and a full probability distribution over
    next-period regimes, with a Brier-vs-persistence verdict."""

    bars = synthetic_bars(200)
    f = forecast_regime(bars, symbol="SYN", timeframe="1d", horizon_bars=5, n_splits=4)

    assert isinstance(f.current_regime, RegimeState)
    assert f.transition_probs is not None
    assert set(f.transition_probs) == set(RegimeState)
    assert sum(f.transition_probs.values()) == pytest.approx(1.0)
    assert isinstance(f.beats_baseline, bool)
    assert f.validation.model_brier is not None
    assert f.validation.persistence_brier is not None
    assert f.provenance is not None
    assert f.as_of_bar_ts == bars[-1].event_ts


def test_taxonomy_is_the_full_trend_by_vol_product() -> None:
    """Sanity on the taxonomy: 3 trend states x 2 vol states = the 6 regimes."""

    assert len(RegimeState) == 6
    assert len(VolState) == 2


def test_unpredictable_fixture_reports_no_edge() -> None:
    """An iid series has no forecastable regime transitions, so the classifier must not
    beat persistence out-of-sample — reported honestly, not raised."""

    bars = _noise_bars(220, seed=11)
    v = validate_regime(bars, horizon_bars=5, n_splits=4)
    assert v.beats_baseline is False
    if v.n_scored > 0:
        assert v.model_brier is not None
        assert v.persistence_brier is not None


def test_two_runs_are_byte_identical() -> None:
    """Determinism (ADR-0040): the same bars produce an identical regime forecast."""

    bars = synthetic_bars(200)
    a = forecast_regime(bars, symbol="SYN", timeframe="1d", horizon_bars=5, n_splits=4)
    b = forecast_regime(bars, symbol="SYN", timeframe="1d", horizon_bars=5, n_splits=4)
    assert a.model_dump() == b.model_dump()


def test_invalid_n_splits_raises() -> None:
    bars = synthetic_bars(60)
    with pytest.raises(RegimeValidationError):
        validate_regime(bars, horizon_bars=5, n_splits=1)
    with pytest.raises(RegimeValidationError):
        validate_regime(bars, horizon_bars=5, n_splits=len(bars) + 1)


def test_too_short_series_is_honest_not_error() -> None:
    """A series too short for the regime axis to become defined yields an honest empty
    verdict rather than raising."""

    bars = synthetic_bars(15)
    f = forecast_regime(bars, symbol="SYN", timeframe="1d", horizon_bars=5, n_splits=2)
    assert f.validation.n_scored == 0
    assert f.beats_baseline is False
    assert f.transition_probs is None
    assert f.provenance is None
