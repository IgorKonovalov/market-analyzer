"""Deterministic scenario sensitivity — DeFi position what-ifs (Plan 0042 phase 2).

The *scenario* half of the risk engine (ADR-0037 output kind 1): pure functions that
recompute a position's exposure for a **supplied** price move on the underlying(s).
Every input is given — the shock is a parameter, never a prediction — so the engine
asserts **no market view** (ADR-0037 invariant 1). Correctness is provable position
math, unit-tested against hand-computed inputs, not statistical skill.

Two independent conditional-fact computations, each a pure function of its inputs:

- **Aave account (`aave_scenario`).** Given the aggregate `AaveAccountDetail` (phase 1)
  and a supplied fractional shock `s` to the account's **collateral value** (debt held
  constant — the typical volatile-collateral / stable-debt case), recompute the health
  factor and the liquidation distance. HF is linear in collateral with debt and the
  blended liquidation threshold fixed, so `HF' = HF · (1 + s)`, and the **liquidation
  distance** — the further fractional collateral drop that brings HF to 1 — is the clean
  identity `1 - 1/HF`. A no-debt account has an undefined HF (`None`) and an unbounded
  liquidation distance (`None`). A shock to the *debt* asset, and per-asset (rather than
  blended) collateral shocks, are documented follow-ups (Plan 0042).

- **Constant-product LP (`constant_product_lp_scenario`).** Given a two-token
  full-range `x·y=k` position's token amounts + **supplied** current prices + per-token
  shocks, recompute the position value and the impermanent loss versus HODL. Exact for a
  full-range AMM: under an external price move the pool rebalances to `a0'·a1' = k` at
  the new price ratio, so `LP_value_after = 2·√(k · p0' · p1')` and
  `IL = LP_value_after / HODL_value_after - 1` (≤ 0). **Concentrated-liquidity range
  amplification is not modelled here** — exact CL IL needs the position's liquidity `L`
  and its price bounds, which the `DefiPosition` model does not carry; the full-range
  formula is a documented lower bound on a CL position's IL (a Plan 0042 follow-up).

No RNG, no wall-clock, no network — deterministic by construction. Prices/amounts are
supplied by the caller (the `defi_risk` tool reads current prices; phase 3 supplies the
probabilistic layer), keeping this module a pure, unit-testable math core.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from market_analyser.defi.models import AaveAccountDetail


@dataclass(frozen=True)
class AaveScenario:
    """The recomputed Aave account exposure under a supplied collateral shock. All
    values are conditional facts about the position, never an action. `health_factor_*`
    and `liquidation_distance_*` are `None` for a no-debt account (undefined HF).
    `liquidation_distance_*` is the fractional further collateral drop that reaches
    HF = 1 (negative when already at/under it)."""

    collateral_shock: float
    collateral_value_before: float
    collateral_value_after: float
    debt_value: float
    net_value_before: float
    net_value_after: float
    health_factor_before: float | None
    health_factor_after: float | None
    liquidation_distance_before: float | None
    liquidation_distance_after: float | None


@dataclass(frozen=True)
class LpScenario:
    """The recomputed constant-product LP exposure under supplied per-token shocks.
    `impermanent_loss` is the LP-vs-HODL ratio minus one (≤ 0)."""

    value_before: float
    hodl_value_after: float
    lp_value_after: float
    impermanent_loss: float


def liquidation_distance(health_factor: float | None) -> float | None:
    """The fractional collateral-value drop that brings the account to HF = 1, the
    identity `1 - 1/HF`. `None` for an undefined (no-debt) HF; `None` too for a
    non-positive HF (a fully-eroded account, where the distance is undefined). A
    negative result means the account is already at or past the liquidation point."""
    if health_factor is None or health_factor <= 0:
        return None
    return 1.0 - 1.0 / health_factor


def aave_scenario(detail: AaveAccountDetail, *, collateral_shock: float) -> AaveScenario:
    """Recompute an Aave account's health factor and liquidation distance for a
    **supplied** fractional shock to its collateral value (e.g. `-0.30` for a 30% drop),
    holding debt and the blended liquidation threshold constant.

    `collateral_shock` is an input, never a prediction (ADR-0037 invariant 1). Raises
    `ValueError` on a non-finite shock. Deterministic."""
    if not math.isfinite(collateral_shock):
        raise ValueError("collateral_shock must be finite")
    collateral_before = detail.total_collateral_base
    collateral_after = collateral_before * (1.0 + collateral_shock)
    debt = detail.total_debt_base
    hf_before = detail.health_factor
    # HF is linear in collateral (debt + blended LT fixed): HF' = HF·(1 + s).
    hf_after = None if hf_before is None else hf_before * (1.0 + collateral_shock)
    return AaveScenario(
        collateral_shock=collateral_shock,
        collateral_value_before=collateral_before,
        collateral_value_after=collateral_after,
        debt_value=debt,
        net_value_before=collateral_before - debt,
        net_value_after=collateral_after - debt,
        health_factor_before=hf_before,
        health_factor_after=hf_after,
        liquidation_distance_before=liquidation_distance(hf_before),
        liquidation_distance_after=liquidation_distance(hf_after),
    )


def constant_product_lp_scenario(
    *,
    amount0: float,
    price0: float,
    shock0: float,
    amount1: float,
    price1: float,
    shock1: float,
) -> LpScenario:
    """Recompute a two-token full-range `x·y=k` LP position's value and impermanent
    loss for **supplied** per-token shocks (`shock0`/`shock1` are fractional moves on
    each token's price). Exact for a full-range constant-product AMM; a documented lower
    bound on a concentrated-liquidity position's IL (CL amplification is out of scope —
    see the module docstring).

    Shocks are inputs, never predictions (ADR-0037 invariant 1). Raises `ValueError` on
    a non-positive amount/price, a non-finite input, or a shock that drives a price
    non-positive. Deterministic."""
    for name, value in (
        ("amount0", amount0),
        ("price0", price0),
        ("amount1", amount1),
        ("price1", price1),
    ):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and strictly positive")
    for name, value in (("shock0", shock0), ("shock1", shock1)):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    price0_after = price0 * (1.0 + shock0)
    price1_after = price1 * (1.0 + shock1)
    if price0_after <= 0 or price1_after <= 0:
        raise ValueError("a shock may not drive a price to zero or negative")

    value_before = amount0 * price0 + amount1 * price1
    hodl_value_after = amount0 * price0_after + amount1 * price1_after
    # Full-range x·y=k: arbitrage rebalances reserves to the new price ratio while
    # preserving k, so the post-move LP value is 2·√(k · p0' · p1').
    k = amount0 * amount1
    lp_value_after = 2.0 * math.sqrt(k * price0_after * price1_after)
    impermanent_loss = lp_value_after / hodl_value_after - 1.0 if hodl_value_after > 0 else 0.0
    return LpScenario(
        value_before=value_before,
        hodl_value_after=hodl_value_after,
        lp_value_after=lp_value_after,
        impermanent_loss=impermanent_loss,
    )


__all__ = [
    "AaveScenario",
    "LpScenario",
    "aave_scenario",
    "constant_product_lp_scenario",
    "liquidation_distance",
]
