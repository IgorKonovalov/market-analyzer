"""Plan 0079 phase 2 — cross-pool discrepancy screener core.

Phase-2 done-when claims pinned here:
- a fixture with a known cross-pool discrepancy yields the correct **net** spread,
  with gas + slippage + fees demonstrably subtracted (a gross number is never
  returned as the opportunity), the correct buy/sell direction, and the
  capturability note;
- a discrepancy smaller than its costs is flagged not-capturable rather than
  surfaced as an opportunity (and is still returned, not silently dropped);
- a re-run over the same quotes is byte-identical (`model_dump` equal);
- determinism: `queried_at` is the newest quote `as_of`, not the wall clock.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from market_analyser.defi.discrepancy import (
    CAPTURABILITY_NOTE,
    DiscrepancyParams,
    scan_discrepancies,
)
from market_analyser.defi.models import PoolQuote

_AS_OF = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _quote(
    *,
    pool_id: str,
    dex: str,
    base_reserve: float,
    quote_reserve: float,
    fee_bps: float,
    pair: str = "WETH/USDC",
    trade_size: float = 1.0,
    as_of: datetime = _AS_OF,
) -> PoolQuote:
    return PoolQuote(
        pool_id=pool_id,
        dex=dex,
        chain="base",
        pair=pair,
        base_token="0xbase000000000000000000000000000000000001",
        quote_token="0xquote00000000000000000000000000000000002",
        trade_size=trade_size,
        price=quote_reserve / base_reserve,
        fee_bps=fee_bps,
        liquidity_base=base_reserve,
        liquidity_quote=quote_reserve,
        as_of=as_of,
    )


_PARAMS = DiscrepancyParams(est_gas_cost=1.0, min_net_spread=0.0)


def _edge_quotes() -> list[PoolQuote]:
    """A real net-of-cost edge: buy WETH at 3000 (pool A), sell at 3030 (pool B),
    both 1000-WETH-deep, trade 1 WETH."""
    return [
        _quote(
            pool_id="0xA", dex="aerodrome", base_reserve=1000, quote_reserve=3_000_000, fee_bps=5
        ),
        _quote(
            pool_id="0xB", dex="uniswap-v2", base_reserve=1000, quote_reserve=3_030_000, fee_bps=30
        ),
    ]


# --- net-of-cost math + direction -----------------------------------------------


def test_net_spread_subtracts_gas_slippage_fees_with_correct_direction() -> None:
    (obs,) = scan_discrepancies(_edge_quotes(), params=_PARAMS)

    # Direction: buy the cheap pool, sell the dear pool.
    assert obs.buy_pool == "0xA"
    assert obs.buy_dex == "aerodrome"
    assert obs.sell_pool == "0xB"
    assert obs.sell_dex == "uniswap-v2"
    assert obs.buy_price == pytest.approx(3000.0)
    assert obs.sell_price == pytest.approx(3030.0)

    # Cost components pinned to the constant-product model (independent arithmetic).
    assert obs.gross_spread == pytest.approx(30.0)  # (3030 - 3000) * 1
    assert obs.est_fees == pytest.approx(1.5 + 9.09)  # 0.05%·3000 + 0.30%·3030
    # slippage_buy = 3_000_000/999 - 3000; slippage_sell = 3030 - 3_030_000/1001
    assert obs.est_slippage == pytest.approx((3_000_000 / 999 - 3000) + (3030 - 3_030_000 / 1001))
    assert obs.est_gas_cost == 1.0

    # net = gross - gas - slippage - fees, and it is STRICTLY below gross (costs
    # were really subtracted — a gross number is never the opportunity).
    assert obs.net_spread == pytest.approx(
        obs.gross_spread - obs.est_gas_cost - obs.est_slippage - obs.est_fees
    )
    assert obs.net_spread == pytest.approx(12.380023976)
    assert obs.net_spread < obs.gross_spread
    assert obs.capturable_at_threshold is True
    assert obs.capturability_note == CAPTURABILITY_NOTE
    assert "UPPER BOUND" in obs.capturability_note


def test_capturability_note_present_on_every_observation() -> None:
    for obs in scan_discrepancies(_edge_quotes(), params=_PARAMS):
        assert obs.capturability_note.startswith("RPC-observed spread")


# --- sub-threshold is flagged, not dropped --------------------------------------


def test_sub_threshold_discrepancy_flagged_not_capturable_not_dropped() -> None:
    quotes = [
        _quote(
            pool_id="0xA", dex="aerodrome", base_reserve=1000, quote_reserve=3_000_000, fee_bps=5
        ),
        # Only a $1 gross gap — the ~$10 fees alone bury it.
        _quote(
            pool_id="0xB", dex="uniswap-v2", base_reserve=1000, quote_reserve=3_001_000, fee_bps=30
        ),
    ]
    results = scan_discrepancies(quotes, params=_PARAMS)

    # Surfaced (not silently dropped) but flagged not-capturable.
    assert len(results) == 1
    (obs,) = results
    assert obs.gross_spread == pytest.approx(1.0)
    assert obs.net_spread < 0
    assert obs.capturable_at_threshold is False


def test_threshold_gates_capturability() -> None:
    quotes = _edge_quotes()
    # net ≈ 12.38 — a threshold above it flips capturable off without dropping it.
    strict = DiscrepancyParams(est_gas_cost=1.0, min_net_spread=20.0)
    (obs,) = scan_discrepancies(quotes, params=strict)
    assert obs.net_spread == pytest.approx(12.380023976)
    assert obs.capturable_at_threshold is False


# --- grouping / ranking / minimal cases -----------------------------------------


def test_single_pool_pair_yields_no_observation() -> None:
    quotes = [
        _quote(
            pool_id="0xA", dex="aerodrome", base_reserve=1000, quote_reserve=3_000_000, fee_bps=5
        )
    ]
    assert scan_discrepancies(quotes, params=_PARAMS) == []


def test_multiple_pairs_ranked_by_net_spread_desc() -> None:
    quotes = [
        # WETH/USDC — a healthy edge.
        _quote(
            pool_id="0xA", dex="aerodrome", base_reserve=1000, quote_reserve=3_000_000, fee_bps=5
        ),
        _quote(
            pool_id="0xB", dex="uniswap-v2", base_reserve=1000, quote_reserve=3_030_000, fee_bps=30
        ),
        # WBTC/USDC — a sub-cost gap (negative net).
        _quote(
            pool_id="0xC",
            dex="aerodrome",
            pair="WBTC/USDC",
            base_reserve=1000,
            quote_reserve=60_000_000,
            fee_bps=5,
        ),
        _quote(
            pool_id="0xD",
            dex="uniswap-v2",
            pair="WBTC/USDC",
            base_reserve=1000,
            quote_reserve=60_010_000,
            fee_bps=30,
        ),
    ]
    results = scan_discrepancies(quotes, params=_PARAMS)
    assert [o.pair for o in results] == ["WETH/USDC", "WBTC/USDC"]
    assert results[0].net_spread > results[1].net_spread


def test_depth_exceeded_is_not_capturable_and_noted() -> None:
    quotes = [
        _quote(
            pool_id="0xA",
            dex="aerodrome",
            base_reserve=1000,
            quote_reserve=3_000_000,
            fee_bps=5,
            trade_size=2000.0,  # larger than the 1000-WETH pool depth
        ),
        _quote(
            pool_id="0xB",
            dex="uniswap-v2",
            base_reserve=1000,
            quote_reserve=3_030_000,
            fee_bps=30,
            trade_size=2000.0,
        ),
    ]
    (obs,) = scan_discrepancies(quotes, params=_PARAMS)
    assert obs.capturable_at_threshold is False
    assert obs.capturability_note.endswith("not executable at this size.")
    # Conservative finite sentinel: both pools' whole quote depth.
    assert obs.est_slippage == pytest.approx(3_000_000 + 3_030_000)


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
        _quote(
            pool_id="0xA",
            dex="aerodrome",
            base_reserve=1000,
            quote_reserve=3_000_000,
            fee_bps=5,
            as_of=older,
        ),
        _quote(
            pool_id="0xB",
            dex="uniswap-v2",
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
        _quote(
            pool_id="0xA", dex="aerodrome", base_reserve=1000, quote_reserve=3_000_000, fee_bps=5
        ),
        _quote(
            pool_id="0xB",
            dex="uniswap-v2",
            base_reserve=1000,
            quote_reserve=3_030_000,
            fee_bps=30,
            trade_size=2.0,
        ),
    ]
    with pytest.raises(ValueError, match="trade size"):
        scan_discrepancies(quotes, params=_PARAMS)
