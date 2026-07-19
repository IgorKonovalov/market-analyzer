"""Conditional probabilistic risk — seeded Monte Carlo (Plan 0042 phase 3).

The *probabilistic* half of the risk engine (ADR-0037 output kind 2): forward
likelihood quantities that are explicitly **conditional on a stated volatility model**,
never a directional market claim. Two estimates, each carrying its assumption in the
result so no bare number can escape (ADR-0037 invariant 3 — honest uncertainty):

- **`liquidation_probability`** — the probability that an Aave account's collateral
  falls far enough within `horizon_days` to bring its health factor to 1, given the
  current liquidation distance (`1 - 1/HF`, from phase 2). A seeded Monte Carlo of the
  collateral's daily log-returns under a **trailing** realized-vol fit and **zero drift**
  (asserting no market view): a path liquidates if its running cumulative return ever
  crosses the barrier `ln(1 - liquidation_distance)`. First-passage, not just terminal.

- **`impermanent_loss_distribution`** — the distribution of a constant-product LP's
  impermanent loss at `horizon_days`, from a seeded Monte Carlo of the two tokens'
  **price-ratio** log-returns under a trailing realized-vol fit. Each simulated terminal
  ratio `R` gives `IL = 2·sqrt(R)/(1+R) - 1`; the result reports quantiles + mean.

**Determinism (ADR-0037 invariant 2 / ADR-0018).** All randomness comes from a single
`random.Random(seed)`; the same seed + inputs reproduce the estimate exactly. Standard
library only (`random`, `math`, `statistics`) — no dependency, so reproducibility needs
no version pin. **Causality:** the vol fit reads only the trailing log-returns passed in;
no future sample can inform it (a garbage-in-when-a-regime-breaks limitation the result
states, not a bug). The fitted vol is surfaced on every result so the assumption is
inspectable, and the `assumption` string spells it out — a probability is never returned
bare.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

# Trading-day horizon math uses daily returns throughout; the caller supplies daily
# trailing log-returns and a horizon in days.
_DEFAULT_PATHS = 10_000
_DEFAULT_QUANTILES: tuple[float, ...] = (0.05, 0.25, 0.50, 0.75, 0.95)


@dataclass(frozen=True)
class LiquidationProbability:
    """A conditional liquidation-probability estimate. `probability` is meaningful
    **only** with `assumption` attached (the vol model + horizon); the field is required
    so a bare probability cannot be constructed."""

    probability: float
    horizon_days: int
    liquidation_distance: float
    daily_vol: float
    n_paths: int
    seed: int
    assumption: str


@dataclass(frozen=True)
class ImpermanentLossDistribution:
    """A conditional impermanent-loss distribution (quantiles + mean), likewise
    inseparable from its `assumption`."""

    quantiles: dict[str, float]
    mean: float
    horizon_days: int
    daily_vol: float
    n_paths: int
    seed: int
    assumption: str


def fit_daily_vol(log_returns: Sequence[float]) -> float:
    """The trailing realized daily volatility — the population stdev of the supplied
    daily log-returns. Reads only the trailing samples given (causal by construction).
    Raises `ValueError` on fewer than two samples or a non-finite input."""
    if len(log_returns) < 2:
        raise ValueError("need at least two trailing log-returns to fit a volatility")
    for r in log_returns:
        if not math.isfinite(r):
            raise ValueError("log-returns must be finite")
    return statistics.pstdev(log_returns)


def liquidation_probability(
    *,
    liquidation_distance: float,
    log_returns: Sequence[float],
    horizon_days: int,
    seed: int,
    n_paths: int = _DEFAULT_PATHS,
) -> LiquidationProbability:
    """Estimate P(the collateral falls enough to reach HF = 1 within `horizon_days`)
    under a trailing realized-vol, zero-drift GBM. `liquidation_distance` is the current
    fractional collateral cushion (`1 - 1/HF`, phase 2).

    A distance `<= 0` (already at/under the liquidation point) returns probability 1.0; a
    distance `>= 1` (needs a full collateral wipe, unreachable under lognormal prices)
    returns 0.0 — both without simulating. Deterministic given `seed`."""
    if horizon_days < 1:
        raise ValueError("horizon_days must be >= 1")
    if n_paths < 1:
        raise ValueError("n_paths must be >= 1")
    daily_vol = fit_daily_vol(log_returns)

    if liquidation_distance <= 0:
        probability = 1.0
    elif liquidation_distance >= 1:
        probability = 0.0
    else:
        barrier = math.log(1.0 - liquidation_distance)
        rng = random.Random(seed)
        hits = 0
        for _ in range(n_paths):
            cumulative = 0.0
            for _day in range(horizon_days):
                cumulative += rng.gauss(0.0, daily_vol)  # zero drift: no market view
                if cumulative <= barrier:
                    hits += 1
                    break
        probability = hits / n_paths

    assumption = (
        f"P(liquidation within {horizon_days}d) under realized daily vol "
        f"{daily_vol:.6f} fit from {len(log_returns)} trailing samples, zero-drift GBM, "
        f"{n_paths} paths (seed {seed}); a trailing-vol fit cannot see a future regime shift"
    )
    return LiquidationProbability(
        probability=probability,
        horizon_days=horizon_days,
        liquidation_distance=liquidation_distance,
        daily_vol=daily_vol,
        n_paths=n_paths,
        seed=seed,
        assumption=assumption,
    )


def impermanent_loss_distribution(
    *,
    ratio_log_returns: Sequence[float],
    horizon_days: int,
    seed: int,
    n_paths: int = _DEFAULT_PATHS,
    quantiles: Sequence[float] = _DEFAULT_QUANTILES,
) -> ImpermanentLossDistribution:
    """Estimate the distribution of a constant-product LP's impermanent loss at
    `horizon_days`, from a seeded Monte Carlo of the two tokens' **price-ratio**
    log-returns under a trailing realized-vol, zero-drift GBM. Each terminal ratio `R`
    gives `IL = 2·sqrt(R)/(1+R) - 1` (<= 0). Deterministic given `seed`."""
    if horizon_days < 1:
        raise ValueError("horizon_days must be >= 1")
    if n_paths < 1:
        raise ValueError("n_paths must be >= 1")
    for q in quantiles:
        if not 0.0 <= q <= 1.0:
            raise ValueError("quantiles must lie in [0, 1]")
    daily_vol = fit_daily_vol(ratio_log_returns)

    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_paths):
        cumulative = 0.0
        for _day in range(horizon_days):
            cumulative += rng.gauss(0.0, daily_vol)  # zero drift on the ratio
        ratio = math.exp(cumulative)
        samples.append(2.0 * math.sqrt(ratio) / (1.0 + ratio) - 1.0)
    samples.sort()

    quantile_map = {f"p{round(q * 100)}": _percentile(samples, q) for q in quantiles}
    mean = statistics.fmean(samples)
    assumption = (
        f"impermanent loss at {horizon_days}d under realized daily ratio-vol "
        f"{daily_vol:.6f} fit from {len(ratio_log_returns)} trailing samples, zero-drift "
        f"GBM, {n_paths} paths (seed {seed}); a full-range constant-product model"
    )
    return ImpermanentLossDistribution(
        quantiles=quantile_map,
        mean=mean,
        horizon_days=horizon_days,
        daily_vol=daily_vol,
        n_paths=n_paths,
        seed=seed,
        assumption=assumption,
    )


def _percentile(sorted_samples: Sequence[float], q: float) -> float:
    """The `q`-quantile of an already-sorted sample via linear interpolation between the
    two nearest ranks (the same convention `numpy.quantile`'s default 'linear' uses),
    so the result is stable and dependency-free."""
    if not sorted_samples:
        raise ValueError("cannot take a percentile of an empty sample")
    if len(sorted_samples) == 1:
        return sorted_samples[0]
    position = q * (len(sorted_samples) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_samples[lower]
    weight = position - lower
    return sorted_samples[lower] * (1.0 - weight) + sorted_samples[upper] * weight


__all__ = [
    "ImpermanentLossDistribution",
    "LiquidationProbability",
    "fit_daily_vol",
    "impermanent_loss_distribution",
    "liquidation_probability",
]
