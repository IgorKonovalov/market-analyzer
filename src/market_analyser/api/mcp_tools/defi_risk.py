"""`defi_risk` MCP tool (Plan 0042 phase 4, ADR-0037 / ADR-0104).

One read-only verb surfacing the risk engine's two output kinds as **conditional facts**
about a position, discriminated by `kind`:

- `kind="scenario"` — deterministic sensitivity to a **supplied** shock. For an Aave
  account (fetched by `address` + `chain` via the `AaveAccountSource`) it returns the
  health factor and liquidation distance before/after a supplied `collateral_shock`. For
  a constant-product LP (numbers **supplied** in the `lp` block) it returns the value and
  impermanent loss under supplied per-token shocks.
- `kind="conditional"` — probabilistic risk under a **stated volatility model**. For an
  Aave account it returns the probability of liquidation within `horizon_days` (a seeded
  Monte Carlo over the `collateral_symbol`'s trailing realized vol, with the assumption
  attached). For an LP it returns the impermanent-loss distribution from supplied
  `ratio_log_returns`.

Both legs are optional and independent: pass an Aave account, an `lp` block, or both. The
Aave leg is **fetched** (the phase-1 `getUserAccountData` read); the LP leg is **supplied**
by the caller, because per-token DeFi pricing is not plumbed to this tool (Plan 0042 scope
decision — LP position auto-discovery + pricing is a follow-up).

Strictly on the facts side of ADR-0015: every output is a condition or a conditional
estimate about the position, and the tool never emits an action. The probabilistic numbers
carry their volatility assumption inline (ADR-0037 invariant 3), and the seeded Monte Carlo
is reproducible (invariant 2). The body is factored as `_defi_risk_response` so the dispatch
is unit-testable without a live MCP server.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from market_analyser.data.adapters.lp_detail import LpDetailConfigError, LpDetailError
from market_analyser.data.errors import UpstreamDataError, failure_reason
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.sources import AaveAccountSource
from market_analyser.defi.models import AaveAccountDetail, Chain
from market_analyser.defi.risk import impermanent_loss_distribution, liquidation_probability
from market_analyser.defi.scan_job import EVM_ADDRESS_PATTERN
from market_analyser.defi.scenario import (
    aave_scenario,
    constant_product_lp_scenario,
    liquidation_distance,
)

_AAVE_SOURCE = "rpc"
_DISCLAIMER = (
    "Conditional facts about the position under the supplied or assumed inputs — "
    "a condition read, never investment advice or an action."
)

DEFI_RISK_DESCRIPTION = (
    "Read-only DeFi position risk as CONDITIONAL FACTS (a condition read, never investment "
    "advice or an action), discriminated by `kind`. Two independent, optional legs: an Aave "
    "account (fetched on-chain from `address` + `chain`) and a constant-product LP (numbers "
    "supplied in `lp`). Pass either or both. "
    "kind='scenario' (deterministic sensitivity to a SUPPLIED price move): the Aave leg "
    "returns {account, scenario:{collateral_shock, health_factor_before/after, "
    "liquidation_distance_before/after (fractional collateral drop that reaches HF=1), "
    "collateral/net value before/after}} for a supplied `collateral_shock` (e.g. -0.30); "
    "the LP leg returns {value_before, hodl_value_after, lp_value_after, impermanent_loss} "
    "from a supplied lp={amount0,price0,shock0,amount1,price1,shock1}. "
    "kind='conditional' (likelihood under a STATED vol model): the Aave leg returns "
    "{account, liquidation:{probability, horizon_days, daily_vol, seed, assumption}} — a "
    "seeded Monte Carlo of `collateral_symbol`'s trailing realized vol over `lookback_days` "
    "(a no-debt account returns liquidation=null with a note); the LP leg returns "
    "{quantiles, mean, daily_vol, assumption} from supplied lp={ratio_log_returns:[...]}. "
    "Every probabilistic figure carries its volatility assumption inline and is reproducible "
    "from `seed`; a trailing-vol fit cannot see a future regime shift (stated, not hidden). "
    "`horizon_days` (default 30), `seed` (default 0), `lookback_days` (default 90) control "
    "the Monte Carlo. On an Aave read failure the aave leg carries {error, message} "
    "(config/rate_limited/upstream_unavailable/malformed_response); the LP leg needs no "
    "network. `address` must be a raw 0x EVM address."
)


class LpInput(BaseModel):
    """The supplied constant-product LP inputs. For `kind="scenario"` supply the six
    amount/price/shock fields; for `kind="conditional"` supply `ratio_log_returns` (the
    trailing daily log-returns of the two tokens' price ratio)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount0: float | None = Field(default=None, gt=0)
    price0: float | None = Field(default=None, gt=0)
    shock0: float | None = None
    amount1: float | None = Field(default=None, gt=0)
    price1: float | None = Field(default=None, gt=0)
    shock1: float | None = None
    ratio_log_returns: list[float] | None = None


class DefiRiskInput(BaseModel):
    """MCP-boundary input. Unknown keys rejected. At least one leg (an Aave account via
    `address` + `chain`, or an `lp` block) must be supplied — validated in the body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["scenario", "conditional"]
    address: str | None = Field(default=None, pattern=EVM_ADDRESS_PATTERN)
    chain: Chain | None = None
    collateral_shock: float | None = None
    collateral_symbol: str | None = None
    lp: LpInput | None = None
    horizon_days: int = Field(default=30, ge=1)
    seed: int = 0
    lookback_days: int = Field(default=90, ge=2)


async def _defi_risk_response(
    *,
    provider: MarketDataProvider,
    aave_source: AaveAccountSource | None,
    params: DefiRiskInput,
) -> dict[str, Any]:
    """Body of the `defi_risk` tool: run whichever legs the input supplies. Raises
    `ValueError` (a clear tool error) when neither leg is specified or a leg is missing a
    field its `kind` needs."""
    has_aave = params.address is not None and params.chain is not None
    if (params.address is None) != (params.chain is None):
        raise ValueError("the Aave leg needs both address and chain, or neither")
    if not has_aave and params.lp is None:
        raise ValueError("provide an Aave account (address + chain) and/or an lp block")

    aave_leg = await _aave_leg(provider, aave_source, params) if has_aave else None
    lp_leg = _lp_leg(params) if params.lp is not None else None
    return {"kind": params.kind, "aave": aave_leg, "lp": lp_leg, "disclaimer": _DISCLAIMER}


async def _aave_leg(
    provider: MarketDataProvider,
    aave_source: AaveAccountSource | None,
    params: DefiRiskInput,
) -> dict[str, Any]:
    if aave_source is None:
        return _leg_error("config", "no Aave account source configured (set an RPC URL)")
    assert params.chain is not None  # guarded by the caller
    assert params.address is not None
    try:
        detail = await asyncio.to_thread(
            aave_source.fetch_account_detail, chain=params.chain, owner=params.address
        )
    except LpDetailConfigError as err:
        return _leg_error("config", str(err))
    except UpstreamDataError as err:
        return _leg_error(failure_reason(err), str(err))
    except LpDetailError as err:
        return _leg_error("malformed_response", str(err))

    if params.kind == "scenario":
        if params.collateral_shock is None:
            raise ValueError("kind='scenario' with an Aave account requires collateral_shock")
        scenario = aave_scenario(detail, collateral_shock=params.collateral_shock)
        return {
            "account": _account_dump(detail),
            "scenario": {
                "collateral_shock": scenario.collateral_shock,
                "collateral_value_before": scenario.collateral_value_before,
                "collateral_value_after": scenario.collateral_value_after,
                "debt_value": scenario.debt_value,
                "net_value_before": scenario.net_value_before,
                "net_value_after": scenario.net_value_after,
                "health_factor_before": scenario.health_factor_before,
                "health_factor_after": scenario.health_factor_after,
                "liquidation_distance_before": scenario.liquidation_distance_before,
                "liquidation_distance_after": scenario.liquidation_distance_after,
            },
            "error": None,
            "message": None,
        }

    # kind == "conditional"
    distance = liquidation_distance(detail.health_factor)
    if distance is None:
        return {
            "account": _account_dump(detail),
            "liquidation": None,
            "note": "account carries no debt — liquidation is not applicable",
            "error": None,
            "message": None,
        }
    if not params.collateral_symbol:
        raise ValueError("kind='conditional' with an Aave account requires collateral_symbol")
    log_returns = await _daily_log_returns(provider, params.collateral_symbol, params.lookback_days)
    estimate = liquidation_probability(
        liquidation_distance=distance,
        log_returns=log_returns,
        horizon_days=params.horizon_days,
        seed=params.seed,
    )
    return {
        "account": _account_dump(detail),
        "liquidation": {
            "probability": estimate.probability,
            "horizon_days": estimate.horizon_days,
            "liquidation_distance": estimate.liquidation_distance,
            "daily_vol": estimate.daily_vol,
            "seed": estimate.seed,
            "assumption": estimate.assumption,
        },
        "error": None,
        "message": None,
    }


def _lp_leg(params: DefiRiskInput) -> dict[str, Any]:
    lp = params.lp
    assert lp is not None  # guarded by the caller
    if params.kind == "scenario":
        scenario = constant_product_lp_scenario(
            amount0=_require(lp.amount0, "amount0"),
            price0=_require(lp.price0, "price0"),
            shock0=_require(lp.shock0, "shock0"),
            amount1=_require(lp.amount1, "amount1"),
            price1=_require(lp.price1, "price1"),
            shock1=_require(lp.shock1, "shock1"),
        )
        return {
            "value_before": scenario.value_before,
            "hodl_value_after": scenario.hodl_value_after,
            "lp_value_after": scenario.lp_value_after,
            "impermanent_loss": scenario.impermanent_loss,
            "error": None,
        }

    # kind == "conditional"
    if lp.ratio_log_returns is None:
        raise ValueError("kind='conditional' lp block requires ratio_log_returns")
    distribution = impermanent_loss_distribution(
        ratio_log_returns=lp.ratio_log_returns,
        horizon_days=params.horizon_days,
        seed=params.seed,
    )
    return {
        "quantiles": distribution.quantiles,
        "mean": distribution.mean,
        "horizon_days": distribution.horizon_days,
        "daily_vol": distribution.daily_vol,
        "seed": distribution.seed,
        "assumption": distribution.assumption,
        "error": None,
    }


async def _daily_log_returns(
    provider: MarketDataProvider, symbol: str, lookback_days: int
) -> list[float]:
    """The trailing daily log-returns of `symbol`'s closes over ~`lookback_days` (a few
    extra calendar days buffer non-trading gaps). Raises `ValueError` when there is too
    little history to fit a volatility."""
    now = datetime.now(tz=UTC)
    start = now - timedelta(days=lookback_days + 5)
    bars = await asyncio.to_thread(
        provider.get_ohlcv, symbol=symbol, timeframe="1d", start=start, end=now
    )
    closes = [bar.close for bar in bars]
    returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    if len(returns) < 2:
        raise ValueError(f"not enough price history for {symbol!r} to fit a volatility")
    return returns


def _account_dump(detail: AaveAccountDetail) -> dict[str, Any]:
    return {
        "chain": detail.chain,
        "total_collateral_base": detail.total_collateral_base,
        "total_debt_base": detail.total_debt_base,
        "available_borrows_base": detail.available_borrows_base,
        "liquidation_threshold": detail.liquidation_threshold,
        "ltv": detail.ltv,
        "health_factor": detail.health_factor,
    }


def _leg_error(reason: str, message: str) -> dict[str, Any]:
    return {"account": None, "error": reason, "message": message}


def _require(value: float | None, name: str) -> float:
    if value is None:
        raise ValueError(f"kind='scenario' lp block requires {name!r}")
    return value


def register_defi_risk(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    aave_account_sources: Mapping[str, AaveAccountSource] | None = None,
) -> None:
    """Bind the `defi_risk` tool. The provider + optional Aave source are captured by
    closure so the tool body keeps its single declared parameter. Registered
    unconditionally — the LP leg needs no source; the Aave leg reports a config error when
    no source is wired."""
    aave_source = (aave_account_sources or {}).get(_AAVE_SOURCE)

    @server.tool(name="defi_risk", description=DEFI_RISK_DESCRIPTION)
    async def defi_risk(params: DefiRiskInput) -> dict[str, Any]:
        return await _defi_risk_response(provider=provider, aave_source=aave_source, params=params)


__all__ = [
    "DEFI_RISK_DESCRIPTION",
    "DefiRiskInput",
    "LpInput",
    "_defi_risk_response",
    "register_defi_risk",
]
