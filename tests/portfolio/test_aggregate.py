"""Cross-venue aggregation tests (Plan 0041 phase 3).

The done-when, read at the assertion level:

(a) unified holdings across all three venues with average-cost basis —
    futures entry price, DeFi replay basis joined by position id, the manual
    file's user-stated basis, spot honestly `None`;
(b) unrealized P&L and exposure by asset and by venue, hand-worked exact;
(c) aggregation is deterministic given source snapshots — byte-identical
    dumps across runs and across price-map insertion orders;
(d) each leg reports its pricing reference and its own as-of time — no
    single implied "now"; unpriced holdings stay visible but never enter the
    exposure sums as zeros.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from market_analyser.data.types import AccountHoldings, FuturesPosition, SpotBalance
from market_analyser.defi.models import DefiPosition, PositionToken
from market_analyser.portfolio.aggregate import (
    BINANCE_MARK_PRICING_SOURCE,
    DEFI_PRICING_SOURCE,
    PricePoint,
    aggregate_portfolio,
    unrealized_contributor_count,
)
from market_analyser.portfolio.models import Holding, PortfolioSummary

_BINANCE_AS_OF = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
_DEFI_AS_OF = datetime(2026, 7, 6, 12, 1, tzinfo=UTC)
_MANUAL_AS_OF = datetime(2026, 7, 1, tzinfo=UTC)
_MANUAL_ROW_AS_OF = datetime(2026, 6, 15, tzinfo=UTC)
_QUOTE_AS_OF = datetime(2026, 7, 6, 11, 59, tzinfo=UTC)
_QUERIED_AT = datetime(2026, 7, 6, 12, 2, tzinfo=UTC)


def _binance() -> AccountHoldings:
    return AccountHoldings(
        venue="binance",
        spot=[
            SpotBalance(asset="BTC", free=0.5, locked=0.0),
            SpotBalance(asset="DUSTCOIN", free=42.0, locked=0.0),  # no quote -> unpriced
        ],
        futures=[
            FuturesPosition(
                symbol="BTCUSDT",
                quantity=0.01,
                entry_price=60_000.0,
                position_side="BOTH",
                mark_price=61_000.0,
                unrealized_pnl_usd=10.0,
            ),
            FuturesPosition(
                symbol="ETHUSDT",
                quantity=-0.5,
                entry_price=3_000.0,
                position_side="BOTH",
                mark_price=2_950.0,
                unrealized_pnl_usd=25.0,
            ),
        ],
        as_of=_BINANCE_AS_OF,
    )


def _defi_positions() -> list[DefiPosition]:
    return [
        DefiPosition(
            position_id="base:aerodrome:lp-1",
            chain="base",
            protocol="aerodrome",
            kind="lp",
            tokens=[
                PositionToken(symbol="USDC", address="0xusdc", amount=2_500.0),
                PositionToken(symbol="WETH", address="0xweth", amount=0.8),
            ],
            usd_value=5_000.0,
            pool="vAMM-WETH/USDC",
            pool_address="0xpool",
        ),
        DefiPosition(
            position_id="base:aave:borrow-1",
            chain="base",
            protocol="aave-v3",
            kind="lending_borrow",
            tokens=[PositionToken(symbol="USDC", address="0xusdc", amount=1_000.0)],
            usd_value=1_000.0,
        ),
    ]


def _manual() -> list[Holding]:
    return [
        Holding(
            symbol="AAPL",
            venue="manual",
            quantity=100.0,
            avg_cost=185.5,
            as_of=_MANUAL_AS_OF,
            kind="manual",
        ),
        Holding(
            symbol="GLD",
            venue="manual",
            quantity=20.0,
            avg_cost=None,  # cost honestly unknown — excluded from P&L, not zeroed
            as_of=_MANUAL_ROW_AS_OF,
            kind="manual",
        ),
    ]


def _prices() -> dict[tuple[str, str], PricePoint]:
    return {
        ("binance", "BTC"): PricePoint(price=61_000.0, source="yahoo:BTC-USD", as_of=_QUOTE_AS_OF),
        ("manual", "AAPL"): PricePoint(price=200.0, source="yahoo:AAPL", as_of=_QUOTE_AS_OF),
        ("manual", "GLD"): PricePoint(price=250.0, source="yahoo:GLD", as_of=_QUOTE_AS_OF),
    }


def _aggregate(
    prices: dict[tuple[str, str], PricePoint] | None = None,
) -> PortfolioSummary:
    return aggregate_portfolio(
        binance=_binance(),
        defi_positions=_defi_positions(),
        defi_as_of=_DEFI_AS_OF,
        defi_basis={"base:aerodrome:lp-1": 4_000.0},
        manual=_manual(),
        prices=prices if prices is not None else _prices(),
        queried_at=_QUERIED_AT,
    )


# --- (a) unified holdings + basis -------------------------------------------------


def test_unified_holdings_span_all_venues_in_fixed_order() -> None:
    summary = _aggregate()
    assert [(h.venue, h.symbol, h.kind) for h in summary.holdings] == [
        ("binance", "BTC", "spot"),
        ("binance", "DUSTCOIN", "spot"),
        ("binance", "BTCUSDT", "futures"),
        ("binance", "ETHUSDT", "futures"),
        ("defi", "vAMM-WETH/USDC", "defi:lp"),
        ("defi", "USDC", "defi:lending_borrow"),
        ("manual", "AAPL", "manual"),
        ("manual", "GLD", "manual"),
    ]


def test_average_cost_basis_per_venue() -> None:
    summary = _aggregate()
    by_key = {(h.venue, h.symbol): h for h in summary.holdings}
    # Futures: the venue's entry price is the basis (plan open question).
    assert by_key[("binance", "BTCUSDT")].avg_cost == 60_000.0
    assert by_key[("binance", "ETHUSDT")].avg_cost == 3_000.0
    # DeFi: the ADR-0036 replay's remaining basis joined by position_id.
    assert by_key[("defi", "vAMM-WETH/USDC")].avg_cost == 4_000.0
    # A borrow is a liability — no average-cost basis, negative value.
    assert by_key[("defi", "USDC")].avg_cost is None
    assert by_key[("defi", "USDC")].usd_value == -1_000.0
    assert by_key[("defi", "USDC")].quantity == -1.0
    # Manual: the user-stated basis; spot: honestly None (the venue keeps none).
    assert by_key[("manual", "AAPL")].avg_cost == 185.5
    assert by_key[("binance", "BTC")].avg_cost is None


# --- (b) P&L + exposure, hand-worked ----------------------------------------------


def test_unrealized_pnl_is_hand_worked_exact() -> None:
    summary = _aggregate()
    # BTCUSDT long: 0.01 * 61000 - 0.01 * 60000 = +10
    # ETHUSDT short: -0.5 * 2950 - (-0.5 * 3000) = +25
    # DeFi LP: 5000 - 4000 = +1000
    # AAPL: 100 * 200 - 100 * 185.5 = +1450
    # (spot BTC, DUSTCOIN, GLD, borrow: no basis -> excluded, not zeroed)
    assert summary.unrealized_pnl_usd == pytest.approx(10.0 + 25.0 + 1000.0 + 1450.0)
    assert unrealized_contributor_count(summary) == 4


def test_exposure_by_asset_and_venue_hand_worked() -> None:
    summary = _aggregate()
    assert summary.exposure_by_asset == pytest.approx(
        {
            "BTC": 0.5 * 61_000.0,  # 30500
            "BTCUSDT": 0.01 * 61_000.0,  # 610
            "ETHUSDT": -0.5 * 2_950.0,  # -1475 — a short is negative exposure
            "vAMM-WETH/USDC": 5_000.0,
            "USDC": -1_000.0,  # a borrow is negative exposure
            "AAPL": 20_000.0,
            "GLD": 5_000.0,
        }
    )
    assert summary.exposure_by_venue == pytest.approx(
        {
            "binance": 30_500.0 + 610.0 - 1_475.0,
            "defi": 5_000.0 - 1_000.0,
            "manual": 25_000.0,
        }
    )


def test_unpriced_holding_stays_visible_but_out_of_exposure() -> None:
    summary = _aggregate()
    dust = next(h for h in summary.holdings if h.symbol == "DUSTCOIN")
    assert dust.usd_value is None
    assert dust.pricing_source is None
    assert "DUSTCOIN" not in summary.exposure_by_asset  # excluded, never a zero


def test_no_basis_bearing_holding_yields_none_pnl_not_zero() -> None:
    summary = aggregate_portfolio(
        binance=AccountHoldings(
            venue="binance",
            spot=[SpotBalance(asset="BTC", free=1.0, locked=0.0)],
            futures=[],
            as_of=_BINANCE_AS_OF,
        ),
        queried_at=_QUERIED_AT,
    )
    assert summary.unrealized_pnl_usd is None


# --- (d) provenance: pricing references + per-leg as-of ----------------------------


def test_every_valuation_names_its_pricing_reference() -> None:
    summary = _aggregate()
    by_key = {(h.venue, h.symbol): h for h in summary.holdings}
    assert by_key[("binance", "BTC")].pricing_source == "yahoo:BTC-USD"
    assert by_key[("binance", "BTCUSDT")].pricing_source == BINANCE_MARK_PRICING_SOURCE
    assert by_key[("defi", "vAMM-WETH/USDC")].pricing_source == DEFI_PRICING_SOURCE
    assert by_key[("manual", "AAPL")].pricing_source == "yahoo:AAPL"
    for holding in summary.holdings:
        assert (holding.usd_value is None) == (holding.pricing_source is None)


def test_legs_as_of_carries_three_distinct_stamps_never_blended() -> None:
    summary = _aggregate()
    assert summary.legs_as_of == {
        "binance": _BINANCE_AS_OF,
        "defi": _DEFI_AS_OF,
        # The manual leg's stamp is its OLDEST row — the conservative read of
        # user-maintained freshness.
        "manual": _MANUAL_ROW_AS_OF,
    }
    assert summary.queried_at == _QUERIED_AT


def test_absent_legs_get_no_as_of_entry() -> None:
    summary = aggregate_portfolio(manual=_manual(), prices={}, queried_at=_QUERIED_AT)
    assert set(summary.legs_as_of) == {"manual"}
    assert set(summary.exposure_by_venue) == set()  # nothing priced


def test_defi_positions_without_scan_instant_raise() -> None:
    with pytest.raises(ValueError, match="defi_as_of"):
        aggregate_portfolio(defi_positions=_defi_positions(), queried_at=_QUERIED_AT)


# --- (c) determinism ----------------------------------------------------------------


def test_same_snapshots_produce_byte_identical_dumps() -> None:
    first = _aggregate()
    second = _aggregate()
    assert first.model_dump() == second.model_dump()
    assert first.model_dump_json() == second.model_dump_json()


def test_price_map_insertion_order_does_not_change_the_output() -> None:
    reversed_prices = dict(reversed(list(_prices().items())))
    first = _aggregate()
    second = _aggregate(prices=reversed_prices)
    assert first.model_dump_json() == second.model_dump_json()
