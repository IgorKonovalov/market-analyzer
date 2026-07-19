"""Plan 0042 phase 3 — behaviour tests for the conditional-probability engine.

Pins the phase-3 done-when (determinism lives in `test_risk_determinism.py`):
(a) liquidation probability behaves sensibly (bounds, edge cases, monotone in cushion /
    vol / horizon); (b) the vol fit is causal — it reads only the trailing samples passed
    in; (c) every result states its assumption (a bare probability is never returned);
(d) the IL distribution is well-formed (ordered quantiles, all <= 0); plus validation.
"""

from __future__ import annotations

import statistics

import pytest

from market_analyser.defi.risk import (
    fit_daily_vol,
    impermanent_loss_distribution,
    liquidation_probability,
)

# 90 trailing daily log-returns with population stdev exactly 0.05, zero mean.
_RETURNS = [0.05, -0.05] * 45
_LOW_VOL = [0.02, -0.02] * 45
_SEED = 42


# -- (a) liquidation probability behaviour -----------------------------------


def test_probability_is_within_unit_interval() -> None:
    result = liquidation_probability(
        liquidation_distance=0.5, log_returns=_RETURNS, horizon_days=30, seed=_SEED
    )
    assert 0.0 <= result.probability <= 1.0


def test_already_underwater_is_certain() -> None:
    # HF <= 1 → distance <= 0 → certain, without simulating.
    result = liquidation_probability(
        liquidation_distance=0.0, log_returns=_RETURNS, horizon_days=30, seed=_SEED
    )
    assert result.probability == 1.0


def test_full_wipe_barrier_is_unreachable() -> None:
    # A >= 100% collateral drop is unreachable under lognormal prices → 0.0.
    result = liquidation_probability(
        liquidation_distance=1.0, log_returns=_RETURNS, horizon_days=30, seed=_SEED
    )
    assert result.probability == 0.0


def test_closer_cushion_is_more_likely_to_liquidate() -> None:
    near = liquidation_probability(
        liquidation_distance=0.2, log_returns=_RETURNS, horizon_days=30, seed=_SEED
    ).probability
    far = liquidation_probability(
        liquidation_distance=0.6, log_returns=_RETURNS, horizon_days=30, seed=_SEED
    ).probability
    assert near > far


def test_higher_vol_is_more_likely_to_liquidate() -> None:
    high = liquidation_probability(
        liquidation_distance=0.4, log_returns=_RETURNS, horizon_days=30, seed=_SEED
    ).probability
    low = liquidation_probability(
        liquidation_distance=0.4, log_returns=_LOW_VOL, horizon_days=30, seed=_SEED
    ).probability
    assert high > low


def test_longer_horizon_is_more_likely_to_liquidate() -> None:
    long_h = liquidation_probability(
        liquidation_distance=0.4, log_returns=_RETURNS, horizon_days=60, seed=_SEED
    ).probability
    short_h = liquidation_probability(
        liquidation_distance=0.4, log_returns=_RETURNS, horizon_days=7, seed=_SEED
    ).probability
    assert long_h >= short_h


# -- (b) causal vol fit (only trailing data) ---------------------------------


def test_vol_fit_uses_only_the_supplied_trailing_samples() -> None:
    # The fitted vol is exactly the population stdev of the samples passed — nothing
    # else (no future price) can inform it.
    assert fit_daily_vol(_RETURNS) == pytest.approx(statistics.pstdev(_RETURNS))
    result = liquidation_probability(
        liquidation_distance=0.5, log_returns=_RETURNS, horizon_days=30, seed=_SEED
    )
    assert result.daily_vol == pytest.approx(statistics.pstdev(_RETURNS))
    # A shorter trailing window is a different fit — proving the fit is a function of the
    # supplied data alone, not of anything beyond it.
    assert fit_daily_vol(_RETURNS[:10]) == pytest.approx(statistics.pstdev(_RETURNS[:10]))


# -- (c) honest uncertainty: assumption always attached ----------------------


def test_probability_carries_its_assumption() -> None:
    result = liquidation_probability(
        liquidation_distance=0.5, log_returns=_RETURNS, horizon_days=30, seed=_SEED
    )
    # The result cannot exist without an assumption, and the assumption names the model.
    assert result.assumption
    assert "realized daily vol" in result.assumption
    assert "30d" in result.assumption
    assert "trailing samples" in result.assumption
    assert f"seed {_SEED}" in result.assumption


def test_il_distribution_carries_its_assumption() -> None:
    result = impermanent_loss_distribution(ratio_log_returns=_RETURNS, horizon_days=30, seed=_SEED)
    assert "realized daily ratio-vol" in result.assumption
    assert "constant-product" in result.assumption


# -- (d) IL distribution shape -----------------------------------------------


def test_il_distribution_quantiles_are_ordered_and_non_positive() -> None:
    result = impermanent_loss_distribution(ratio_log_returns=_RETURNS, horizon_days=30, seed=_SEED)
    q = result.quantiles
    assert set(q) == {"p5", "p25", "p50", "p75", "p95"}
    ordered = [q["p5"], q["p25"], q["p50"], q["p75"], q["p95"]]
    assert ordered == sorted(ordered)  # ascending
    assert all(v <= 1e-12 for v in ordered)  # IL is never positive
    assert result.mean <= 0.0


# -- validation --------------------------------------------------------------


def test_fit_requires_at_least_two_samples() -> None:
    with pytest.raises(ValueError, match="at least two"):
        fit_daily_vol([0.01])


def test_non_finite_return_rejected() -> None:
    with pytest.raises(ValueError, match="finite"):
        fit_daily_vol([0.01, float("inf")])


def test_horizon_must_be_positive() -> None:
    with pytest.raises(ValueError, match="horizon_days"):
        liquidation_probability(
            liquidation_distance=0.5, log_returns=_RETURNS, horizon_days=0, seed=_SEED
        )
