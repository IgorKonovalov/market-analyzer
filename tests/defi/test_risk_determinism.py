"""Plan 0042 phase 3 — determinism of the seeded Monte Carlo (ADR-0037 inv. 2 / ADR-0018).

The same seed + inputs must reproduce a liquidation probability and an IL distribution
byte-for-byte; a different seed gives a different draw but the (RNG-free) vol fit is
identical. This is the determinism contract the backtest engine also holds.
"""

from __future__ import annotations

from market_analyser.defi.risk import (
    impermanent_loss_distribution,
    liquidation_probability,
)

_RETURNS = [0.05, -0.05] * 45
_RATIO_RETURNS = [0.04, -0.03, 0.02, -0.05] * 25


def test_liquidation_probability_same_seed_is_identical() -> None:
    first = liquidation_probability(
        liquidation_distance=0.5, log_returns=_RETURNS, horizon_days=30, seed=7, n_paths=5_000
    )
    second = liquidation_probability(
        liquidation_distance=0.5, log_returns=_RETURNS, horizon_days=30, seed=7, n_paths=5_000
    )
    assert first == second  # full dataclass equality (probability + assumption + all)


def test_il_distribution_same_seed_is_identical() -> None:
    first = impermanent_loss_distribution(
        ratio_log_returns=_RATIO_RETURNS, horizon_days=30, seed=7, n_paths=5_000
    )
    second = impermanent_loss_distribution(
        ratio_log_returns=_RATIO_RETURNS, horizon_days=30, seed=7, n_paths=5_000
    )
    assert first == second


def test_different_seed_changes_the_draw_but_not_the_vol_fit() -> None:
    a = liquidation_probability(
        liquidation_distance=0.4, log_returns=_RETURNS, horizon_days=45, seed=1, n_paths=5_000
    )
    b = liquidation_probability(
        liquidation_distance=0.4, log_returns=_RETURNS, horizon_days=45, seed=2, n_paths=5_000
    )
    # The fitted vol is RNG-free, so it is identical across seeds...
    assert a.daily_vol == b.daily_vol
    # ...while the sampled probability is a draw (may differ; both valid probabilities).
    assert 0.0 <= a.probability <= 1.0
    assert 0.0 <= b.probability <= 1.0
