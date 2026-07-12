"""Plan 0086 — cross-pool discrepancy screener v2 (executable-quote model, ADR-0080).

Done-when claims pinned here:
- the screener returns the correct buy/sell venue and
  net = max(sell_proceeds) - min(buy_cost) - gas;
- the reconstructed slippage/fee breakdown sums back to the executable numbers;
- a sub-threshold net is flagged not-capturable, not dropped (and still returned);
- determinism: stable sort, `queried_at` = newest quote `as_of`, byte-identical
  re-run;
- the capturability caveat rides every observation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from market_analyser.data.adapters.onchain_pools import _cp_executable_legs
from market_analyser.defi.discrepancy import (
    CAPTURABILITY_NOTE,
    ArbObservation,
    DiscrepancyParams,
    scan_discrepancies,
)
from market_analyser.defi.models import ExecutableQuote

_AS_OF = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
_PARAMS = DiscrepancyParams(est_gas_cost=1.0, min_net_spread=0.0)


def _exec_quote(
    *,
    pool_id: str,
    dex: str,
    base_reserve: float,
    quote_reserve: float,
    fee_bps: float,
    pair: str = "WETH/USDC",
    trade_size: float = 1.0,
    as_of: datetime = _AS_OF,
) -> ExecutableQuote:
    """Build an `ExecutableQuote` from constant-product reserves via the very math
    the CP adapter uses — so these quotes are exactly what `fetch_executable_quotes`
    would emit for the same reserves."""
    legs = _cp_executable_legs(
        liquidity_base=base_reserve,
        liquidity_quote=quote_reserve,
        fee_bps=fee_bps,
        trade_size=trade_size,
    )
    assert legs is not None
    buy_cost, sell_proceeds, marginal_price = legs
    return ExecutableQuote(
        pool_id=pool_id,
        dex=dex,
        chain="base",
        pair=pair,
        fee_tier=int(fee_bps),
        trade_size=trade_size,
        buy_cost=buy_cost,
        sell_proceeds=sell_proceeds,
        marginal_price=marginal_price,
        as_of=as_of,
    )


def _edge_quotes() -> list[ExecutableQuote]:
    """A net-of-cost edge: buy at pool A (marginal 3000), sell at pool B (marginal
    3030), both 1000-WETH-deep, trade 1 WETH."""
    return [
        _exec_quote(
            pool_id="0xA", dex="aerodrome", base_reserve=1000, quote_reserve=3_000_000, fee_bps=5
        ),
        _exec_quote(
            pool_id="0xB", dex="uniswap-v3", base_reserve=1000, quote_reserve=3_030_000, fee_bps=30
        ),
    ]


# --- net-of-cost math + direction -----------------------------------------------


def test_net_is_max_proceeds_minus_min_cost_minus_gas() -> None:
    (obs,) = scan_discrepancies(_edge_quotes(), params=_PARAMS)

    assert isinstance(obs, ArbObservation)
    # Buy the executably-cheapest venue, sell the executably-dearest.
    assert obs.buy_pool == "0xA"
    assert obs.buy_dex == "aerodrome"
    assert obs.sell_pool == "0xB"
    assert obs.sell_dex == "uniswap-v3"

    # Hand-computed x.y=k round trip (fee on input, size 1):
    #   buy_cost_A = 3_000_000 / (999 * 0.9995); sell_B = 0.997*3_030_000 / (1000+0.997).
    buy_cost_a = 3_000_000 / (999 * 0.9995)
    sell_b = 0.997 * 3_030_000 / (1000 + 0.997)
    assert obs.buy_cost == pytest.approx(buy_cost_a)
    assert obs.sell_proceeds == pytest.approx(sell_b)
    assert obs.est_gas_cost == 1.0
    assert obs.net_spread == pytest.approx(sell_b - buy_cost_a - 1.0)
    assert obs.net_spread == pytest.approx(12.395897, abs=1e-5)
    assert obs.capturable_at_threshold is True
    assert obs.capturability_note == CAPTURABILITY_NOTE
    assert "UPPER BOUND" in obs.capturability_note


def test_capturability_note_present_on_every_observation() -> None:
    for obs in scan_discrepancies(_edge_quotes(), params=_PARAMS):
        assert obs.capturability_note.startswith("RPC-observed spread")


def test_reconstructed_breakdown_sums_back_to_executable_numbers() -> None:
    """Auditability identity: sell_proceeds - buy_cost equals the marginal spread
    minus the reconstructed fees and slippage — the breakdown decomposes the
    executable numbers exactly against the zero-size reference."""
    (obs,) = scan_discrepancies(_edge_quotes(), params=_PARAMS)

    marginal_spread = (3030.0 - 3000.0) * obs.trade_size  # P_sell - P_buy, size 1
    assert obs.sell_proceeds - obs.buy_cost == pytest.approx(
        marginal_spread - obs.reconstructed_fees - obs.reconstructed_slippage
    )
    assert obs.reconstructed_fees == pytest.approx(0.0005 * 3000 + 0.003 * 3030)  # 1.5 + 9.09
    assert obs.reconstructed_slippage > 0


# --- sub-threshold is flagged, not dropped --------------------------------------


def test_sub_threshold_flagged_not_capturable_not_dropped() -> None:
    quotes = [
        _exec_quote(
            pool_id="0xA", dex="aerodrome", base_reserve=1000, quote_reserve=3_000_000, fee_bps=5
        ),
        # Only a $1 marginal gap — fees + slippage bury it.
        _exec_quote(
            pool_id="0xB", dex="uniswap-v3", base_reserve=1000, quote_reserve=3_001_000, fee_bps=30
        ),
    ]
    results = scan_discrepancies(quotes, params=_PARAMS)
    assert len(results) == 1
    (obs,) = results
    assert obs.net_spread < 0
    assert obs.capturable_at_threshold is False


def test_threshold_gates_capturability() -> None:
    strict = DiscrepancyParams(est_gas_cost=1.0, min_net_spread=20.0)
    (obs,) = scan_discrepancies(_edge_quotes(), params=strict)
    assert obs.net_spread == pytest.approx(12.395897, abs=1e-5)
    assert obs.capturable_at_threshold is False


# --- grouping / ranking / minimal cases -----------------------------------------


def test_single_pool_pair_yields_no_observation() -> None:
    quotes = [
        _exec_quote(
            pool_id="0xA", dex="aerodrome", base_reserve=1000, quote_reserve=3_000_000, fee_bps=5
        )
    ]
    assert scan_discrepancies(quotes, params=_PARAMS) == []


def test_combines_cp_and_cl_venues_for_the_same_pair() -> None:
    """The screener groups purely by pair, so quotes pooled from a CP source and a
    CL source for WETH/USDC compete in one observation — the Plan 0086 unification."""
    quotes = [
        _exec_quote(
            pool_id="0xCP", dex="aerodrome", base_reserve=1000, quote_reserve=3_000_000, fee_bps=5
        ),
        _exec_quote(
            pool_id="0xCL", dex="uniswap-v3", base_reserve=1000, quote_reserve=3_030_000, fee_bps=30
        ),
    ]
    (obs,) = scan_discrepancies(quotes, params=_PARAMS)
    assert obs.buy_pool == "0xCP"
    assert obs.sell_pool == "0xCL"


def test_multiple_pairs_ranked_by_net_spread_desc() -> None:
    quotes = [
        *_edge_quotes(),
        _exec_quote(
            pool_id="0xC",
            dex="aerodrome",
            pair="WBTC/USDC",
            base_reserve=1000,
            quote_reserve=60_000_000,
            fee_bps=5,
        ),
        _exec_quote(
            pool_id="0xD",
            dex="uniswap-v3",
            pair="WBTC/USDC",
            base_reserve=1000,
            quote_reserve=60_010_000,
            fee_bps=30,
        ),
    ]
    results = scan_discrepancies(quotes, params=_PARAMS)
    assert [o.pair for o in results] == ["WETH/USDC", "WBTC/USDC"]
    assert results[0].net_spread > results[1].net_spread


# --- determinism ----------------------------------------------------------------


def test_rerun_is_byte_identical() -> None:
    quotes = _edge_quotes()
    first = [o.model_dump() for o in scan_discrepancies(quotes, params=_PARAMS)]
    second = [o.model_dump() for o in scan_discrepancies(quotes, params=_PARAMS)]
    assert first == second


def test_queried_at_is_newest_as_of_not_wall_clock() -> None:
    older = datetime(2026, 7, 11, 11, 0, tzinfo=UTC)
    newer = datetime(2026, 7, 11, 11, 30, tzinfo=UTC)
    quotes = [
        _exec_quote(
            pool_id="0xA",
            dex="aerodrome",
            base_reserve=1000,
            quote_reserve=3_000_000,
            fee_bps=5,
            as_of=older,
        ),
        _exec_quote(
            pool_id="0xB",
            dex="uniswap-v3",
            base_reserve=1000,
            quote_reserve=3_030_000,
            fee_bps=30,
            as_of=newer,
        ),
    ]
    (obs,) = scan_discrepancies(quotes, params=_PARAMS)
    assert obs.queried_at == newer


def test_mixed_trade_sizes_in_a_pair_is_a_caller_bug() -> None:
    quotes = [
        _exec_quote(
            pool_id="0xA", dex="aerodrome", base_reserve=1000, quote_reserve=3_000_000, fee_bps=5
        ),
        _exec_quote(
            pool_id="0xB",
            dex="uniswap-v3",
            base_reserve=1000,
            quote_reserve=3_030_000,
            fee_bps=30,
            trade_size=2.0,
        ),
    ]
    with pytest.raises(ValueError, match="trade size"):
        scan_discrepancies(quotes, params=_PARAMS)
