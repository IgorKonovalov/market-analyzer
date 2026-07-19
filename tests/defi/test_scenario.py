"""Plan 0042 phase 2 — unit tests for the deterministic scenario engine.

Correctness is provable position math, so every case is checked against a **hand-computed**
value (ADR-0037: scenarios are deterministic, not statistical). Pins the phase-2 done-when:
(a) Aave HF + liquidation distance under a supplied collateral shock; (b) no-debt account →
undefined (None) HF + unbounded distance; (c) constant-product LP value + impermanent loss;
(d) the engine asserts no market view (the shock is a supplied input, not a prediction);
(e) determinism; plus input-validation guards.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from market_analyser.defi.models import AaveAccountDetail
from market_analyser.defi.scenario import (
    aave_scenario,
    constant_product_lp_scenario,
    liquidation_distance,
)

_AS_OF = datetime(2026, 7, 19, tzinfo=UTC)


def _account(
    *,
    collateral: float = 10_000.0,
    debt: float = 4_000.0,
    lt: float = 0.825,
    hf: float | None = 2.0625,  # collateral * lt / debt = 10000*0.825/4000
) -> AaveAccountDetail:
    return AaveAccountDetail(
        chain="base",
        total_collateral_base=collateral,
        total_debt_base=debt,
        available_borrows_base=2_000.0,
        liquidation_threshold=lt,
        ltv=0.80,
        health_factor=hf,
        as_of=_AS_OF,
    )


# -- (a) Aave HF + liquidation distance --------------------------------------


def test_aave_scenario_recomputes_hf_and_distance_under_shock() -> None:
    scenario = aave_scenario(_account(), collateral_shock=-0.30)

    assert scenario.collateral_value_before == pytest.approx(10_000.0)
    assert scenario.collateral_value_after == pytest.approx(7_000.0)  # 10000 * 0.70
    assert scenario.debt_value == pytest.approx(4_000.0)
    assert scenario.net_value_before == pytest.approx(6_000.0)
    assert scenario.net_value_after == pytest.approx(3_000.0)
    # HF is linear in collateral: 2.0625 * 0.70 = 1.44375.
    assert scenario.health_factor_before == pytest.approx(2.0625)
    assert scenario.health_factor_after == pytest.approx(1.44375)
    # liquidation distance = 1 - 1/HF.
    assert scenario.liquidation_distance_before == pytest.approx(1 - 1 / 2.0625)
    assert scenario.liquidation_distance_after == pytest.approx(1 - 1 / 1.44375)


def test_aave_liquidation_distance_before_is_the_drop_that_reaches_hf_one() -> None:
    # HF 2.0625 → collateral can fall ~51.5% before HF hits 1; verify by applying that
    # exact drop and confirming HF_after ≈ 1.
    scenario = aave_scenario(_account(), collateral_shock=0.0)
    drop = scenario.liquidation_distance_before
    assert drop is not None
    at_liq = aave_scenario(_account(), collateral_shock=-drop)
    assert at_liq.health_factor_after == pytest.approx(1.0)


# -- (b) no-debt account -----------------------------------------------------


def test_no_debt_account_has_undefined_hf_and_distance() -> None:
    scenario = aave_scenario(_account(debt=0.0, hf=None), collateral_shock=-0.50)

    assert scenario.health_factor_before is None
    assert scenario.health_factor_after is None
    assert scenario.liquidation_distance_before is None
    assert scenario.liquidation_distance_after is None
    # Net value is still recomputed (collateral only).
    assert scenario.net_value_after == pytest.approx(5_000.0)  # 10000*0.5 - 0


def test_liquidation_distance_helper_boundaries() -> None:
    assert liquidation_distance(2.0) == pytest.approx(0.5)
    assert liquidation_distance(1.0) == pytest.approx(0.0)
    assert liquidation_distance(0.5) == pytest.approx(-1.0)  # already underwater
    assert liquidation_distance(None) is None
    assert liquidation_distance(0.0) is None  # undefined, not a ZeroDivisionError


# -- (c) constant-product LP value + IL --------------------------------------


def test_constant_product_lp_il_matches_hand_computation() -> None:
    # Balanced 1 token0 @ $100 + 100 token1 @ $1 (value $200, k=100). token0 +21%.
    scenario = constant_product_lp_scenario(
        amount0=1.0, price0=100.0, shock0=0.21, amount1=100.0, price1=1.0, shock1=0.0
    )
    assert scenario.value_before == pytest.approx(200.0)
    assert scenario.hodl_value_after == pytest.approx(221.0)  # 1*121 + 100*1
    assert scenario.lp_value_after == pytest.approx(220.0)  # 2*sqrt(100*121*1)
    assert scenario.impermanent_loss == pytest.approx(220.0 / 221.0 - 1.0)
    assert scenario.impermanent_loss < 0  # IL is always a loss vs HODL


def test_no_price_move_has_zero_il() -> None:
    scenario = constant_product_lp_scenario(
        amount0=1.0, price0=100.0, shock0=0.0, amount1=100.0, price1=1.0, shock1=0.0
    )
    assert scenario.impermanent_loss == pytest.approx(0.0)
    assert scenario.lp_value_after == pytest.approx(scenario.value_before)


def test_il_matches_closed_form_ratio_formula() -> None:
    # IL(R) = 2*sqrt(R)/(1+R) - 1 for a price-ratio change R. Here token0 doubles → R=2.
    scenario = constant_product_lp_scenario(
        amount0=1.0, price0=100.0, shock0=1.0, amount1=100.0, price1=1.0, shock1=0.0
    )
    r = 2.0
    assert scenario.impermanent_loss == pytest.approx(2 * (r**0.5) / (1 + r) - 1)


# -- (d) no market view (supplied shock, not a prediction) -------------------


def test_engine_responds_to_supplied_shock_not_a_prediction() -> None:
    # The only thing that changes the outcome is the *supplied* shock — the engine
    # holds no internal price view. Different supplied shocks → different HF, monotone.
    mild = aave_scenario(_account(), collateral_shock=-0.10).health_factor_after
    severe = aave_scenario(_account(), collateral_shock=-0.40).health_factor_after
    assert mild is not None and severe is not None
    assert severe < mild  # a bigger supplied drop yields a lower HF, nothing predicted


# -- (e) determinism ---------------------------------------------------------


def test_scenarios_are_deterministic() -> None:
    assert aave_scenario(_account(), collateral_shock=-0.3) == aave_scenario(
        _account(), collateral_shock=-0.3
    )
    args = {
        "amount0": 2.0,
        "price0": 50.0,
        "shock0": -0.2,
        "amount1": 100.0,
        "price1": 1.0,
        "shock1": 0.0,
    }
    assert constant_product_lp_scenario(**args) == constant_product_lp_scenario(**args)


# -- input-validation guards -------------------------------------------------


def test_non_finite_collateral_shock_raises() -> None:
    with pytest.raises(ValueError, match="finite"):
        aave_scenario(_account(), collateral_shock=float("nan"))


def test_non_positive_amount_or_price_raises() -> None:
    with pytest.raises(ValueError, match="strictly positive"):
        constant_product_lp_scenario(
            amount0=0.0, price0=100.0, shock0=0.0, amount1=100.0, price1=1.0, shock1=0.0
        )


def test_shock_driving_price_non_positive_raises() -> None:
    with pytest.raises(ValueError, match="zero or negative"):
        constant_product_lp_scenario(
            amount0=1.0, price0=100.0, shock0=-1.0, amount1=100.0, price1=1.0, shock1=0.0
        )
