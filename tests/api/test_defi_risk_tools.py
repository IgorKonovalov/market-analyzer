"""Plan 0042 phase 4 — unit tests for the `defi_risk` tool body.

Drives `_defi_risk_response` directly (no live MCP server) with a fake `AaveAccountSource`
and a fake OHLCV provider, via `anyio.run` (the repo's sync-test-over-async convention).
Pins the phase-4 done-when: (a) kind="scenario" returns Aave HF / liquidation distance for
a supplied shock and constant-product LP impermanent loss from supplied numbers;
(b) kind="conditional" returns a liquidation probability (with its assumption) and an IL
distribution; (c) the outputs + description carry NO exit / rebalance / de-risk / advice
language (ADR-0037 invariant 4); plus leg-validation + a typed Aave config error.
`EXPECTED_FULL_TOOLSET` membership is pinned in `test_mcp_tools.py`.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import anyio
import pytest

from market_analyser.api.mcp_tools.defi_risk import (
    DEFI_RISK_DESCRIPTION,
    DefiRiskInput,
    LpInput,
    _defi_risk_response,
)
from market_analyser.data.adapters.lp_detail import LpDetailConfigError
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Bar
from market_analyser.defi.models import AaveAccountDetail, Chain

_ADDRESS = "0x" + "1" * 40
_AS_OF = datetime(2026, 7, 19, tzinfo=UTC)


def _detail(*, debt: float = 4_000.0, hf: float | None = 2.0625) -> AaveAccountDetail:
    return AaveAccountDetail(
        chain="base",
        total_collateral_base=10_000.0,
        total_debt_base=debt,
        available_borrows_base=2_000.0,
        liquidation_threshold=0.825,
        ltv=0.80,
        health_factor=hf,
        as_of=_AS_OF,
    )


class _FakeAaveSource:
    """Structurally an `AaveAccountSource`: returns a canned detail or raises."""

    def __init__(
        self, detail: AaveAccountDetail | None = None, error: Exception | None = None
    ) -> None:
        self._detail = detail if detail is not None else _detail()
        self._error = error

    def fetch_account_detail(self, *, chain: Chain, owner: str) -> AaveAccountDetail:
        if self._error is not None:
            raise self._error
        return self._detail


class _FakeProvider:
    """Minimal OHLCV provider returning canned daily bars whose closes alternate, giving
    a non-zero realized vol. Only `get_ohlcv` is exercised by the tool."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        closes = [100.0, 110.0] * 10  # 20 bars → 19 alternating log-returns
        return [
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                event_ts=_AS_OF - timedelta(days=len(closes) - i),
                open=c,
                high=c,
                low=c,
                close=c,
                volume=1.0,
                source="test",
            )
            for i, c in enumerate(closes)
        ]


def _run(params: DefiRiskInput, *, aave: _FakeAaveSource | None = None) -> dict[str, Any]:
    return anyio.run(
        functools.partial(
            _defi_risk_response,
            provider=cast("MarketDataProvider", _FakeProvider()),
            aave_source=cast("Any", aave),
            params=params,
        )
    )


# -- (a) scenario ------------------------------------------------------------


def test_scenario_aave_returns_hf_and_liquidation_distance() -> None:
    result = _run(
        DefiRiskInput(kind="scenario", address=_ADDRESS, chain="base", collateral_shock=-0.30),
        aave=_FakeAaveSource(),
    )
    scenario = result["aave"]["scenario"]
    assert scenario["health_factor_before"] == pytest.approx(2.0625)
    assert scenario["health_factor_after"] == pytest.approx(1.44375)
    assert scenario["liquidation_distance_before"] == pytest.approx(1 - 1 / 2.0625)
    assert result["aave"]["error"] is None
    assert result["lp"] is None


def test_scenario_lp_returns_impermanent_loss_from_supplied_numbers() -> None:
    result = _run(
        DefiRiskInput(
            kind="scenario",
            lp=LpInput(
                amount0=1.0, price0=100.0, shock0=0.21, amount1=100.0, price1=1.0, shock1=0.0
            ),
        )
    )
    lp = result["lp"]
    assert lp["value_before"] == pytest.approx(200.0)
    assert lp["hodl_value_after"] == pytest.approx(221.0)
    assert lp["impermanent_loss"] == pytest.approx(220.0 / 221.0 - 1.0)
    assert result["aave"] is None


def test_scenario_both_legs_together() -> None:
    result = _run(
        DefiRiskInput(
            kind="scenario",
            address=_ADDRESS,
            chain="base",
            collateral_shock=-0.1,
            lp=LpInput(
                amount0=1.0, price0=100.0, shock0=0.0, amount1=100.0, price1=1.0, shock1=0.0
            ),
        ),
        aave=_FakeAaveSource(),
    )
    assert result["aave"]["scenario"]["health_factor_after"] == pytest.approx(2.0625 * 0.9)
    assert result["lp"]["impermanent_loss"] == pytest.approx(0.0)


# -- (b) conditional ---------------------------------------------------------


def test_conditional_aave_returns_probability_with_assumption() -> None:
    result = _run(
        DefiRiskInput(
            kind="conditional",
            address=_ADDRESS,
            chain="base",
            collateral_symbol="ETH",
            horizon_days=30,
            seed=1,
        ),
        aave=_FakeAaveSource(),
    )
    liq = result["aave"]["liquidation"]
    assert 0.0 <= liq["probability"] <= 1.0
    assert "realized daily vol" in liq["assumption"]
    assert liq["horizon_days"] == 30


def test_conditional_aave_no_debt_is_not_applicable() -> None:
    result = _run(
        DefiRiskInput(kind="conditional", address=_ADDRESS, chain="base", collateral_symbol="ETH"),
        aave=_FakeAaveSource(_detail(debt=0.0, hf=None)),
    )
    assert result["aave"]["liquidation"] is None
    assert "no debt" in result["aave"]["note"]


def test_conditional_lp_returns_il_distribution() -> None:
    result = _run(
        DefiRiskInput(
            kind="conditional",
            lp=LpInput(ratio_log_returns=[0.05, -0.05] * 45),
            horizon_days=30,
            seed=1,
        )
    )
    lp = result["lp"]
    assert set(lp["quantiles"]) == {"p5", "p25", "p50", "p75", "p95"}
    assert lp["mean"] <= 0.0
    assert "constant-product" in lp["assumption"]


# -- (c) no advice language (ADR-0037 invariant 4) ---------------------------

_FORBIDDEN = ("buy", "sell", "exit", "rebalance", "de-risk", "derisk", "should", "recommend")


def test_outputs_and_description_carry_no_advice_language() -> None:
    text = DEFI_RISK_DESCRIPTION.lower()
    assert not any(word in text for word in _FORBIDDEN), "description must stay advice-free"

    # A representative full output (both legs, both facts) must also be advice-free.
    scenario = _run(
        DefiRiskInput(
            kind="scenario",
            address=_ADDRESS,
            chain="base",
            collateral_shock=-0.3,
            lp=LpInput(
                amount0=1.0, price0=100.0, shock0=0.1, amount1=100.0, price1=1.0, shock1=0.0
            ),
        ),
        aave=_FakeAaveSource(),
    )
    conditional = _run(
        DefiRiskInput(
            kind="conditional", address=_ADDRESS, chain="base", collateral_symbol="ETH", seed=1
        ),
        aave=_FakeAaveSource(),
    )
    for payload in (scenario, conditional):
        dumped = json.dumps(payload).lower()
        assert not any(word in dumped for word in _FORBIDDEN), payload


# -- validation + typed errors -----------------------------------------------


def test_no_leg_supplied_is_an_error() -> None:
    with pytest.raises(ValueError, match="Aave account"):
        _run(DefiRiskInput(kind="scenario"))


def test_address_without_chain_is_an_error() -> None:
    with pytest.raises(ValueError, match="both address and chain"):
        _run(DefiRiskInput(kind="scenario", address=_ADDRESS, collateral_shock=-0.1))


def test_aave_source_absent_reports_config_error() -> None:
    result = _run(
        DefiRiskInput(kind="scenario", address=_ADDRESS, chain="base", collateral_shock=-0.1),
        aave=None,
    )
    assert result["aave"]["error"] == "config"


def test_aave_config_error_is_surfaced_typed() -> None:
    result = _run(
        DefiRiskInput(kind="scenario", address=_ADDRESS, chain="base", collateral_shock=-0.1),
        aave=_FakeAaveSource(error=LpDetailConfigError("no RPC URL")),
    )
    assert result["aave"]["error"] == "config"
    assert "no RPC URL" in result["aave"]["message"]
