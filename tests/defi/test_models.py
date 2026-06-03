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

from market_analyser.defi.models import DefiPosition, PositionToken


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
