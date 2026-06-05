"""Plan 0032 phase 2: `DefiPosition` / `PositionToken` boundary validation.

The model is the "no garbage past the boundary" gate (ADR-0035, best-practices):
a NaN / Inf / negative `usd_value` and a non-positive / non-finite token `amount`
are rejected at construction, never silently coerced to zero. Downstream code
(discovery, the scan tool, later P&L / risk) may trust the fields.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from market_analyser.defi.models import DefiPosition, LpPositionDetail, PositionToken


def _token(amount: float = 1.0) -> PositionToken:
    return PositionToken(symbol="USDC", address="0xa0b8", amount=amount)


def test_valid_lending_position_constructs() -> None:
    position = DefiPosition(
        position_id="ethereum:aave-v3:x",
        chain="ethereum",
        protocol="aave-v3",
        kind="lending_supply",
        tokens=[_token(1000.0)],
        usd_value=1000.0,
    )
    assert position.kind == "lending_supply"
    assert position.tokens[0].symbol == "USDC"
    # LP-only fields default to None for a non-LP position.
    assert position.pool is None
    assert position.tick_lower is None
    assert position.in_range is None


def test_lp_position_carries_optional_tick_fields_as_none() -> None:
    """Tick boundaries are not decoded by the Zerion source (deep-adapter plan);
    the fields exist but stay None."""
    position = DefiPosition(
        position_id="arbitrum:uniswap-v3:nft-1",
        chain="arbitrum",
        protocol="uniswap-v3",
        kind="lp",
        tokens=[_token(600.0), PositionToken(symbol="WETH", address="0x82", amount=0.16)],
        usd_value=1000.0,
        pool="USDC / WETH",
    )
    assert position.pool == "USDC / WETH"
    assert position.tick_lower is None
    assert position.tick_upper is None
    assert position.in_range is None


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -1.0])
def test_usd_value_rejects_nan_inf_negative(bad_value: float) -> None:
    with pytest.raises(ValidationError):
        DefiPosition(
            position_id="x",
            chain="ethereum",
            protocol="aave-v3",
            kind="lending_supply",
            tokens=[_token()],
            usd_value=bad_value,
        )


def test_usd_value_zero_is_allowed() -> None:
    position = DefiPosition(
        position_id="x",
        chain="ethereum",
        protocol="aave-v3",
        kind="lending_supply",
        tokens=[_token()],
        usd_value=0.0,
    )
    assert position.usd_value == 0.0


@pytest.mark.parametrize("bad_amount", [0.0, -5.0, float("nan"), float("inf")])
def test_token_amount_rejects_non_positive_and_non_finite(bad_amount: float) -> None:
    with pytest.raises(ValidationError):
        PositionToken(symbol="USDC", address="0xa0b8", amount=bad_amount)


def test_position_requires_at_least_one_token() -> None:
    with pytest.raises(ValidationError):
        DefiPosition(
            position_id="x",
            chain="ethereum",
            protocol="aave-v3",
            kind="lending_supply",
            tokens=[],
            usd_value=1.0,
        )


def test_chain_must_be_a_target_chain() -> None:
    with pytest.raises(ValidationError):
        DefiPosition(
            position_id="x",
            chain="polygon",  # type: ignore[arg-type]  # off-target chain rejected
            protocol="aave-v3",
            kind="lending_supply",
            tokens=[_token()],
            usd_value=1.0,
        )


def test_position_is_frozen() -> None:
    position = DefiPosition(
        position_id="x",
        chain="ethereum",
        protocol="aave-v3",
        kind="lending_supply",
        tokens=[_token()],
        usd_value=1.0,
    )
    with pytest.raises(ValidationError):
        position.usd_value = 2.0  # frozen model — assignment raises


def test_finite_helper_sanity() -> None:
    # Guards the test's own assumption that inf/nan are the non-finite cases.
    assert not math.isfinite(float("inf"))
    assert not math.isfinite(float("nan"))


# -- Plan 0034 phase 2: deep-state model fields + LpPositionDetail --------------


def test_defi_position_deep_state_fields_default_none() -> None:
    # The phase-2 additive LP-detail fields are None until the deep adapter
    # (phases 3-4) and enrichment (phase 5) populate them.
    position = DefiPosition(
        position_id="base:aerodrome:x",
        chain="base",
        protocol="aerodrome",
        kind="lp",
        tokens=[_token(600.0), PositionToken(symbol="WETH", address="0x42", amount=0.1)],
        usd_value=1000.0,
        pool="WETH / AERO",
        pool_address="0xe3800a58b5535935850a10e082952ec3577d8dcc",
    )
    assert position.current_tick is None
    assert position.uncollected_fees is None


def test_defi_position_accepts_populated_deep_state() -> None:
    position = DefiPosition(
        position_id="base:aerodrome:x",
        chain="base",
        protocol="aerodrome",
        kind="lp",
        tokens=[_token(600.0)],
        usd_value=1000.0,
        tick_lower=-100,
        tick_upper=100,
        current_tick=10,
        in_range=True,
        uncollected_fees=[_token(0.5)],
    )
    assert position.current_tick == 10
    assert position.uncollected_fees is not None
    assert position.uncollected_fees[0].symbol == "USDC"


def test_lp_position_detail_in_range_constructs() -> None:
    detail = LpPositionDetail(
        tick_lower=-200,
        tick_upper=200,
        current_tick=0,
        in_range=True,
        uncollected_fees=[_token(0.25), PositionToken(symbol="WETH", address="0x42", amount=0.01)],
    )
    assert detail.in_range is True
    assert {t.symbol for t in detail.uncollected_fees} == {"USDC", "WETH"}


def test_lp_position_detail_out_of_range_constructs() -> None:
    # current_tick at/above tick_upper is out of range (half-open interval).
    detail = LpPositionDetail(
        tick_lower=-200,
        tick_upper=200,
        current_tick=200,
        in_range=False,
        uncollected_fees=[],
    )
    assert detail.in_range is False
    assert detail.uncollected_fees == []


def test_lp_position_detail_rejects_inconsistent_in_range() -> None:
    with pytest.raises(ValidationError):
        LpPositionDetail(
            tick_lower=-200,
            tick_upper=200,
            current_tick=0,  # in range, but flagged out
            in_range=False,
            uncollected_fees=[],
        )


def test_lp_position_detail_rejects_unordered_ticks() -> None:
    with pytest.raises(ValidationError):
        LpPositionDetail(
            tick_lower=200,
            tick_upper=-200,
            current_tick=0,
            in_range=False,
            uncollected_fees=[],
        )


def test_lp_position_detail_rejects_non_positive_fee_amount() -> None:
    # A fee token inherits PositionToken's finite/positive boundary.
    with pytest.raises(ValidationError):
        LpPositionDetail(
            tick_lower=-200,
            tick_upper=200,
            current_tick=0,
            in_range=True,
            uncollected_fees=[PositionToken(symbol="USDC", address="0xa0b8", amount=0.0)],
        )


def test_lp_position_detail_is_frozen() -> None:
    detail = LpPositionDetail(
        tick_lower=-200, tick_upper=200, current_tick=0, in_range=True, uncollected_fees=[]
    )
    with pytest.raises(ValidationError):
        detail.current_tick = 5  # frozen model — assignment raises
